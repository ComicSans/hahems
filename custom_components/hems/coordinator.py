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
    CONF_PV_POWER,
    CONF_WEATHER,
    DEFAULT_BASELINE_W,
    DEFAULT_FREE_H,
    DEFAULT_FREE_KWH,
    DEFAULT_NIGHT_W,
    DOMAIN,
    MODE_OBSERVE,
    PRIORITY_AUTO,
    WEATHER_CONDITION_FACTORS,
)
from .models import DeviceRegistry, parse_devices
from .planner import (
    PlanInput,
    PlanResult,
    StorageState,
    block_windows,
    compute_plan,
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
        self.plan: PlanResult = PlanResult()


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
        self._night_load_w: float | None = None
        self._night_load_fetched: datetime | None = None
        # (Tagtyp, UTC-Stunde) → mittlere Last in W; Tagtyp 0 = Werktag, 1 = Wochenende
        self._load_profile: dict[tuple[int, int], float] | None = None
        self._profile_source: str = "konstante"
        self._weather_cache: tuple[str | None, float | None] = (None, None)
        self._weather_fetched: datetime | None = None
        self._unit_warned: set[str] = set()

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

    async def _refresh_load_model(self) -> float:
        """Lastmodell lernen: Nacht-Grundlast (Skalar) und 24-h-Lastprofil.

        Primärquelle für das Profil ist der bereits rekonstruierte Haus-
        verbrauch (`lastfluss`-Sensor, PV- und akkukompensiert) — damit
        bekommen auch die Tagesstunden ein echtes Profil, nicht nur die
        Nacht. Fehlt dessen Historie (frische Installation), greift das
        Nacht-Profil aus dem rohen Zähler, zuletzt der konfigurierte
        Konstantwert. Rückgabe ist die Nacht-Grundlast als Fallback-Skalar.
        """
        fallback = float(self._opt(CONF_NIGHT_W, DEFAULT_NIGHT_W))
        now = dt_util.utcnow()
        if (
            self._night_load_w is not None
            and self._night_load_fetched is not None
            and now - self._night_load_fetched < STATS_CACHE
        ):
            return self._night_load_w

        night_scalar, night_profile = await self._meter_night_stats(now)
        house_profile = await self._house_load_profile(now)

        if house_profile:
            self._load_profile = house_profile
            self._profile_source = "hausverbrauch (24 h)"
        elif night_profile:
            self._load_profile = night_profile
            self._profile_source = "zähler-nacht"
        else:
            self._load_profile = None
            self._profile_source = "konstante"

        self._night_load_w = (
            night_scalar if night_scalar and night_scalar > 0 else fallback
        )
        self._night_load_fetched = now
        return self._night_load_w

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
        from homeassistant.helpers import entity_registry as er

        ent_reg = er.async_get(self.hass)
        stat_id = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{self.entry.entry_id}_lastfluss"
        )
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
            )
            for s in reg.storages
        ]
        thermal = reg.thermals[0] if reg.thermals else None

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
                night_load_w=await self._refresh_load_model(),
                baseline_load_w=float(
                    self._opt(CONF_BASELINE_W, DEFAULT_BASELINE_W)
                ),
                thermal_temp=self._num(thermal.temp_entity) if thermal else None,
                thermal_base=thermal.base_target if thermal else 48,
                thermal_comfort=thermal.comfort_target if thermal else 60,
                priority_mode=self._opt(CONF_PRIORITY_MODE, PRIORITY_AUTO),
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
            )
        )

        data.lastprofil_quelle = self._profile_source
        data.lastprofil = _profile_rows(self._load_profile, now)

        if self.mode == MODE_OBSERVE:
            _LOGGER.info("HEMS-Empfehlung: %s", data.plan.empfehlung)
        return data
