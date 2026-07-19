"""Koordinator: liest Ist-Werte und Prognosen, ruft den Planner auf."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.sun import get_astral_event_next
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
from .planner import PlanInput, PlanResult, StorageState, compute_plan

_LOGGER = logging.getLogger(__name__)

STATS_CACHE = timedelta(hours=6)
WEATHER_CACHE = timedelta(minutes=30)
NIGHT_HOURS_LOCAL = (22, 23, 0, 1, 2, 3, 4, 5)

# Umrechnung nach W bzw. kWh; Sensoren ohne Einheit werden als W/kWh gelesen.
POWER_UNITS = {"w": 1.0, "kw": 1000.0, "mw": 1_000_000.0}
ENERGY_UNITS = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}


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
        self._weather_cache: tuple[str | None, float | None] = (None, None)
        self._weather_fetched: datetime | None = None
        self._unit_warned: set[str] = set()

    # -- Konfiguration -----------------------------------------------------

    def _opt(self, key: str, default):
        return self.entry.options.get(key, self.entry.data.get(key, default))

    @property
    def registry(self) -> DeviceRegistry:
        return parse_devices(self.entry.options.get(CONF_DEVICES, []))

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

    async def _night_load(self) -> float:
        """Mittlere Nachtlast aus den Langzeitstatistiken des Zählers lernen.

        Fallback: konfigurierter Wert, falls (noch) keine Statistik vorliegt.
        """
        fallback = float(self._opt(CONF_NIGHT_W, DEFAULT_NIGHT_W))
        now = dt_util.utcnow()
        if (
            self._night_load_w is not None
            and self._night_load_fetched is not None
            and now - self._night_load_fetched < STATS_CACHE
        ):
            return self._night_load_w

        meter = self._opt(CONF_METER, None)
        learned: float | None = None
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )

            start = now - timedelta(days=14)
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                None,
                {meter},
                "hour",
                None,
                {"mean"},
            )
            means = []
            for row in stats.get(meter, []):
                ts = row.get("start")
                mean = row.get("mean")
                if ts is None or mean is None:
                    continue
                local = dt_util.as_local(dt_util.utc_from_timestamp(ts))
                if local.hour in NIGHT_HOURS_LOCAL:
                    # Nur Bezug zählt; ein evtl. gedeckelter Zähler liefert eh >= 0
                    means.append(max(0.0, float(mean)))
            if means:
                learned = sum(means) / len(means)
        except Exception as err:  # Statistik ist optional, nie fatal
            _LOGGER.debug("Nachtlast-Statistik nicht verfügbar: %s", err)

        self._night_load_w = learned if learned and learned > 0 else fallback
        self._night_load_fetched = now
        return self._night_load_w

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
            )
            for s in reg.storages
        ]
        thermal = reg.thermals[0] if reg.thermals else None

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
                night_load_w=await self._night_load(),
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
            )
        )

        if self.mode == MODE_OBSERVE:
            _LOGGER.info("HEMS-Empfehlung: %s", data.plan.empfehlung)
        return data
