"""Koordinator: liest die verdrahteten Entities und ruft die Analyse.

Die Aufteilung folgt dem Kontrakt: fein im Abfragetakt in einem Ringpuffer,
grob und dauerhaft über die Langzeitstatistik. Die Langfristpunkte werden
dabei aus den **Quell**-Sensoren rekonstruiert und nicht aus den eigenen —
sonst könnte die Integration erst rechnen, wenn sie schon gerechnet hat.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .analysis import evaluate, hints, presets, thermal
from .analysis.types import (
    BETRIEB_HEIZEN,
    Analyse,
    HinweisZustand,
    Messwert,
    Preset,
    TaktZustand,
)
from .const import (
    ABFRAGE_SEKUNDEN,
    BETRIEBSART_SCHLUESSEL,
    CONF_AUSSENTEMPERATUR,
    CONF_BETRIEBSART,
    CONF_DURCHFLUSS,
    CONF_LEISTUNG,
    CONF_PRESET,
    CONF_RUECKLAUF,
    CONF_STANDBY_W,
    CONF_STEUERUNG_AKTIV,
    CONF_STEUERUNG_GRUND,
    CONF_VERDICHTER,
    CONF_VORLAUF,
    DOMAIN,
    DURCHFLUSS_UMRECHNUNG,
    RINGPUFFER_STUNDEN,
)

_LOGGER = logging.getLogger(__name__)

LEISTUNG_UMRECHNUNG = {"w": 1.0, "kw": 1000.0, "mw": 1_000_000.0, "va": 1.0}
STATISTIK_TAGE = 60
STATISTIK_CACHE = timedelta(hours=6)


class EinheitFehlt(Exception):
    """Eine Entity trägt keine verwertbare Einheit.

    Wird als Konfigurationsfehler gemeldet und nicht geraten: ein
    angenommenes l/min statt l/h verfälscht jeden COP um den Faktor 60.
    """


class WpOptimizationCoordinator(DataUpdateCoordinator[Analyse]):
    """Hält Zustand über Abfragen und Neustarts hinweg."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=ABFRAGE_SEKUNDEN),
        )
        self.entry = entry
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.zaehler")
        self._takt = TaktZustand()
        self._hinweise = HinweisZustand()
        self._preset: Preset | None = None
        # Ringpuffer: (ts, spreizung, laeuft, cop_abweichung, vorlauf, t_aussen)
        self._puffer: deque[tuple[float, float | None, bool, float | None, float | None, float | None]] = deque(
            maxlen=int(RINGPUFFER_STUNDEN * 3600 / ABFRAGE_SEKUNDEN)
        )
        self._starts: deque[float] = deque(maxlen=2000)
        self._statistik: tuple[list, list] = ([], [])
        self._statistik_geholt: datetime | None = None
        self.konfigfehler: list[str] = []

    # --- Start und Persistenz ------------------------------------------

    async def async_vorbereiten(self) -> None:
        """Preset laden und die Zähler aus dem Speicher holen.

        Ein Zähler, der bei jedem Neustart auf null fällt, ist als
        `total_increasing` schlimmer als keiner: die Statistik deutet den
        Rücksprung als neuen Zyklus und addiert ihn dazu.
        """
        verzeichnis = Path(__file__).parent / "presets"
        alle = await self.hass.async_add_executor_job(presets.lade_presets, verzeichnis)
        schluessel = self._opt(CONF_PRESET)
        self._preset = alle.get(schluessel)
        if self._preset is None:
            raise ValueError(f"Preset {schluessel!r} nicht gefunden")

        eigener_standby = float(self._opt(CONF_STANDBY_W) or 0)
        if eigener_standby > 0:
            self._preset = _mit_standby(self._preset, eigener_standby)

        gespeichert = await self._store.async_load()
        if gespeichert:
            self._takt = TaktZustand(
                laeuft=False,  # nach einem Neustart bewusst unbekannt -> aus
                starts=int(gespeichert.get("starts", 0)),
                laufzeit_s=float(gespeichert.get("laufzeit_s", 0.0)),
                letzter_ts=None,
            )

    async def _zaehler_sichern(self) -> None:
        await self._store.async_save(
            {"starts": self._takt.starts, "laufzeit_s": self._takt.laufzeit_s}
        )

    # --- Abfrage -------------------------------------------------------

    async def _async_update_data(self) -> Analyse:
        assert self._preset is not None
        self.konfigfehler = []
        messwert = self._messwert()

        jetzt = dt_util.utcnow()
        if (
            self._statistik_geholt is None
            or jetzt - self._statistik_geholt > STATISTIK_CACHE
        ):
            self._statistik = await self._langfristpunkte(jetzt)
            self._statistik_geholt = jetzt

        vorher = self._takt.starts
        analyse = evaluate.analysiere(
            evaluate.AnalyseEingang(
                messwert=messwert,
                preset=self._preset,
                hinweise=self._hinweise,
                tagesbild=self._tagesbild(messwert),
                verlust_punkte=self._statistik[0],
                kurven_punkte=self._statistik[1],
                takt=self._takt,
            )
        )
        self._takt = analyse.takt
        self._hinweise = analyse.hinweise

        if self._takt.starts != vorher:
            self._starts.append(messwert.ts)
            await self._zaehler_sichern()

        self._puffer.append(
            (
                messwert.ts,
                analyse.spreizung_k,
                self._takt.laeuft,
                analyse.cop_abweichung,
                messwert.vorlauf_c,
                messwert.t_aussen_c,
            )
        )
        return analyse

    # --- Eingänge lesen ------------------------------------------------

    def _opt(self, schluessel: str):
        return self.entry.options.get(schluessel)

    def _zahl(self, schluessel: str, umrechnung: dict[str, float] | None = None):
        """Zahlenwert einer verdrahteten Entity, in der Zieleinheit."""
        eid = self._opt(schluessel)
        if not eid:
            return None
        state = self.hass.states.get(eid)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return None
        try:
            wert = float(state.state)
        except (TypeError, ValueError):
            return None
        if umrechnung is None:
            return wert
        einheit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
        faktor = umrechnung.get(einheit)
        if faktor is None:
            self.konfigfehler.append(
                f"{eid}: Einheit {einheit or 'fehlt'!r} nicht verwertbar"
            )
            return None
        return wert * faktor

    def _temperatur(self, schluessel: str) -> float | None:
        eid = self._opt(schluessel)
        if not eid:
            return None
        state = self.hass.states.get(eid)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return None
        try:
            wert = float(state.state)
        except (TypeError, ValueError):
            return None
        einheit = (state.attributes.get("unit_of_measurement") or "°C").strip()
        if einheit in ("°F", "F"):
            return (wert - 32.0) * 5.0 / 9.0
        return wert

    def _betriebsart(self) -> str | None:
        eid = self._opt(CONF_BETRIEBSART)
        if not eid:
            return None
        state = self.hass.states.get(eid)
        if state is None:
            return None
        roh = str(state.state).strip().lower()
        # climate-Entities tragen die Betriebsart teils im Attribut.
        roh = str(state.attributes.get("hvac_action") or roh).lower()
        for muster, normal in BETRIEBSART_SCHLUESSEL:
            if muster in roh:
                return normal
        return None

    def _messwert(self) -> Messwert:
        grund = self._opt(CONF_STEUERUNG_GRUND)
        grund_state = self.hass.states.get(grund) if grund else None
        aktiv = self._opt(CONF_STEUERUNG_AKTIV)
        aktiv_state = self.hass.states.get(aktiv) if aktiv else None
        return Messwert(
            ts=dt_util.utcnow().timestamp(),
            vorlauf_c=self._temperatur(CONF_VORLAUF),
            ruecklauf_c=self._temperatur(CONF_RUECKLAUF),
            durchfluss_lh=self._zahl(CONF_DURCHFLUSS, DURCHFLUSS_UMRECHNUNG),
            p_el_w=self._zahl(CONF_LEISTUNG, LEISTUNG_UMRECHNUNG),
            t_aussen_c=self._temperatur(CONF_AUSSENTEMPERATUR),
            verdichter_hz=self._zahl(CONF_VERDICHTER),
            betrieb=self._betriebsart(),
            steuerung_aktiv=bool(aktiv_state and aktiv_state.state == "on"),
            steuerung_grund=(
                str(grund_state.state) if grund_state else "normal"
            ),
        )

    # --- Verdichtung ---------------------------------------------------

    def _tagesbild(self, jetzt: Messwert) -> hints.Tagesbild:
        """Kennzahlen über den Ringpuffer verdichten.

        Alles hier ist über Tage gemittelt, nie über einen Zyklus — ein
        Hinweis, der im Abfragetakt kippt, ist Flackern.
        """
        if not self._puffer:
            return hints.Tagesbild()

        laufend = [p for p in self._puffer if p[2]]
        spreizungen = [p[1] for p in laufend if p[1] is not None]
        mittel = sum(spreizungen) / len(spreizungen) if spreizungen else None
        null_anteil = (
            sum(1 for s in spreizungen if abs(s) < 0.05) / len(spreizungen)
            if spreizungen
            else None
        )
        abweichungen = [p[3] for p in self._puffer if p[3] is not None]

        fenster_s = self._puffer[-1][0] - self._puffer[0][0]
        takte = None
        if fenster_s > 3600:
            frisch = [t for t in self._starts if t >= self._puffer[0][0]]
            takte = len(frisch) * 86400.0 / fenster_s

        return hints.Tagesbild(
            spreizung_mittel_k=mittel,
            takte_pro_tag=takte,
            cop_abweichung_prozent=(
                sum(abweichungen) / len(abweichungen) if abweichungen else None
            ),
            anteil_spreizung_null=null_anteil,
        )

    # --- Langzeitstatistik ---------------------------------------------

    async def _langfristpunkte(
        self, jetzt: datetime
    ) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        """Stundenpaare für Wärmeverlust und Heizkurve.

        Rekonstruiert aus den Quell-Sensoren: Vorlauf, Rücklauf und
        Außentemperatur als Stundenmittel, die Wärmeleistung daraus. So hängt
        die Auswertung nicht an den eigenen Sensoren und liefert schon beim
        ersten Lauf nach einer Neuinstallation Ergebnisse, sofern die
        Quell-Historie reicht.
        """
        assert self._preset is not None
        start = jetzt - timedelta(days=STATISTIK_TAGE)
        reihen = {}
        for schluessel in (CONF_VORLAUF, CONF_RUECKLAUF, CONF_AUSSENTEMPERATUR):
            eid = self._opt(schluessel)
            if not eid:
                return [], []
            reihen[schluessel] = await self._stunden(eid, start)

        vl, rl, ta = (
            reihen[CONF_VORLAUF],
            reihen[CONF_RUECKLAUF],
            reihen[CONF_AUSSENTEMPERATUR],
        )
        fluss = self._preset.durchfluss_nominal_lh
        verlust: list[tuple[float, float]] = []
        kurve: list[tuple[float, float]] = []
        for ts in sorted(set(vl) & set(rl) & set(ta)):
            spreiz = vl[ts] - rl[ts]
            if spreiz < self._preset.spreizung_min_gueltig_k:
                continue  # keine Wärmeabgabe in dieser Stunde
            kurve.append((ta[ts], vl[ts]))
            if fluss:
                leistung = thermal.waermeleistung_w(
                    fluss, spreiz, self._preset.waermetraeger_faktor
                )
                if leistung is not None:
                    verlust.append((ta[ts], leistung))
        return verlust, kurve

    async def _stunden(self, stat_id: str, start: datetime) -> dict[float, float]:
        """Stündliche Mittelwerte einer Entity, oder leer."""
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )

            rohdaten = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                None,
                {stat_id},
                "hour",
                None,
                {"mean"},
            )
        except Exception as err:  # Statistik ist optional, nie fatal
            _LOGGER.debug("Statistik für %s nicht verfügbar: %s", stat_id, err)
            return {}
        ergebnis: dict[float, float] = {}
        for zeile in rohdaten.get(stat_id, []):
            ts, mittel = zeile.get("start"), zeile.get("mean")
            if ts is not None and mittel is not None:
                ergebnis[float(ts)] = float(mittel)
        return ergebnis


def _mit_standby(preset: Preset, standby_w: float) -> Preset:
    """Preset mit anlagenspezifischem Standby-Sockel."""
    return replace(preset, standby_w=standby_w)


__all__ = ["WpOptimizationCoordinator", "EinheitFehlt"]
