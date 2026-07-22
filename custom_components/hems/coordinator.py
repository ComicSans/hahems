"""Koordinator: liest Ist-Werte und Prognosen, ruft den Planner auf."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
)
from homeassistant.helpers.sun import get_astral_event_date, get_astral_event_next
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASELINE_W,
    CONF_DEVICES,
    CONF_FREE_H,
    CONF_FREE_KWH,
    CONF_INVERT,
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
    DEFAULT_WP_W_PER_K,
    DOMAIN,
    EV_DEMAND_FLOOR_W,
    SWITCH_LEARN_FLOOR_W,
    EV_DEMAND_GRACE_S,
    EV_EMPTY_COOLDOWN_S,
    GOAL_SELF_CONSUMPTION,
    MODE_AUTO,
    MODE_OBSERVE,
    PRIORITY_AUTO,
    WEATHER_CONDITION_FACTORS,
    WP_MODEL_DAYS,
    WP_MODEL_MIN_HOURS,
)
from .actuator import Actuator
from .changelog import ChangeLog, decision_snapshot, diff_snapshots
from .config_check import ConfigCheck, check_config
from .models import DeviceRegistry, parse_devices
from .planner import block_windows, compute_plan, weekly_windows
from .strategies.types import (
    HeatingState,
    ModulatedState,
    PlanFlags,
    PlanInput,
    PlanResult,
    StorageState,
    SwitchableState,
    WpModel,
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


def _parse_weekday(value: str | int | None) -> int | None:
    """Wochentag aus den Optionen (Select liefert Strings) nach 0–6 wandeln."""
    if value is None or value == "" or value == "none":
        return None
    try:
        day = int(value)
    except (TypeError, ValueError):
        return None
    return day if 0 <= day <= 6 else None


def _profile_rows(
    profile: dict[tuple[int, int], float] | None, now: datetime
) -> list[dict]:
    """Gelerntes Profil für die Anzeige in lokale Stunden umrechnen."""
    if not profile:
        return []
    midnight = now.replace(minute=0, second=0, microsecond=0)
    rows: list[dict] = []
    for utc_hour in range(24):
        werktag = profile.get((0, utc_hour))
        wochenende = profile.get((1, utc_hour))
        if werktag is None and wochenende is None:
            continue
        local_hour = dt_util.as_local(midnight.replace(hour=utc_hour)).hour
        rows.append(
            {"stunde": local_hour, "werktag_w": werktag, "wochenende_w": wochenende}
        )
    rows.sort(key=lambda r: r["stunde"])
    return rows


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
        self.wp_w: float | None = None
        self.wallbox_w: float | None = None
        self.haus_w: float | None = None
        self.lastprofil_quelle: str = ""
        self.lastprofil: list[dict] = []
        self.wp_modell: dict | None = None
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
        self._night_load_w: float | None = None
        self._night_load_fetched: datetime | None = None
        # (Tagtyp, UTC-Stunde) → mittlere Last in W; Tagtyp 0 = Werktag, 1 = Wochenende
        self._load_profile: dict[tuple[int, int], float] | None = None
        self._profile_source: str = "konstante"
        self._weather_cache: tuple[str | None, float | None] = (None, None)
        self._weather_fetched: datetime | None = None
        # WP-Verbrauchsmodell (Heizgradstunden), im STATS_CACHE-Takt gelernt.
        self._wp_model: WpModel | None = None
        self._wp_model_quelle: str = ""
        # Stündliche Temperaturvorhersage, im WEATHER_CACHE-Takt geholt.
        self._temp_forecast: dict[datetime, float] = {}
        self._temp_forecast_fetched: datetime | None = None
        self._unit_warned: set[str] = set()
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
        # Gelernte erwartete Leistung schaltbarer Lasten (id → letzter An-Wert).
        self._sw_erwartet_w: dict[str, float] = {}

    # -- Konfiguration -----------------------------------------------------

    def _opt(self, key: str, default):
        return self.entry.options.get(key, self.entry.data.get(key, default))

    @property
    def registry(self) -> DeviceRegistry:
        return parse_devices(self.entry.options.get(CONF_DEVICES, []))

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
        for h in reg.heatings:
            ids.update(e for e in (h.outdoor_temp_entity, h.demand_entity) if e)
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
        """Laufzeitzustand der schaltbaren Lasten aus HA-States bauen.

        Der An/Aus-Zustand und die Zeit seit dem letzten Schaltvorgang kommen aus
        dem Schalter (min_on/min_off/max_block); die erwartete Leistung wird aus
        `power_entity` gelernt (letzter nennenswerter An-Wert), bis dahin greift
        im Planner der konservative Fallback.
        """
        states = []
        for s in reg.switchables:
            power = self._power_w(s.power_entity)
            st = self.hass.states.get(s.switch_entity)
            ist_an = bool(st and st.state == "on")
            seit = (
                (now - st.last_changed).total_seconds() if st is not None else None
            )
            if ist_an and power is not None and power > SWITCH_LEARN_FLOOR_W:
                self._sw_erwartet_w[s.id] = power
            states.append(
                SwitchableState(
                    name=s.name,
                    id=s.id,
                    priority=s.priority,
                    power_w=power,
                    erwartet_w=self._sw_erwartet_w.get(s.id),
                    ist_an=ist_an,
                    an_seit_s=seit if ist_an else None,
                    aus_seit_s=seit if not ist_an else None,
                    min_on_min=s.min_on_min,
                    min_off_min=s.min_off_min,
                    max_block_min=s.max_block_min,
                )
            )
        return states

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

    async def _refresh_load_model(self) -> float:
        """Lastmodell lernen: Nacht-Grundlast (Skalar) und 24-h-Lastprofil.

        Primärquelle für das Profil ist der bereits rekonstruierte Haus-
        verbrauch (`lastfluss`-Sensor, PV- und akkukompensiert) — damit
        bekommen auch die Tagesstunden ein echtes Profil, nicht nur die
        Nacht. Fehlt dessen Historie (frische Installation), greift das
        Nacht-Profil aus dem rohen Zähler, zuletzt der konfigurierte
        Konstantwert. Rückgabe ist die Nacht-Grundlast als Fallback-Skalar.

        Liegt eine WP-Leistungsstatistik vor, wird sie stundenweise aus
        beiden Profilquellen herausgerechnet und stattdessen ein
        temperaturabhängiges WP-Verbrauchsmodell gelernt — das gemittelte
        Profil würde den WP-Verbrauch sonst wetterblind fortschreiben und
        saisonalen Übergängen wochenlang hinterherlaufen.
        """
        fallback = float(self._opt(CONF_NIGHT_W, DEFAULT_NIGHT_W))
        now = dt_util.utcnow()
        if (
            self._night_load_w is not None
            and self._night_load_fetched is not None
            and now - self._night_load_fetched < STATS_CACHE
        ):
            return self._night_load_w

        wp_by_ts = await self._wp_hourly_stats(now)
        self._wp_model, self._wp_model_quelle = await self._learn_wp_model(
            now, wp_by_ts
        )
        # Profile nur bereinigen, wenn die WP auch explizit modelliert wird —
        # sonst bliebe ihr Verbrauch komplett unberücksichtigt.
        wp_abzug = wp_by_ts if self._wp_model is not None else None
        night_scalar, night_profile = await self._meter_night_stats(now, wp_abzug)
        house_profile = await self._house_load_profile(now, wp_abzug)

        if house_profile:
            self._load_profile = house_profile
            self._profile_source = "hausverbrauch (24 h)"
        elif night_profile:
            self._load_profile = night_profile
            self._profile_source = "zähler-nacht"
        else:
            self._load_profile = None
            self._profile_source = "konstante"
        if self._wp_model is not None:
            self._profile_source += ", wp-bereinigt"

        self._night_load_w = (
            night_scalar if night_scalar and night_scalar > 0 else fallback
        )
        self._night_load_fetched = now
        return self._night_load_w

    async def _wp_hourly_stats(self, now: datetime) -> dict[float, float] | None:
        """Stündliche WP-Leistung (Summe der Leistungs-Entitäten aller
        schaltbaren Lasten) je Statistik-Zeitstempel — Basis für die
        Profilbereinigung und das Verbrauchsmodell."""
        entities = [
            s.power_entity for s in self.registry.switchables if s.power_entity
        ]
        if not entities:
            return None
        by_ts: dict[float, float] = {}
        for entity in entities:
            rows = await self._statistics_hourly_mean(
                entity, now - timedelta(days=WP_MODEL_DAYS)
            )
            for row in rows or []:
                ts, mean = row.get("start"), row.get("mean")
                if ts is None or mean is None:
                    continue
                by_ts[ts] = by_ts.get(ts, 0.0) + max(0.0, float(mean))
        return by_ts or None

    async def _learn_wp_model(
        self, now: datetime, wp_by_ts: dict[float, float] | None
    ) -> tuple[WpModel | None, str]:
        """Heizgradstunden-Modell der WP lernen: P = Basis + k × (Grenze − T).

        Basis ist die mittlere WP-Leistung oberhalb der Heizgrenze
        (Warmwasser, Standby), k die Steigung aus der Statistik der Stunden
        darunter. Ohne Heizkreis oder WP-Statistik gibt es kein Modell (die
        WP bleibt dann implizit im Lastprofil); reicht die Historie noch
        nicht, überbrückt der Richtwert aus const.py.
        """
        heating_cfg = (
            self.registry.heatings[0] if self.registry.heatings else None
        )
        if heating_cfg is None or not wp_by_ts:
            return None, ""
        limit = heating_cfg.heat_off_c
        temp_rows = await self._statistics_hourly_mean(
            heating_cfg.outdoor_temp_entity, now - timedelta(days=WP_MODEL_DAYS)
        )

        warm: list[float] = []
        heiz: list[tuple[float, float]] = []  # (Heizgradstunden, Watt)
        for row in temp_rows or []:
            ts, mean = row.get("start"), row.get("mean")
            if ts is None or mean is None or ts not in wp_by_ts:
                continue
            temp, watt = float(mean), wp_by_ts[ts]
            if temp >= limit:
                warm.append(watt)
            else:
                heiz.append((limit - temp, watt))

        base = sum(warm) / len(warm) if warm else 0.0
        if len(heiz) >= WP_MODEL_MIN_HOURS:
            hgs = sum(grad for grad, _w in heiz)
            heizenergie = sum(max(0.0, w - base) for _grad, w in heiz)
            if hgs > 0:
                return (
                    WpModel(
                        base_w=round(base, 1),
                        k_w_per_k=round(heizenergie / hgs, 1),
                        limit_c=limit,
                        max_w=round(max(wp_by_ts.values()), 0),
                    ),
                    "gelernt",
                )
        return (
            WpModel(
                base_w=round(base, 1),
                k_w_per_k=DEFAULT_WP_W_PER_K,
                limit_c=limit,
                max_w=None,
            ),
            "richtwert",
        )

    async def _statistics_hourly_mean(
        self, stat_id: str, start: datetime
    ) -> list[dict] | None:
        """Stündliche Mittelwert-Statistik ab `start` lesen (oder None)."""
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )

            stats = await get_instance(self.hass).async_add_executor_job(
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
            return None
        return stats.get(stat_id, [])

    async def _meter_night_stats(
        self, now: datetime, wp_by_ts: dict[float, float] | None = None
    ) -> tuple[float | None, dict[tuple[int, int], float] | None]:
        """Nachtlast aus dem rohen Zähler (14 Tage) als Fallback lernen.

        Nur Nachtstunden, weil tagsüber PV den Zählerwert verfälscht. Liefert
        den Skalar-Mittelwert und ein Nacht-Profil, das auf beide Wochentag-
        typen gespiegelt wird — es dient nur, bis der Hausverbrauch genug
        Historie für ein volles 24-h-Profil hat. Eine übergebene WP-Statistik
        wird stundenweise abgezogen (die WP kommt dann aus dem Modell).
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
                watt = float(mean) - (wp_by_ts or {}).get(ts, 0.0)
                by_hour.setdefault(utc.hour, []).append(max(0.0, watt))
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
        self, now: datetime, wp_by_ts: dict[float, float] | None = None
    ) -> dict[tuple[int, int], float] | None:
        """Volles 24-h-Lastprofil aus dem rekonstruierten Hausverbrauch lernen.

        Quelle ist der integrationseigene `lastfluss`-Sensor (state_class
        measurement → Langzeitstatistik). Gebündelt nach Wochentagstyp
        (Werktag/Wochenende) und UTC-Stunde über `PROFILE_DAYS`. Buckets mit
        zu wenigen Beobachtungen werden verworfen; ist das Profil insgesamt zu
        dünn, greift der Aufrufer auf das Nacht-Profil zurück. Eine übergebene
        WP-Statistik wird stundenweise abgezogen (die WP kommt dann aus dem
        Modell).
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
            watt = float(mean) - (wp_by_ts or {}).get(ts, 0.0)
            buckets.setdefault((daytype, utc.hour), []).append(max(0.0, watt))

        profile = {
            key: round(sum(vals) / len(vals), 1)
            for key, vals in buckets.items()
            if len(vals) >= MIN_PROFILE_SAMPLES
        }
        return profile if len(profile) >= MIN_PROFILE_BUCKETS else None

    async def _weather_tomorrow(self) -> tuple[str | None, float | None]:
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
            resp = await self.hass.services.async_call(
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

    async def _temp_forecast_hourly(self) -> dict[datetime, float]:
        """Stündliche Temperaturvorhersage (UTC-Stundenanfang → °C) holen.

        Speist das WP-Verbrauchsmodell des Planners. Integrationen ohne
        Stunden-Vorhersage liefern leer; der Planner fällt dann auf die
        aktuelle Außentemperatur zurück. Auch ein leeres Ergebnis wird
        gecacht, damit nicht jeder Update-Zyklus einen Service-Call kostet.
        """
        entity = self._opt(CONF_WEATHER, None)
        if not entity:
            return {}
        now = dt_util.utcnow()
        if (
            self._temp_forecast_fetched is not None
            and now - self._temp_forecast_fetched < WEATHER_CACHE
        ):
            return self._temp_forecast

        forecast: dict[datetime, float] = {}
        try:
            resp = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
            for item in (resp or {}).get(entity, {}).get("forecast", []):
                when = dt_util.parse_datetime(item.get("datetime") or "")
                temp = item.get("temperature")
                if when is None or temp is None:
                    continue
                key = dt_util.as_utc(when).replace(
                    minute=0, second=0, microsecond=0
                )
                forecast[key] = float(temp)
        except Exception as err:  # Wetter ist optional, nie fatal
            _LOGGER.debug("Stündliche Wettervorhersage nicht verfügbar: %s", err)

        self._temp_forecast = forecast
        self._temp_forecast_fetched = now
        return forecast

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
        data.wp_w = self._sum_power([s.power_entity for s in reg.switchables])
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

        data.wetter_morgen, data.wetter_faktor_morgen = await self._weather_tomorrow()

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
        heating_cfg = reg.heatings[0] if reg.heatings else None

        # Darstellungshorizont der Plankarte: der ganze heutige und der ganze
        # morgige Kalendertag (lokal), plus die Sonnenzeiten beider Tage für
        # die PV-Glocken. Der Planner rechnet ausschließlich in UTC.
        today_local = dt_util.now().date()
        tomorrow_local = today_local + timedelta(days=1)
        horizon_start = dt_util.as_utc(dt_util.start_of_local_day())
        horizon_end = dt_util.as_utc(
            dt_util.start_of_local_day(today_local + timedelta(days=2))
        )
        ww_sperren = block_windows(
            thermal.block_start if thermal else None,
            thermal.block_end if thermal else None,
            horizon_start,
            horizon_end,
            dt_util.DEFAULT_TIME_ZONE,
        )
        ww_legionellen = weekly_windows(
            _parse_weekday(thermal.legionella_weekday) if thermal else None,
            thermal.legionella_start if thermal else None,
            thermal.legionella_end if thermal else None,
            horizon_start,
            horizon_end,
            dt_util.DEFAULT_TIME_ZONE,
        )

        heating = None
        if heating_cfg is not None:
            month = dt_util.now().month
            lo, hi = heating_cfg.heat_lock_from_month, heating_cfg.heat_lock_to_month
            # Sperrbereich über den Jahreswechsel (z. B. 11 → 3) zulassen.
            locked = lo <= month <= hi if lo <= hi else (month >= lo or month <= hi)
            heating = HeatingState(
                name=heating_cfg.name,
                outdoor_temp_c=self._num(heating_cfg.outdoor_temp_entity),
                demand_pct=self._num(heating_cfg.demand_entity),
                heat_locked=locked,
                heat_on_c=heating_cfg.heat_on_c,
                heat_off_c=heating_cfg.heat_off_c,
                cool_on_c=heating_cfg.cool_on_c,
                cool_off_c=heating_cfg.cool_off_c,
                curve_base_c=heating_cfg.curve_base_c,
                curve_slope=heating_cfg.curve_slope,
                vlt_min_c=heating_cfg.vlt_min_c,
                vlt_min_cold_c=heating_cfg.vlt_min_cold_c,
                vlt_max_c=heating_cfg.vlt_max_c,
                cool_vlt_c=heating_cfg.cool_vlt_c,
            )

        modulateds = self._modulated_states(reg, now)
        switchables = self._switchable_states(reg, now)

        # Lastmodell zuerst: der Aufruf lernt auch das WP-Modell, das unten
        # in den PlanInput einfließt.
        night_load_w = await self._refresh_load_model()
        temp_forecast = await self._temp_forecast_hourly()
        if self._wp_model is not None:
            data.wp_modell = {
                "quelle": self._wp_model_quelle,
                "basis_w": self._wp_model.base_w,
                "k_w_pro_k": self._wp_model.k_w_per_k,
                "heizgrenze_c": self._wp_model.limit_c,
                "max_w": self._wp_model.max_w,
            }

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
                load_profile_w=self._load_profile,
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
                thermal_block_windows=ww_sperren,
                thermal_legionella_windows=ww_legionellen,
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
                heating=heating,
                modulateds=modulateds,
                switchables=switchables,
                wp_model=self._wp_model,
                temp_forecast_c=temp_forecast or None,
                flags=self._plan_flags,
            )
        )
        self._plan_flags = data.plan.flags

        data.ziel = self.goal
        data.ev_zwang = self.ev_force

        # Config-Sanity-Check (speist binary_sensor.hems_konfiguration). Fehler/
        # Überlappungen nur bei Änderung loggen, nicht jeden 60-s-Zyklus.
        data.config_check = check_config(self.hass, reg)
        self.config_check = data.config_check
        sig = data.config_check.signature()
        if sig != self._check_signature:
            self._check_signature = sig
            for msg in data.config_check.errors:
                _LOGGER.warning("HEMS-Config-Fehler: %s", msg)
            for msg in data.config_check.warnings:
                _LOGGER.info("HEMS-Config-Warnung: %s", msg)

        data.lastprofil_quelle = self._profile_source
        data.lastprofil = _profile_rows(self._load_profile, now)
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
