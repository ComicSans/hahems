"""Auswertelauf: liest die verdrahteten Entities und ruft die Analyse.

Die Aufteilung ist bewusst zweistufig: fein im eigenen Abfragetakt in einem
Ringpuffer, grob und dauerhaft über die Langzeitstatistik. Die Langfristpunkte
werden dabei aus den **Quell**-Sensoren rekonstruiert und nicht aus den
eigenen — sonst könnte die Analyse erst rechnen, wenn sie schon gerechnet hat.

Eigener Timer statt Mitlaufen im HEMS-Koordinator: der rechnet im Minutentakt,
die Startzählung braucht 30 Sekunden. Ein Verdichter, der zwischen zwei
Abfragen anläuft und wieder ausgeht, fehlt sonst in der Zählung für immer, und
`takte` ist als `total_increasing` veröffentlicht — ein verpasster Start ist
nicht nachtragbar.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..models import HeatPumpAnalysis
from .analysis import evaluate, hints, presets, thermal
from .analysis.types import Analyse, HinweisZustand, Messwert, Preset, TaktZustand
from .const import (
    ABFRAGE_SEKUNDEN,
    BETRIEBSART_SCHLUESSEL,
    DURCHFLUSS_UMRECHNUNG,
    GRUND_NORMAL,
    LEISTUNG_UMRECHNUNG,
    RINGPUFFER_STUNDEN,
    STATISTIK_CACHE_STUNDEN,
    STATISTIK_TAGE,
)

_LOGGER = logging.getLogger(__name__)

PRESET_VERZEICHNIS = Path(__file__).parent / "presets"


class AnalyseLauf:
    """Hält Zustand über Abfragen und Neustarts hinweg.

    Nicht als `DataUpdateCoordinator` gebaut: die Entities hängen am
    HEMS-Koordinator und lesen das Ergebnis aus `HemsData`. Ein zweiter
    Koordinator brächte einen zweiten Verfügbarkeitsbegriff für dieselbe
    Integration.
    """

    def __init__(
        self, hass: HomeAssistant, entry_id: str, rolle: HeatPumpAnalysis
    ) -> None:
        self.hass = hass
        self.rolle = rolle
        self.analyse: Analyse | None = None
        self.konfigfehler: list[str] = []

        self._store = Store(hass, 1, f"hems.{entry_id}.{rolle.id}.wp_zaehler")
        self._takt = TaktZustand()
        self._hinweise = HinweisZustand()
        self._preset: Preset | None = None
        # Ringpuffer: (ts, spreizung, laeuft, cop_abweichung, vorlauf, t_aussen)
        self._puffer: deque[
            tuple[float, float | None, bool, float | None, float | None, float | None]
        ] = deque(maxlen=int(RINGPUFFER_STUNDEN * 3600 / ABFRAGE_SEKUNDEN))
        self._starts: deque[float] = deque(maxlen=2000)
        self._statistik: tuple[list, list] = ([], [])
        self._statistik_geholt: datetime | None = None
        self._abmelden: CALLBACK_TYPE | None = None
        # Von HEMS gesetzt, bevor gerechnet wird: greift der Energiemanager
        # gerade selbst in die Wärmepumpe ein? Das ist kein normaler Betrieb
        # und darf die Erwartungsbasis nicht prägen.
        self.steuerung_aktiv = False
        self.steuerung_grund = GRUND_NORMAL

    # --- Start und Ende -------------------------------------------------

    async def async_start(self) -> None:
        """Preset laden, Zähler zurückholen, Timer anwerfen."""
        alle = await self.hass.async_add_executor_job(
            presets.lade_presets, PRESET_VERZEICHNIS
        )
        self._preset = alle.get(self.rolle.preset)
        if self._preset is None:
            self.konfigfehler.append(
                f"Preset {self.rolle.preset!r} unbekannt — Analyse bleibt aus"
            )
            _LOGGER.warning("Preset %r nicht gefunden", self.rolle.preset)
            return

        if self.rolle.standby_w > 0:
            self._preset = replace(self._preset, standby_w=self.rolle.standby_w)

        # Ein Zähler, der bei jedem Neustart auf null fällt, ist als
        # `total_increasing` schlimmer als keiner: die Statistik deutet den
        # Rücksprung als neuen Zyklus und addiert ihn dazu.
        gespeichert = await self._store.async_load()
        if gespeichert:
            self._takt = TaktZustand(
                laeuft=False,  # nach einem Neustart bewusst unbekannt -> aus
                starts=int(gespeichert.get("starts", 0)),
                laufzeit_s=float(gespeichert.get("laufzeit_s", 0.0)),
                letzter_ts=None,
            )

        self._abmelden = async_track_time_interval(
            self.hass, self._tick, timedelta(seconds=ABFRAGE_SEKUNDEN)
        )
        await self._tick(dt_util.utcnow())

    def async_stop(self) -> None:
        if self._abmelden is not None:
            self._abmelden()
            self._abmelden = None

    # --- Abfrage ---------------------------------------------------------

    async def _tick(self, _jetzt) -> None:
        try:
            await self._auswerten()
        except Exception:  # eine Analyse darf HEMS nie mitreißen
            _LOGGER.exception("Wärmepumpen-Analyse fehlgeschlagen")

    async def _auswerten(self) -> None:
        if self._preset is None:
            return
        self.konfigfehler = []
        messwert = self._messwert()

        jetzt = dt_util.utcnow()
        if self._statistik_geholt is None or jetzt - self._statistik_geholt > timedelta(
            hours=STATISTIK_CACHE_STUNDEN
        ):
            self._statistik = await self._langfristpunkte(jetzt)
            self._statistik_geholt = jetzt

        vorher = self._takt.starts
        analyse = evaluate.analysiere(
            evaluate.AnalyseEingang(
                messwert=messwert,
                preset=self._preset,
                hinweise=self._hinweise,
                tagesbild=self._tagesbild(),
                verlust_punkte=self._statistik[0],
                kurven_punkte=self._statistik[1],
                takt=self._takt,
            )
        )
        self._takt = analyse.takt
        self._hinweise = analyse.hinweise

        if self._takt.starts != vorher:
            self._starts.append(messwert.ts)
            await self._store.async_save(
                {"starts": self._takt.starts, "laufzeit_s": self._takt.laufzeit_s}
            )

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
        self.analyse = analyse

    # --- Eingänge lesen ---------------------------------------------------

    def _zahl(self, eid: str | None, umrechnung: dict[str, float] | None = None):
        """Zahlenwert einer verdrahteten Entity, in der Zieleinheit."""
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

    def _temperatur(self, eid: str | None) -> float | None:
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
        eid = self.rolle.betriebsart
        if not eid:
            return None
        state = self.hass.states.get(eid)
        if state is None:
            return None
        # climate-Entities tragen die Betriebsart teils im Attribut.
        roh = str(
            state.attributes.get("hvac_action") or str(state.state).strip()
        ).lower()
        for muster, normal in BETRIEBSART_SCHLUESSEL:
            if muster in roh:
                return normal
        return None

    def _messwert(self) -> Messwert:
        r = self.rolle
        return Messwert(
            ts=dt_util.utcnow().timestamp(),
            vorlauf_c=self._temperatur(r.vorlauf_temp),
            ruecklauf_c=self._temperatur(r.ruecklauf_temp),
            durchfluss_lh=self._zahl(r.durchfluss, DURCHFLUSS_UMRECHNUNG),
            p_el_w=self._zahl(r.leistung_elektrisch, LEISTUNG_UMRECHNUNG),
            t_aussen_c=self._temperatur(r.aussentemperatur),
            verdichter_hz=self._zahl(r.verdichter_frequenz),
            betrieb=self._betriebsart(),
            # Seit der Zusammenführung weiß HEMS das über sich selbst, statt
            # es aus einer verdrahteten Entity zu lesen.
            steuerung_aktiv=self.steuerung_aktiv,
            steuerung_grund=self.steuerung_grund,
        )

    # --- Verdichtung ------------------------------------------------------

    def _tagesbild(self) -> hints.Tagesbild:
        """Kennzahlen über den Ringpuffer verdichten.

        Alles hier ist über Tage gemittelt, nie über einen Zyklus — ein
        Hinweis, der im Abfragetakt kippt, ist Flackern statt Hinweis.
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

    # --- Langzeitstatistik -------------------------------------------------

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
        if self._preset is None:
            return [], []
        start = jetzt - timedelta(days=STATISTIK_TAGE)
        r = self.rolle
        reihen = {}
        for name, eid in (
            ("vorlauf", r.vorlauf_temp),
            ("ruecklauf", r.ruecklauf_temp),
            ("aussen", r.aussentemperatur),
        ):
            if not eid:
                return [], []
            reihen[name] = await self._stunden(eid, start)

        vl, rl, ta = reihen["vorlauf"], reihen["ruecklauf"], reihen["aussen"]
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
