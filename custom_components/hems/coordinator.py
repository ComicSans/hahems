"""Koordinator: liest Ist-Werte und Prognosen, ruft den Planner auf."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
)
from homeassistant.helpers.sun import get_astral_event_date, get_astral_event_next
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .actuator import Actuator
from .changelog import ChangeLog, decision_snapshot, diff_snapshots
from .config_check import ConfigCheck, check_config
from .const import (
    ALERT_CHANNELS,
    CONF_BASELINE_W,
    CONF_DEVICES,
    CONF_FREE_H,
    CONF_FREE_KWH,
    CONF_INVERT,
    CONF_INVERT_PV,
    CONF_METER,
    CONF_NIGHT_W,
    CONF_PRIORITY_MODE,
    CONF_PV_MINUS_BATTERY,
    CONF_PV_POWER,
    CONF_WEATHER,
    DEFAULT_BASELINE_W,
    DEFAULT_FREE_H,
    DEFAULT_FREE_KWH,
    DEFAULT_GAIN_LEVEL,
    DEFAULT_NIGHT_W,
    DOMAIN,
    EV_DEMAND_FLOOR_W,
    EV_DEMAND_GRACE_S,
    EV_EMPTY_COOLDOWN_S,
    GOAL_SELF_CONSUMPTION,
    MODE_AUTO,
    MODE_OBSERVE,
    PRIORITY_AUTO,
    SALDO_JUMP_COOLDOWN_S,
    SALDO_JUMP_W,
    SWITCH_LEARN_FLOOR_HEAT_W,
    SWITCH_LEARN_FLOOR_W,
    WEATHER_CONDITION_FACTORS,
)
from . import entity_domain
from .models import DeviceRegistry, HeatingSystem, parse_devices
from .planner import (
    block_windows,
    compute_plan,
    parse_weekday,
    profile_rows,
    weekly_windows,
)
from .power_memory import PowerMemory
from .strategies.alerts import evaluate as evaluate_alerts
from .strategies.switchable import lern_leistung
from .strategies.types import (
    HeatingState,
    ModulatedState,
    PlanFlags,
    PlanInput,
    PlanResult,
    StorageState,
    SwitchableState,
)

_LOGGER = logging.getLogger(__name__)

STATS_CACHE = timedelta(hours=6)
WEATHER_CACHE = timedelta(minutes=30)
NIGHT_HOURS_LOCAL = (22, 23, 0, 1, 2, 3, 4, 5)

# Volles 24-h-Lastprofil aus dem rekonstruierten Hausverbrauch
PROFILE_DAYS = timedelta(days=28)
MIN_PROFILE_SAMPLES = 2  # Mindest-Beobachtungen je (Tagtyp, Stunde)-Bucket
MIN_PROFILE_BUCKETS = 6  # darunter gilt das Profil als zu dünn, Fallback greift

# Umrechnung nach W bzw. kWh; Sensoren ohne Einheit werden als W/kWh gelesen.
POWER_UNITS = {"w": 1.0, "kw": 1000.0, "mw": 1_000_000.0}
ENERGY_UNITS = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}


def _state_power_w(state) -> float | None:
    """Leistung eines State-Objekts in W lesen (kW/MW umrechnen).

    Nimmt bewusst ein `State`-Objekt statt einer entity_id entgegen — anders
    als `HemsCoordinator._power_w` (das immer den AKTUELLEN Zustand holt)
    muss die Sprung-Erkennung auch den alten Zustand aus dem Event auswerten
    können. Ohne Warn-Logging (das übernimmt der reguläre Update-Zyklus).
    """
    if state is None:
        return None
    try:
        val = float(state.state)
    except (TypeError, ValueError):
        return None
    unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
    if unit in ENERGY_UNITS:
        return None
    return val * POWER_UNITS.get(unit, 1.0)


class LoadModelLearner:
    """Lernt Nacht-Grundlast und 24-h-Lastprofil aus der Statistik-Historie.

    Aus `HemsCoordinator` herausgelöst (siehe docs/architektur-review.md) —
    reine Kapselung derselben Logik, keine Verhaltensänderung. Bekommt seine
    HA-Zugriffe (`hass`, Options-Lookup, Device-Registry, eigene Entity-IDs)
    als schmale Abhängigkeiten statt des ganzen Coordinators.
    """

    def __init__(self, hass, opt, registry, own_entity_id) -> None:
        self._hass = hass
        self._opt = opt
        self._registry = registry
        self._own_entity_id = own_entity_id
        self.night_load_w: float | None = None
        self._night_load_fetched: datetime | None = None
        # (Tagtyp, UTC-Stunde) → mittlere Last in W; Tagtyp 0 = Werktag, 1 = Wochenende
        self.load_profile: dict[tuple[int, int], float] | None = None
        self.profile_source: str = "konstante"

    async def refresh(self) -> float:
        """Lastmodell lernen: Nacht-Grundlast (Skalar) und 24-h-Lastprofil.

        Primärquelle für das Profil ist der bereits rekonstruierte Haus-
        verbrauch (`lastfluss`-Sensor, PV- und akkukompensiert) — damit
        bekommen auch die Tagesstunden ein echtes Profil, nicht nur die
        Nacht. Fehlt dessen Historie (frische Installation), greift das
        Nacht-Profil aus dem rohen Zähler, zuletzt der konfigurierte
        Konstantwert. Rückgabe ist die Nacht-Grundlast als Fallback-Skalar.

        Wärmeerzeuger stecken implizit im Profil: Sie werden weder getrennt
        modelliert noch herausgerechnet. Das Profil schreibt ihren Verbrauch
        damit wetterblind aus dem 28-Tage-Mittel fort.
        """
        fallback = float(self._opt(CONF_NIGHT_W, DEFAULT_NIGHT_W))
        now = dt_util.utcnow()
        if (
            self.night_load_w is not None
            and self._night_load_fetched is not None
            and now - self._night_load_fetched < STATS_CACHE
        ):
            return self.night_load_w

        night_scalar, night_profile = await self._meter_night_stats(now)
        house_profile = await self._house_load_profile(now)

        if house_profile:
            self.load_profile = house_profile
            self.profile_source = "hausverbrauch (24 h)"
        elif night_profile:
            self.load_profile = night_profile
            self.profile_source = "zähler-nacht"
        else:
            self.load_profile = None
            self.profile_source = "konstante"

        self.night_load_w = (
            night_scalar if night_scalar and night_scalar > 0 else fallback
        )
        self._night_load_fetched = now
        return self.night_load_w

    async def _statistics_hourly_mean(
        self, stat_id: str, start: datetime
    ) -> list[dict] | None:
        """Stündliche Mittelwert-Statistik ab `start` lesen (oder None)."""
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )

            stats = await get_instance(self._hass).async_add_executor_job(
                statistics_during_period,
                self._hass,
                start,
                None,
                {stat_id},
                "hour",
                None,
                {"mean"},
            )
        except Exception as err:  # Statistik ist optional, nie fatal
            _LOGGER.debug("Statistik für %s nicht verfügbar: %s", stat_id, err)
            return None
        return stats.get(stat_id, [])

    async def _meter_night_stats(
        self, now: datetime
    ) -> tuple[float | None, dict[tuple[int, int], float] | None]:
        """Nachtlast aus dem rohen Zähler (14 Tage) als Fallback lernen.

        Nur Nachtstunden, weil tagsüber PV den Zählerwert verfälscht. Liefert
        den Skalar-Mittelwert und ein Nacht-Profil, das auf beide Wochentag-
        typen gespiegelt wird — es dient nur, bis der Hausverbrauch genug
        Historie für ein volles 24-h-Profil hat.
        """
        meter = self._opt(CONF_METER, None)
        if not meter:
            return None, None
        rows = await self._statistics_hourly_mean(meter, now - timedelta(days=14))
        if not rows:
            return None, None

        by_hour: dict[int, list[float]] = {}
        for row in rows:
            ts, mean = row.get("start"), row.get("mean")
            if ts is None or mean is None:
                continue
            utc = dt_util.utc_from_timestamp(ts)
            if dt_util.as_local(utc).hour in NIGHT_HOURS_LOCAL:
                # Nur Bezug zählt; ein evtl. gedeckelter Zähler liefert eh >= 0
                by_hour.setdefault(utc.hour, []).append(max(0.0, float(mean)))
        if not by_hour:
            return None, None

        all_vals = [v for vals in by_hour.values() for v in vals]
        scalar = sum(all_vals) / len(all_vals)
        profile: dict[tuple[int, int], float] = {}
        for hour, vals in by_hour.items():
            watt = round(sum(vals) / len(vals), 1)
            profile[(0, hour)] = watt  # Werktag
            profile[(1, hour)] = watt  # Wochenende (mangels Daten identisch)
        return scalar, profile

    async def _house_load_profile(
        self, now: datetime
    ) -> dict[tuple[int, int], float] | None:
        """Volles 24-h-Lastprofil aus dem rekonstruierten Hausverbrauch lernen.

        Quelle ist der integrationseigene `lastfluss`-Sensor (state_class
        measurement → Langzeitstatistik). Gebündelt nach Wochentagstyp
        (Werktag/Wochenende) und UTC-Stunde über `PROFILE_DAYS`. Buckets mit
        zu wenigen Beobachtungen werden verworfen; ist das Profil insgesamt zu
        dünn, greift der Aufrufer auf das Nacht-Profil zurück.
        """
        stat_id = self._own_entity_id("lastfluss")
        if not stat_id:
            return None
        rows = await self._statistics_hourly_mean(stat_id, now - PROFILE_DAYS)
        if not rows:
            return None

        buckets: dict[tuple[int, int], list[float]] = {}
        for row in rows:
            ts, mean = row.get("start"), row.get("mean")
            if ts is None or mean is None:
                continue
            utc = dt_util.utc_from_timestamp(ts)
            daytype = 1 if utc.weekday() >= 5 else 0
            buckets.setdefault((daytype, utc.hour), []).append(max(0.0, float(mean)))

        profile = {
            key: round(sum(vals) / len(vals), 1)
            for key, vals in buckets.items()
            if len(vals) >= MIN_PROFILE_SAMPLES
        }
        return profile if len(profile) >= MIN_PROFILE_BUCKETS else None


class WeatherClient:
    """Wetterlage/PV-Ertragsfaktor für morgen und stündliche Temperatur-
    vorhersage, mit eigenem Cache (`WEATHER_CACHE`).

    Aus `HemsCoordinator` herausgelöst (siehe docs/architektur-review.md) —
    reine Kapselung derselben Logik, keine Verhaltensänderung.
    """

    def __init__(self, hass, opt) -> None:
        self._hass = hass
        self._opt = opt
        self._weather_cache: tuple[str | None, float | None] = (None, None)
        self._weather_fetched: datetime | None = None

    def outdoor_c(self) -> float | None:
        """Aktuelle Außentemperatur aus der Wetter-Entität.

        Jede `weather`-Entität trägt sie als Attribut `temperature`. Kein Cache
        und kein Service-Aufruf nötig — anders als die Vorhersage steht sie
        direkt im Zustand. Ohne konfigurierte Wetter-Entität (oder ohne das
        Attribut) `None`; die Heizung fällt dann auf ihren eigenen
        Temperatursensor zurück, und ohne beides regelt sie nicht.
        """
        entity = self._opt(CONF_WEATHER, None)
        if not entity:
            return None
        state = self._hass.states.get(entity)
        if state is None:
            return None
        try:
            return float(state.attributes.get("temperature"))
        except (TypeError, ValueError):
            return None

    async def tomorrow(self) -> tuple[str | None, float | None]:
        """Wetterlage und PV-Ertragsfaktor (0–1) für morgen bestimmen.

        Bevorzugt den Bewölkungsgrad der Tagesvorhersage; fehlt er, wird
        die Wetterlage (condition) über eine feste Tabelle abgebildet.
        """
        entity = self._opt(CONF_WEATHER, None)
        if not entity:
            return None, None
        now = dt_util.utcnow()
        if (
            self._weather_fetched is not None
            and now - self._weather_fetched < WEATHER_CACHE
        ):
            return self._weather_cache

        condition: str | None = None
        factor: float | None = None
        try:
            resp = await self._hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": entity, "type": "daily"},
                blocking=True,
                return_response=True,
            )
            forecast = (resp or {}).get(entity, {}).get("forecast", [])
            tomorrow = dt_util.now().date() + timedelta(days=1)
            for item in forecast:
                when = dt_util.parse_datetime(item.get("datetime") or "")
                if when is None or dt_util.as_local(when).date() != tomorrow:
                    continue
                condition = item.get("condition")
                cloud = item.get("cloud_coverage")
                if cloud is not None:
                    # Voll bedeckt liefert diffus noch ~15 % des klaren Ertrags
                    factor = round(max(0.0, 1 - 0.85 * float(cloud) / 100), 2)
                elif condition in WEATHER_CONDITION_FACTORS:
                    factor = WEATHER_CONDITION_FACTORS[condition]
                break
        except Exception as err:  # Wetter ist optional, nie fatal
            _LOGGER.debug("Wettervorhersage nicht verfügbar: %s", err)

        self._weather_cache = (condition, factor)
        self._weather_fetched = now
        return self._weather_cache


class HemsData:
    """Ergebnis eines Update-Zyklus."""

    def __init__(self) -> None:
        self.pv_today_kwh: float = 0.0
        self.pv_remaining_kwh: float = 0.0
        self.pv_tomorrow_kwh: float = 0.0
        self.pv_power_now_w: float | None = None
        self.pv_power_estimated: bool = False
        self.wetter_morgen: str | None = None
        self.wetter_faktor_morgen: float | None = None
        self.saldo_w: float | None = None
        self.batterie_w: float | None = None  # positiv = Entladen ins Haus
        self.waermepumpe_w: float | None = None
        self.wallbox_w: float | None = None
        self.haus_w: float | None = None
        self.lastprofil_quelle: str = ""
        self.lastprofil: list[dict] = []
        # Eigene Entity-IDs, aus denen die Plankarte den gemessenen Verlauf
        # des laufenden Tages nachlädt (Slugs sind instanzabhängig).
        self.verlauf_pv_entity: str | None = None
        self.verlauf_soc_entity: str | None = None
        # Laufzeit-Steuerung (aus Select/Switch), fürs Dashboard mitgeführt.
        self.ziel: str = GOAL_SELF_CONSUMPTION
        self.ev_zwang: bool = False
        self.config_check: ConfigCheck | None = None
        self.plan: PlanResult = PlanResult()
        # Pro-Speicher-Momentaufnahme für die Lastfluss-Karte
        # (Name, SoC %, Ist-Leistung W, Kapazität kWh).
        self.speicher_liste: list[dict] = []
        # Pro-Schaltlast-Momentaufnahme für die Lastfluss-Karte (Name,
        # Priorität, An/Aus, Ist- und erwartete Leistung, Begründung).
        self.schaltlasten: list[dict] = []
        # Pro-Heizung-Momentaufnahme für den Heizungs-Reiter des Panels:
        # Außentemperatur, Status (Frostschutz/Sommersperre/…), Vorlauf-Soll
        # und -Ist sowie die eingestellte Kurve.
        self.heizungen: list[dict] = []


class HemsCoordinator(DataUpdateCoordinator[HemsData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=60),
        )
        self.entry = entry
        self.mode: str = MODE_OBSERVE
        # Vorheriger Modus, um den Übergang auto→(beobachten|aus) zu erkennen
        # und den Akku genau einmal freizugeben.
        self._prev_mode: str = MODE_OBSERVE
        # Signatur des letzten Config-Checks, damit nur bei Änderung geloggt wird.
        self._check_signature: tuple | None = None
        # Signatur der zuletzt zugestellten Meldungen (verhindert Zyklus-Lärm).
        self._alert_signature: tuple | None = None
        # Optimierungsziel und E-Auto-Zwangsladung (von Select bzw. Switch
        # gesetzt, in RestoreEntity persistiert).
        self.goal: str = GOAL_SELF_CONSUMPTION
        self.ev_force: bool = False
        # Regel-Aggressivität (min/normal/max), vom Select gesetzt und in
        # RestoreEntity persistiert. Default aggressiv, damit Ladelücken zügig
        # geschlossen werden.
        self.gain_level: str = DEFAULT_GAIN_LEVEL
        # Änderungs-Log der Entscheidungen (vom Setup gesetzt) und die
        # Momentaufnahme des Vorlaufs, gegen die diffed wird.
        self.changelog: ChangeLog | None = None
        self._decisions: dict | None = None
        # Schalt-Ebene (nur im Auto-Modus aktiv).
        self._actuator = Actuator(hass)
        # Lastprofil-Lernen und Wetter-Fetch sind eigene Collaborators (siehe
        # docs/architektur-review.md) statt Methoden direkt auf dem Coordinator.
        self._load_model = LoadModelLearner(
            hass, self._opt, lambda: self.registry, self._own_entity_id
        )
        self._weather = WeatherClient(hass, self._opt)
        # Zuletzt gesehene Betriebsart je Wärmeerzeuger (id → "heizen"/"kuehlen"/
        # "fremd"). Überbrückt das Abschalten, in dem die climate-Entität nur
        # noch `off` sagt — siehe _betriebsart.
        self._letzte_betriebsart: dict[str, str] = {}
        self._unit_warned: set[str] = set()
        # Cooldown-Zeitpunkt für die Sprung-Erkennung (async_setup_saldo_jump_tracking).
        self._last_jump_refresh: datetime | None = None
        # Hysterese-Zustand des Planners, über die Update-Zyklen fortgeschrieben.
        self._plan_flags = PlanFlags()
        # Fairness-Akkumulator für die Lastrotation: geladene Energie je Last
        # (kWh) am laufenden lokalen Kalendertag, aus der gemessenen Leistung
        # integriert. Reset um Mitternacht. Bewusst „dumm" — jede Entscheidung
        # trifft der reine Planner, hier wird nur gemessen und gezählt.
        self._mod_energy_kwh: dict[str, float] = {}
        self._mod_energy_day = None  # date, an dem der Akkumulator gilt
        self._mod_energy_ts: datetime | None = None  # letzter Integrationszeit
        # Rotations-Cooldown je Last: Zeitpunkt, bis zu dem eine beobachtet-leere
        # Last in der Rangfolge hinten steht (Name → Ablauf-UTC).
        self._mod_leer_bis: dict[str, datetime] = {}
        # Gelernte erwartete Leistung schaltbarer Lasten (id → letzter An-Wert),
        # über Neustarts hinweg persistiert (vom Setup gesetzt).
        self.power_memory: PowerMemory | None = None

    # -- Konfiguration -----------------------------------------------------

    def _opt(self, key: str, default):
        return self.entry.options.get(key, self.entry.data.get(key, default))

    @property
    def registry(self) -> DeviceRegistry:
        return parse_devices(self.entry.options.get(CONF_DEVICES, []))

    def _deliver_alerts(self, config_errors: list[str]) -> None:
        """Meldungen bewerten und über ihre Kanäle abgleichen. Reconcile über
        die volle Kandidatenmenge (aktiv → anlegen, inaktiv → löschen), damit es
        ohne persistierten Zustand restart-fest bleibt."""
        result = evaluate_alerts(config_errors)
        # Nur bei Änderung zustellen: eine daueraktive Meldung soll nicht jede
        # Minute die Notification neu hochziehen, und eine weggeklickte Meldung
        # nicht sofort zurückkommen. Nach Neustart ist die Signatur leer, der
        # erste Zyklus reconcilet die volle Menge.
        sig = tuple(
            (a.key, a.active, tuple(sorted(a.placeholders.items())))
            for a in result.alerts
        )
        if sig == self._alert_signature:
            return
        self._alert_signature = sig
        for a in result.alerts:
            channels = ALERT_CHANNELS.get(a.severity, ())
            slug = f"{DOMAIN}_{a.key.replace(':', '_')}"
            if a.active:
                if "repair" in channels:
                    ir.async_create_issue(
                        self.hass,
                        DOMAIN,
                        slug,
                        is_fixable=False,
                        severity=ir.IssueSeverity.ERROR,
                        translation_key=a.translation_key,
                        translation_placeholders=a.placeholders,
                    )
                if "notify" in channels:
                    persistent_notification.async_create(
                        self.hass, a.message, title=a.title, notification_id=slug
                    )
            else:
                if "repair" in channels:
                    ir.async_delete_issue(self.hass, DOMAIN, slug)
                if "notify" in channels:
                    persistent_notification.async_dismiss(self.hass, slug)

    def _tracked_entities(self) -> set[str]:
        """Alle Quell-Entitäten, deren Verfügbarwerden eine Neurechnung auslöst."""
        reg = self.registry
        ids: set[str] = set()
        for key in (CONF_METER, CONF_PV_POWER, CONF_WEATHER):
            if entity := self._opt(key, None):
                ids.add(entity)
        for device in self.entry.options.get(CONF_DEVICES, []):
            if entity := device.get("power_now"):
                ids.add(entity)
        for f in reg.forecasts:
            ids.update(
                e for e in (f.energy_today, f.energy_remaining, f.energy_tomorrow) if e
            )
        for s in reg.storages:
            ids.update(e for e in (s.soc_entity, s.power_entity) if e)
        for t in reg.thermals:
            if t.temp_entity:
                ids.add(t.temp_entity)
        for s in reg.switchables:
            ids.update(e for e in (s.switch_entity, s.power_entity) if e)
        for m in reg.modulateds:
            ids.update(e for e in (m.current_entity, m.switch_entity, m.power_entity) if e)
        return ids

    @callback
    def async_setup_source_tracking(self) -> None:
        """Sofort neu rechnen, sobald eine Quelle verfügbar wird.

        Nach einem Neustart sind die Quell-Entitäten (Zähler, Speicher-SoC,
        Prognose, Wetter …) oft noch nicht bereit; der 60-s-Poll würde die
        Karten bis zu eine Minute leer lassen. Wir hören daher auf den
        Übergang „nicht bereit → bereit" und stoßen dann eine (entprellte)
        Neuberechnung an. Reine Wertänderungen laufender Sensoren lösen bewusst
        nichts aus — dafür genügt der reguläre Poll.
        """
        entities = self._tracked_entities()
        if not entities:
            return

        @callback
        def _source_became_ready(event: Event[EventStateChangedData]) -> None:
            new = event.data["new_state"]
            if new is None or new.state in ("unknown", "unavailable"):
                return
            old = event.data["old_state"]
            if old is not None and old.state not in ("unknown", "unavailable"):
                return  # nur das Verfügbarwerden zählt, keine Wertänderung
            self.hass.async_create_task(self.async_request_refresh())

        self.entry.async_on_unload(
            async_track_state_change_event(
                self.hass, list(entities), _source_became_ready
            )
        )

    @callback
    def async_setup_saldo_jump_tracking(self) -> None:
        """Sofort neu rechnen, wenn der Netzsaldo sprunghaft springt.

        Der reguläre 60-s-Takt lässt den Speicher im schlechtesten Fall fast
        eine Minute am alten Sollwert hängen, wenn PV oder Last sich abrupt
        ändern (Wolkenkante, große Last an/aus) — sichtbar als kurzer
        Bezugs-/Einspeise-Spike (Aktuierungs-Totzeit, siehe const.py). Ein
        Sprung über `SALDO_JUMP_W` löst hier eine sofortige Neuberechnung
        aus, statt auf den nächsten Poll zu warten. `SALDO_JUMP_COOLDOWN_S`
        verhindert Update-Stürme bei einer Serie kleiner Sprünge (z. B.
        flackernde Wolkenkante) — kein Wolkenradar, nur schnellere Reaktion
        auf den bereits eingetretenen Sprung.
        """
        meter = self._opt(CONF_METER, None)
        if not meter:
            return

        @callback
        def _saldo_jumped(event: Event[EventStateChangedData]) -> None:
            old_w = _state_power_w(event.data["old_state"])
            new_w = _state_power_w(event.data["new_state"])
            if old_w is None or new_w is None:
                return
            if abs(new_w - old_w) < SALDO_JUMP_W:
                return
            now = dt_util.utcnow()
            if (
                self._last_jump_refresh is not None
                and (now - self._last_jump_refresh).total_seconds()
                < SALDO_JUMP_COOLDOWN_S
            ):
                return
            self._last_jump_refresh = now
            self.hass.async_create_task(self.async_request_refresh())

        self.entry.async_on_unload(
            async_track_state_change_event(self.hass, [meter], _saldo_jumped)
        )

    # -- Helfer ------------------------------------------------------------

    def _state(self, entity_id: str | None):
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        return state

    def _num(self, entity_id: str | None) -> float | None:
        state = self._state(entity_id)
        if state is None:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _is_on(self, entity_id: str | None) -> bool:
        """Ein/Aus-Rückmeldung als bool. Nicht konfiguriert, nicht vorhanden
        oder unavailable/unknown → False: eine optionale Rückmeldung, die nicht
        antwortet, darf nichts auslösen (fail open)."""
        state = self._state(entity_id)
        return state is not None and state.state == "on"

    def _is_on_or_none(self, entity_id: str | None) -> bool | None:
        """Wie `_is_on`, aber eine ausgefallene Rückmeldung bleibt unbekannt.

        Für Signale, aus denen HEMS Flanken zählt: Das Modbus-Gateway fällt
        regelmäßig für Sekunden aus, und `_is_on` würde daraus erst eine
        Abschalt- und dann eine Einschaltflanke machen — also Verdichterstarts
        erfinden, die es nie gab.
        """
        state = self._state(entity_id)
        return None if state is None else state.state == "on"

    def _warn_unit(self, entity_id: str, unit: str, expected: str) -> None:
        if entity_id in self._unit_warned:
            return
        self._unit_warned.add(entity_id)
        _LOGGER.warning(
            "%s liefert '%s', erwartet wird %s — Wert wird ignoriert. "
            "Bitte die Entität in der HEMS-Konfiguration korrigieren.",
            entity_id,
            unit,
            expected,
        )

    def _power_w(self, entity_id: str | None) -> float | None:
        """Leistung in W lesen; kW/MW umrechnen, Energie-Entitäten ablehnen."""
        state = self._state(entity_id)
        if state is None:
            return None
        try:
            val = float(state.state)
        except ValueError:
            return None
        unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit in ENERGY_UNITS:
            self._warn_unit(entity_id, unit, "Leistung (W)")
            return None
        return val * POWER_UNITS.get(unit, 1.0)

    def _energy_kwh(self, entity_id: str | None) -> float | None:
        """Energie in kWh lesen; Wh/MWh umrechnen, Leistungs-Entitäten ablehnen."""
        state = self._state(entity_id)
        if state is None:
            return None
        try:
            val = float(state.state)
        except ValueError:
            return None
        unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit in POWER_UNITS:
            self._warn_unit(entity_id, unit, "Energie (kWh)")
            return None
        return val * ENERGY_UNITS.get(unit, 1.0)

    def _sum_energy(self, entity_ids: list[str | None]) -> float:
        return sum(v for e in entity_ids if (v := self._energy_kwh(e)) is not None)

    def _sum_power(self, entity_ids: list[str | None]) -> float | None:
        """Summe in W; None statt 0, wenn kein einziger Wert verfügbar ist."""
        vals = [v for e in entity_ids if e and (v := self._power_w(e)) is not None]
        return round(sum(vals), 0) if vals else None

    def _modulated_states(self, reg: DeviceRegistry, now: datetime) -> list:
        """Laufzeit-Zustand aller modulierbaren Lasten für die Überschuss-
        regelung. Bewusst „dumm": misst Ist-Leistung, integriert die geladene
        Tagesenergie (Fairness-Schlüssel, Reset lokal um Mitternacht) und leitet
        Schaltlage/Anlaufzeit/Nachfrage ab — jede Entscheidung trifft der reine
        Planner aus diesen Werten."""
        today = dt_util.now().date()
        if self._mod_energy_day != today:
            self._mod_energy_day = today
            self._mod_energy_kwh = {}
            self._mod_energy_ts = None
        dt_h = 0.0
        if self._mod_energy_ts is not None:
            dt_h = max(0.0, (now - self._mod_energy_ts).total_seconds() / 3600.0)
        self._mod_energy_ts = now

        states = []
        for m in reg.modulateds:
            key = m.id  # eindeutiger Join-Schlüssel (nicht der editierbare Name)
            power = self._power_w(m.power_entity)
            # Nur echten Bezug (positiv) integrieren; ein Vorzeichen-Ausreißer
            # oder fehlender Wert lässt den Zähler stehen.
            if power is not None and power > 0 and dt_h > 0:
                self._mod_energy_kwh[key] = (
                    self._mod_energy_kwh.get(key, 0.0) + power * dt_h / 1000.0
                )
            if m.switch_entity:
                s = self.hass.states.get(m.switch_entity)
                ist_an = bool(s and s.state == "on")
                an_seit_s = (
                    (now - s.last_changed).total_seconds()
                    if s is not None and ist_an
                    else None
                )
                aus_seit_s = (
                    (now - s.last_changed).total_seconds()
                    if s is not None and not ist_an
                    else None
                )
            else:
                # Ohne Schalter gibt es keinen von HEMS geschützten Schütz; die
                # Last gilt als an, sobald sie Leistung zieht (keine min_on-/
                # min_off-Sperre, da HEMS sie nicht schaltet).
                ist_an = bool(power and power > 0)
                an_seit_s = None
                aus_seit_s = None
            nachfrage = bool(power is not None and power > EV_DEMAND_FLOOR_W)

            # Leer-Cooldown: an und nach der Anlaufzeit ohne nennenswerte
            # Leistung → als leer merken (nach hinten in der Rotation). Zieht die
            # Last wieder, entfällt der Cooldown sofort. Neu bewaffnet wird erst
            # nach Ablauf, damit eine leere Last nur einmal pro Cooldown kurz
            # geprüft wird.
            observed_empty = (
                ist_an
                and an_seit_s is not None
                and an_seit_s > EV_DEMAND_GRACE_S
                and (power or 0.0) < EV_DEMAND_FLOOR_W
            )
            end = self._mod_leer_bis.get(key)
            cooling = end is not None and now < end
            if nachfrage:
                self._mod_leer_bis.pop(key, None)
                leer = False
            elif observed_empty and not cooling:
                self._mod_leer_bis[key] = now + timedelta(
                    seconds=EV_EMPTY_COOLDOWN_S
                )
                leer = True
            else:
                leer = cooling

            states.append(
                ModulatedState(
                    name=m.name,
                    id=m.id,
                    min_a=m.min_a,
                    phases=m.phases,
                    max_a=m.max_a,
                    priority=m.priority,
                    min_on_min=m.min_on_min,
                    min_off_min=m.min_off_min,
                    hat_schalter=bool(m.switch_entity),
                    power_w=power,
                    energie_heute_kwh=round(self._mod_energy_kwh.get(key, 0.0), 3),
                    ist_an=ist_an,
                    an_seit_s=an_seit_s,
                    aus_seit_s=aus_seit_s,
                    nachfrage=nachfrage,
                    leer=leer,
                )
            )
        return states

    def _switchable_states(self, reg: DeviceRegistry, now: datetime) -> list:
        """Laufzeitzustand aller schaltbaren Lasten aus HA-States bauen.

        Der An/Aus-Zustand und die Zeit seit dem letzten Schaltvorgang kommen aus
        der Steuer-Entität (min_on/min_off/max_block); die erwartete Leistung
        wird aus `power_entity` gelernt (nach Anlaufkarenz, asymmetrisch gedämpft
        — siehe `lern_leistung`), bis dahin greift im Planner der konservative
        Fallback.

        Wärmeerzeuger (Rolle Heizung) stehen hier mit drin: sie konkurrieren im
        selben Überschuss-Budget und derselben Prioritätsreihenfolge wie jede
        andere Schaltlast. Was sie zusätzlich haben — Frostschutz, Sommersperre,
        Heizkurve — trägt `_heating_states`.

        „An" ist dabei domänenabhängig: ein `switch` steht auf `on`, eine
        `climate`-Entität auf ihrem HVAC-Modus (`heat`, `auto`, …). Ohne diese
        Unterscheidung gälte eine climate-geführte Anlage dauerhaft als aus —
        dann lernt HEMS ihre Leistung nie, hält `min_on` nie ein und meldet sie
        im Lastfluss als aus, während sie heizt.
        """
        states = []
        for s in [*reg.switchables, *reg.heatings]:
            ist_heizung = isinstance(s, HeatingSystem)
            power = self._power_w(s.power_entity)
            st = self.hass.states.get(s.switch_entity) if s.switch_entity else None
            ist_an = entity_domain.ist_an(
                s.switch_entity, st.state if st is not None else None
            )
            seit = (
                (now - st.last_changed).total_seconds() if st is not None else None
            )
            if self.power_memory is not None and ist_an and power is not None:
                # Wärmepumpen laufen mit hohem Standby-Sockel an — für sie gilt
                # ein deutlich höherer Boden als für eine kleine Steckdosenlast.
                neu = lern_leistung(
                    self.power_memory.get(s.id),
                    power,
                    seit,
                    floor_w=(
                        SWITCH_LEARN_FLOOR_HEAT_W
                        if ist_heizung
                        else SWITCH_LEARN_FLOOR_W
                    ),
                )
                if neu is not None:
                    self.power_memory.learn(s.id, neu)
            states.append(
                SwitchableState(
                    name=s.name,
                    id=s.id,
                    priority=s.priority,
                    power_w=power,
                    erwartet_w=(
                        self.power_memory.get(s.id)
                        if self.power_memory is not None
                        else None
                    ),
                    ist_an=ist_an,
                    an_seit_s=seit if ist_an else None,
                    aus_seit_s=seit if not ist_an else None,
                    min_on_min=s.min_on_min,
                    min_off_min=s.min_off_min,
                    max_block_min=s.max_block_min,
                )
            )
        # Über beide Rollen hinweg nach Nutzer-Priorität sortieren; sonst käme
        # jede Heizung pauschal hinter jede Schaltlast, egal wie sie eingestellt
        # ist. Bei Gleichstand entscheidet die Reihenfolge oben (Schaltlasten
        # zuerst) — `sorted` ist stabil.
        states.sort(key=lambda st: st.priority)
        return states

    def _betriebsart(self, h) -> str:
        """Betriebsart einer Anlage — mit Gedächtnis über das Abschalten hinweg.

        Eine ausgeschaltete `climate`-Entität steht auf `off` und sagt nicht
        mehr, was sie vorher tat. Ohne Gedächtnis fiele sie damit auf „heizen"
        zurück, und HEMS würde eine Anlage, die es selbst aus dem Kühlbetrieb
        genommen hat, nach der Sommersperre beurteilen — also nie wieder
        einschalten und beim Einschalten den falschen Modus schreiben.

        Gemerkt wird nur der zuletzt *gesehene* aktive Modus, nicht der
        geschriebene: Was am Gerät steht, ist die Wahrheit, auch wenn jemand
        anders es umgestellt hat. Nach einem Neustart ist das Gedächtnis leer —
        dann gilt wieder „heizen", das Verhalten vor dieser Buchführung.

        **`fremd` wird nicht gemerkt.** Diese Betriebsart heißt „HEMS lässt die
        Anlage in Ruhe" und beschreibt immer eine laufende: Ausgeschaltet steht
        eine climate-Entität auf `off`, nie auf `heat_cool`. Käme sie aus dem
        Gedächtnis, träfe sie eine abgeschaltete Anlage — und die bekäme dann
        keinen Frostschutz mehr, weil die Witterungsführung im Fremdmodus gar
        nicht erst rechnet. Bei einer climate-Entität ist dieser Frostschutz die
        einzige Rückfallebene, die HEMS kennt; ihn an einem Modus zu verlieren,
        in dem die Anlage vor Wochen einmal lief, wäre der teuerste denkbare
        Nebeneffekt dieser Buchführung.
        """
        if not h.switch_entity:
            return entity_domain.BETRIEBSART_HEIZEN
        st = self.hass.states.get(h.switch_entity)
        state = st.state if st is not None else None
        art = entity_domain.betriebsart(
            h.switch_entity, state, h.mode_heat_option, h.mode_cool_option
        )
        if entity_domain.ist_an(h.switch_entity, state):
            if art == entity_domain.BETRIEBSART_FREMD:
                self._letzte_betriebsart.pop(h.id, None)
            else:
                self._letzte_betriebsart[h.id] = art
            return art
        return self._letzte_betriebsart.get(h.id, art)

    def _heating_states(self, reg: DeviceRegistry, now: datetime) -> list:
        """Witterungsführung der Wärmeerzeuger: Außentemperatur und Parameter.

        Die Außentemperatur kommt aus dem eigenen Sensor der Anlage, sonst aus
        der global konfigurierten Wetter-Entität. Fehlt beides, bleibt sie
        `None` — `heating_control` schaltet dann nichts ab, statt blind zu
        regeln.
        """
        if not reg.heatings:
            return []
        wetter_c = self._weather.outdoor_c()
        month = dt_util.now().month
        return [
            HeatingState(
                name=h.name,
                id=h.id,
                outdoor_temp_c=(
                    self._num(h.outdoor_temp_entity)
                    if h.outdoor_temp_entity
                    else wetter_c
                ),
                month=month,
                betriebsart=self._betriebsart(h),
                hat_vorlauf_entity=bool(h.flow_setpoint_entity),
                frost_on_c=h.frost_on_c,
                frost_off_c=h.frost_off_c,
                heat_on_c=h.heat_on_c,
                heat_off_c=h.heat_off_c,
                curve_base_c=h.curve_base_c,
                curve_slope=h.curve_slope,
                vlt_min_c=h.vlt_min_c,
                vlt_max_c=h.vlt_max_c,
                heat_lock_from_month=h.heat_lock_from_month,
                heat_lock_to_month=h.heat_lock_to_month,
            )
            for h in reg.heatings
        ]

    def _own_entity_id(self, key: str) -> str | None:
        """Entity-ID einer eigenen Entität über die Registry auflösen.

        Die Karte kann die IDs nicht raten: Der Slug hängt an der beim
        Anlegen vergebenen Bezeichnung und lässt sich vom Nutzer umbenennen.
        Nach einer Umbenennung greift der neue Name mit dem nächsten
        Update-Zyklus.
        """
        from homeassistant.helpers import entity_registry as er

        return er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, f"{self.entry.entry_id}_{key}"
        )

    # -- Update ------------------------------------------------------------

    async def _async_update_data(self) -> HemsData:
        data = HemsData()
        reg = self.registry

        # Meter (positiv = Netzbezug)
        raw = self._power_w(self._opt(CONF_METER, None))
        if raw is not None:
            data.saldo_w = -raw if self._opt(CONF_INVERT, False) else raw

        # Forecast-Fusion über alle Flächen
        data.pv_today_kwh = round(
            self._sum_energy([f.energy_today for f in reg.forecasts]), 2
        )
        data.pv_remaining_kwh = round(
            self._sum_energy([f.energy_remaining for f in reg.forecasts]), 2
        )
        data.pv_tomorrow_kwh = round(
            self._sum_energy([f.energy_tomorrow for f in reg.forecasts]), 2
        )
        # Eine globale Quelle für die PV-Momentanleistung; ältere
        # Konfigurationen hatten sie pro Prognosefläche ("power_now").
        pv_entity = self._opt(CONF_PV_POWER, None)
        pv_sources = (
            [pv_entity]
            if pv_entity
            else [d.get("power_now") for d in self.entry.options.get(CONF_DEVICES, [])]
        )
        data.pv_power_now_w = self._sum_power(pv_sources)
        # Wechselrichter mit umgekehrtem Vorzeichen (negativ = Erzeugung) hier
        # normalisieren — vor pv_minus_battery und allen Folgeberechnungen, die
        # positiv = Erzeugung erwarten.
        if data.pv_power_now_w is not None and self._opt(CONF_INVERT_PV, False):
            data.pv_power_now_w = -data.pv_power_now_w

        # Ist-Leistungen für den Lastfluss.
        # Batterie-Konvention: positiv = Entladen ins Haus, negativ = Laden.
        data.batterie_w = self._sum_power([s.power_entity for s in reg.storages])

        # Hängen PV und Akku am selben Messpunkt (Hybrid-Wechselrichter), enthält
        # die gemessene PV-Leistung die Akkuleistung: Entladen (batterie_w > 0)
        # treibt sie hoch, Laden (< 0) senkt sie. Die Akkuleistung
        # herausrechnen (pv - batterie_w) liefert die reine Erzeugung und behebt
        # zugleich den Doppelzähler in haus_w unten (dort geht batterie_w bereits
        # separat ein). Nur auf die gemessene PV anwenden — die Schätzung weiter
        # unten ist prognosebasiert und akku-frei. Kein Herausrechnen ohne
        # bekannte Akkuleistung. Untergrenze 0, da echte Erzeugung nie negativ.
        if (
            self._opt(CONF_PV_MINUS_BATTERY, False)
            and data.pv_power_now_w is not None
            and data.batterie_w is not None
        ):
            data.pv_power_now_w = round(
                max(0.0, data.pv_power_now_w - data.batterie_w), 0
            )
        # Nur die Wärmeerzeuger (Rolle Heizung) sind „die Wärmepumpe"; alle
        # übrigen Schaltlasten stehen einzeln in data.schaltlasten (siehe
        # unten) statt anonym in dieser Summe.
        data.waermepumpe_w = self._sum_power(
            [h.power_entity for h in reg.heatings]
        )
        data.wallbox_w = self._sum_power([m.power_entity for m in reg.modulateds])

        # Sonnenstände: nächster Untergang, danach der folgende Aufgang
        now = dt_util.utcnow()
        next_sunrise = get_astral_event_next(self.hass, "sunrise", utc_point_in_time=now)
        sunset = get_astral_event_next(self.hass, "sunset", utc_point_in_time=now)
        sunrise = get_astral_event_next(
            self.hass, "sunrise", utc_point_in_time=sunset or now
        )

        # Ohne Messquelle wird die PV-Momentanleistung geschätzt: Restenergie
        # der Prognose gleichmäßig über das restliche Sonnenfenster verteilt.
        if data.pv_power_now_w is None and sunset is not None:
            data.pv_power_estimated = True
            sun_up = next_sunrise is None or next_sunrise > sunset
            window_h = (sunset - now).total_seconds() / 3600
            if sun_up and window_h > 0.1:
                data.pv_power_now_w = round(
                    data.pv_remaining_kwh / window_h * 1000, 0
                )
            else:
                data.pv_power_now_w = 0.0

        if data.saldo_w is not None:
            data.haus_w = round(
                max(
                    0.0,
                    (data.pv_power_now_w or 0.0)
                    + data.saldo_w
                    + (data.batterie_w or 0.0),
                ),
                0,
            )

        data.wetter_morgen, data.wetter_faktor_morgen = await self._weather.tomorrow()

        if sunset is None or sunrise is None:
            return data  # Polarnacht/-tag: ohne Sonnenzeiten keine Planung

        storages = [
            StorageState(
                name=s.name,
                soc=self._num(s.soc_entity),
                capacity_kwh=s.capacity_kwh,
                reserve_soc=s.reserve_soc,
                max_charge_w=s.max_charge_w,
                max_discharge_w=s.max_discharge_w,
                power_w=self._power_w(s.power_entity),
                cold_reserve=s.cold_reserve,
            )
            for s in reg.storages
        ]
        # Pro-Speicher-Werte für die Lastfluss-Karte (dynamisch je Speicher).
        data.speicher_liste = [
            {
                "name": st.name,
                "soc": st.soc,
                "watt": st.power_w,
                "kapazitaet_kwh": st.capacity_kwh,
            }
            for st in storages
        ]
        thermal = reg.thermals[0] if reg.thermals else None

        # Darstellungshorizont der Plankarte: der ganze heutige und der ganze
        # morgige Kalendertag (lokal), plus die Sonnenzeiten beider Tage für
        # die PV-Glocken. Der Planner rechnet ausschließlich in UTC.
        today_local = dt_util.now().date()
        tomorrow_local = today_local + timedelta(days=1)
        # Lokale Uhrzeit als Offset gegen UTC: Die Ladefenster der Speicher-
        # Strategie sind Uhrzeiten, der Planner rechnet in UTC (und kennt keine
        # Zeitzone). `utcoffset()` fehlt nur bei naiven Zeiten — dt_util liefert
        # immer aware, der Fallback ist reine Vorsicht.
        offset = dt_util.now().utcoffset()
        utc_offset_h = offset.total_seconds() / 3600 if offset is not None else 0.0
        horizon_start = dt_util.as_utc(dt_util.start_of_local_day())
        horizon_end = dt_util.as_utc(
            dt_util.start_of_local_day(today_local + timedelta(days=2))
        )
        warmwasser_sperren = block_windows(
            thermal.block_start if thermal else None,
            thermal.block_end if thermal else None,
            horizon_start,
            horizon_end,
            dt_util.DEFAULT_TIME_ZONE,
        )
        warmwasser_legionellen = weekly_windows(
            parse_weekday(thermal.legionella_weekday) if thermal else None,
            thermal.legionella_start if thermal else None,
            thermal.legionella_end if thermal else None,
            horizon_start,
            horizon_end,
            dt_util.DEFAULT_TIME_ZONE,
        )

        modulateds = self._modulated_states(reg, now)
        switchables = self._switchable_states(reg, now)
        heatings = self._heating_states(reg, now)

        night_load_w = await self._load_model.refresh()

        data.plan = compute_plan(
            PlanInput(
                now=now,
                sunset=sunset,
                sunrise=sunrise,
                pv_today_kwh=data.pv_today_kwh,
                pv_remaining_kwh=data.pv_remaining_kwh,
                pv_tomorrow_kwh=data.pv_tomorrow_kwh,
                pv_power_now_w=data.pv_power_now_w,
                saldo_w=data.saldo_w,
                storages=storages,
                night_load_w=night_load_w,
                baseline_load_w=float(
                    self._opt(CONF_BASELINE_W, DEFAULT_BASELINE_W)
                ),
                thermal_temp=self._num(thermal.temp_entity) if thermal else None,
                thermal_base=thermal.base_target if thermal else 48,
                thermal_comfort=thermal.comfort_target if thermal else 60,
                thermal_present=thermal is not None,
                priority_mode=self._opt(CONF_PRIORITY_MODE, PRIORITY_AUTO),
                goal=self.goal,
                gain_level=self.gain_level,
                ev_force=self.ev_force,
                wallbox_w=data.wallbox_w,
                weather_factor_tomorrow=data.wetter_faktor_morgen,
                free_kwh=float(self._opt(CONF_FREE_KWH, DEFAULT_FREE_KWH)),
                free_h=float(self._opt(CONF_FREE_H, DEFAULT_FREE_H)),
                next_sunrise=next_sunrise,
                load_profile_w=self._load_model.load_profile,
                utc_offset_h=utc_offset_h,
                horizon_start=horizon_start,
                horizon_end=horizon_end,
                today_sunrise=get_astral_event_date(self.hass, "sunrise", today_local),
                today_sunset=get_astral_event_date(self.hass, "sunset", today_local),
                tomorrow_sunrise=get_astral_event_date(
                    self.hass, "sunrise", tomorrow_local
                ),
                tomorrow_sunset=get_astral_event_date(
                    self.hass, "sunset", tomorrow_local
                ),
                thermal_block_windows=warmwasser_sperren,
                thermal_legionella_windows=warmwasser_legionellen,
                thermal_legionella_target=thermal.legionella_target
                if thermal
                else 60,
                thermal_boost_soc_on=thermal.boost_soc_on if thermal else 80,
                thermal_boost_soc_off=thermal.boost_soc_off if thermal else 75,
                thermal_boost_saldo_on_w=thermal.boost_saldo_on_w
                if thermal
                else -2800,
                thermal_boost_saldo_off_w=thermal.boost_saldo_off_w
                if thermal
                else 200,
                modulateds=modulateds,
                switchables=switchables,
                heatings=heatings,
                flags=self._plan_flags,
            )
        )
        self._plan_flags = data.plan.flags

        # Pro-Schaltlast-Zeilen für die Lastfluss-Karte. Erst hier, weil die
        # Empfehlung (an/aus samt Begründung) aus dem eben gelaufenen Plan
        # kommt; ohne Empfehlung (kein Saldo) bleibt nur der Ist-Zustand.
        empfehlung = (
            {sp.id: sp for sp in data.plan.schaltbare.lasten}
            if data.plan.schaltbare is not None
            else {}
        )
        heizungs_ids = {h.id for h in reg.heatings}
        data.schaltlasten = [
            {
                "name": st.name,
                "prio": st.priority,
                "ist_an": st.ist_an,
                "watt": st.power_w,
                "erwartet_w": st.erwartet_w,
                "soll_an": empfehlung[st.id].an if st.id in empfehlung else None,
                "grund": empfehlung[st.id].grund if st.id in empfehlung else "",
                "heizung": st.id in heizungs_ids,
            }
            for st in switchables
        ]

        # Heizungs-Zeilen für den eigenen Reiter: Witterungsführung samt
        # Vorlauf-Sollwert. Sie entsteht ohne Netzsaldo und steht deshalb auch
        # dann, wenn es gar keine Überschuss-Empfehlung gibt.
        heiz_plan = (
            {sp.id: sp for sp in data.plan.heizung.anlagen}
            if data.plan.heizung is not None
            else {}
        )
        data.heizungen = [
            {
                "name": h.name,
                "id": h.id,
                "prio": h.priority,
                "ist_an": next(
                    (st.ist_an for st in switchables if st.id == h.id), False
                ),
                "watt": self._power_w(h.power_entity),
                "t_aussen_c": heiz_plan[h.id].t_aussen_c if h.id in heiz_plan else None,
                "status": heiz_plan[h.id].status if h.id in heiz_plan else "",
                "grund": heiz_plan[h.id].grund if h.id in heiz_plan else "",
                "frostschutz": (
                    heiz_plan[h.id].zwang_an if h.id in heiz_plan else False
                ),
                "vorlauf_soll_c": (
                    heiz_plan[h.id].vorlauf_c if h.id in heiz_plan else None
                ),
                "vorlauf_ist_c": self._num(h.flow_setpoint_entity),
                # Ob überhaupt eine Vorlauf-Entität hinterlegt ist — getrennt
                # vom aktuellen Sollwert. Der ist auch bei konfigurierter
                # Entität leer, solange nicht geheizt wird (Sperre, Heizgrenze,
                # unbekannte Außentemperatur); ohne dieses Feld läse die Karte
                # daraus „nicht eingerichtet".
                "hat_vorlauf": bool(h.flow_setpoint_entity),
                "soll_an": empfehlung[h.id].an if h.id in empfehlung else None,
                # Die Begründung der Schaltentscheidung — nicht dieselbe wie
                # `grund` daneben: der beschreibt die Witterungslage
                # („Sommersperre"), diese die Lage, die tatsächlich entschieden
                # hat („min_on gehalten"). Beide können auseinanderfallen, weil
                # die Mindestlaufzeit vor der Sperre steht (siehe
                # strategies/switchable.py).
                "soll_grund": empfehlung[h.id].grund if h.id in empfehlung else "",
                "kurve_fusspunkt_c": h.curve_base_c,
                "kurve_steilheit": h.curve_slope,
                "vlt_min_c": h.vlt_min_c,
                "vlt_max_c": h.vlt_max_c,
                "frost_on_c": h.frost_on_c,
            }
            for h in reg.heatings
        ]

        data.ziel = self.goal
        data.ev_zwang = self.ev_force

        # Config-Sanity-Check (speist binary_sensor.hems_konfiguration). Fehler/
        # Überlappungen nur bei Änderung loggen, nicht jeden 60-s-Zyklus.
        data.config_check = check_config(
            self.hass, reg, weather=self._opt(CONF_WEATHER, None)
        )
        self.config_check = data.config_check
        sig = data.config_check.signature()
        if sig != self._check_signature:
            self._check_signature = sig
            for msg in data.config_check.errors:
                _LOGGER.warning("HEMS-Config-Fehler: %s", msg)
            for msg in data.config_check.warnings:
                _LOGGER.info("HEMS-Config-Warnung: %s", msg)

        # Störungs-/Warnmeldungen an den Nutzer (Repair-Issue, Notification,
        # Push-Sensor) — Bewertung HA-frei in strategies/alerts, Zustellung hier.
        # Defensiv gekapselt: eine Meldung ist optional und darf nie den ganzen
        # Update-Zyklus reißen (sonst gingen alle HEMS-Entitäten auf unavailable).
        try:
            self._deliver_alerts(data.config_check.errors)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("HEMS: Zustellung der Störungsmeldungen fehlgeschlagen")

        data.lastprofil_quelle = self._load_model.profile_source
        data.lastprofil = profile_rows(
            self._load_model.load_profile, now, dt_util.DEFAULT_TIME_ZONE
        )
        data.verlauf_pv_entity = self._own_entity_id("pv_leistung_jetzt")
        data.verlauf_soc_entity = self._own_entity_id("speicher_soc")

        if self.mode == MODE_AUTO:
            _LOGGER.info("HEMS-Auto: %s", data.plan.empfehlung)
            await self._actuator.apply(reg, data.plan)
        else:
            if self.mode == MODE_OBSERVE:
                _LOGGER.info("HEMS-Empfehlung: %s", data.plan.empfehlung)
            # Verlassen des Auto-Modus (→ beobachten oder aus): den Akku einmalig
            # freigeben, damit er nicht mit der letzten Rate blind weiterläuft.
            # WW/WP/EV bleiben unangetastet.
            if self._prev_mode == MODE_AUTO:
                _LOGGER.info("HEMS: Auto verlassen – Akku wird auf 0/0 freigegeben")
                await self._actuator.release_battery(reg)
        self._prev_mode = self.mode

        # Entscheidungsänderungen für den Logs-Reiter fortschreiben.
        self._record_decisions(data)
        return data


    def _record_decisions(self, data: HemsData) -> None:
        """Aktuelle Entscheidungen gegen den Vorlauf diffen und Änderungen loggen.

        Der erste Lauf nach einem (Neu-)Start setzt nur die Baseline, damit das
        Verfügbarwerden der Quellen keinen Schwall Scheinänderungen erzeugt.
        """
        if self.changelog is None:
            return
        snap = decision_snapshot(self.mode, self.goal, self.ev_force, data.plan)
        prev, self._decisions = self._decisions, snap
        if prev is None:
            return
        self.changelog.add(diff_snapshots(prev, snap, dt_util.utcnow().timestamp()))
