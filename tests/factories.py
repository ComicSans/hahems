"""Baukästen für Planner-Testeingaben.

Bewusst schlank: sonnige Mittagslage mit Überschuss als Default, einzelne
Szenarien überschreiben gezielt. Alle Zeiten in UTC (der Planner kennt keine
Zeitzone).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hems.const import GAIN_NORMAL, GOAL_SELF_CONSUMPTION
from hems.strategies import types as P

UTC = timezone.utc

# Referenztag: 22.07.2026. Mittag 11:00 UTC, Sonnenuntergang 19:00 UTC.
DAY = datetime(2026, 7, 22, tzinfo=UTC)
NOON = DAY.replace(hour=11)
SUNSET = DAY.replace(hour=19)
SUNRISE = DAY.replace(hour=5)


def storage(
    name: str,
    soc: float | None,
    *,
    capacity_kwh: float = 2.0,
    reserve_soc: float = 10.0,
    max_charge_w: float = 1200.0,
    max_discharge_w: float = 1200.0,
    power_w: float | None = 0.0,
    cold_reserve: bool = False,
) -> P.StorageState:
    return P.StorageState(
        name=name,
        soc=soc,
        capacity_kwh=capacity_kwh,
        reserve_soc=reserve_soc,
        max_charge_w=max_charge_w,
        max_discharge_w=max_discharge_w,
        power_w=power_w,
        cold_reserve=cold_reserve,
    )


def storages(socs: list[float], **kw) -> list[P.StorageState]:
    return [storage(f"L{i + 1}", s, **kw) for i, s in enumerate(socs)]


def load(
    name: str = "WB",
    *,
    id: str = "wb1",
    min_a: float = 6.0,
    max_a: float = 16.0,
    phases: int = 3,
    priority: int = 1,
    power_w: float | None = 0.0,
    energie_heute_kwh: float = 0.0,
    ist_an: bool = False,
    an_seit_s: float | None = None,
    aus_seit_s: float | None = None,
    min_on_min: int = 10,
    min_off_min: int = 10,
    nachfrage: bool = False,
    leer: bool = False,
) -> P.ModulatedState:
    return P.ModulatedState(
        name=name,
        id=id,
        min_a=min_a,
        max_a=max_a,
        phases=phases,
        priority=priority,
        min_on_min=min_on_min,
        min_off_min=min_off_min,
        power_w=power_w,
        energie_heute_kwh=energie_heute_kwh,
        ist_an=ist_an,
        an_seit_s=an_seit_s,
        aus_seit_s=aus_seit_s,
        nachfrage=nachfrage,
        leer=leer,
    )


def heating(
    *,
    name: str = "HK",
    outdoor_temp_c: float | None = 5.0,
    demand_pct: float | None = 50.0,
    heat_locked: bool = False,
    heat_on_c: float = 14.0,
    heat_off_c: float = 17.0,
    cool_on_c: float = 25.0,
    cool_off_c: float = 23.0,
    frost_on_c: float = 6.0,
    frost_off_c: float = 8.0,
    curve_base_c: float = 40.0,
    curve_slope: float = 0.8,
    vlt_min_c: float = 28.0,
    vlt_min_cold_c: float = 32.0,
    vlt_max_c: float = 45.0,
    cool_vlt_c: float = 18.0,
) -> P.HeatingState:
    return P.HeatingState(
        name=name,
        outdoor_temp_c=outdoor_temp_c,
        demand_pct=demand_pct,
        heat_locked=heat_locked,
        heat_on_c=heat_on_c,
        heat_off_c=heat_off_c,
        cool_on_c=cool_on_c,
        cool_off_c=cool_off_c,
        frost_on_c=frost_on_c,
        frost_off_c=frost_off_c,
        curve_base_c=curve_base_c,
        curve_slope=curve_slope,
        vlt_min_c=vlt_min_c,
        vlt_min_cold_c=vlt_min_cold_c,
        vlt_max_c=vlt_max_c,
        cool_vlt_c=cool_vlt_c,
    )


def switchable(
    name: str = "Pumpe",
    *,
    id: str = "sw1",
    priority: int = 1,
    power_w: float | None = None,
    erwartet_w: float | None = 1500.0,
    ist_an: bool = False,
    an_seit_s: float | None = None,
    aus_seit_s: float | None = None,
    min_on_min: int = 20,
    min_off_min: int = 10,
    max_block_min: int = 120,
) -> P.SwitchableState:
    return P.SwitchableState(
        name=name,
        id=id,
        priority=priority,
        power_w=power_w,
        erwartet_w=erwartet_w,
        ist_an=ist_an,
        an_seit_s=an_seit_s,
        aus_seit_s=aus_seit_s,
        min_on_min=min_on_min,
        min_off_min=min_off_min,
        max_block_min=max_block_min,
    )


def plan_input(
    *,
    now: datetime = NOON,
    sunset: datetime = SUNSET,
    sunrise: datetime = SUNRISE,
    next_sunrise: datetime | None = None,
    socs: list[float] | None = None,
    storage_states: list[P.StorageState] | None = None,
    saldo_w: float | None = -1500.0,
    pv_remaining_kwh: float = 20.0,
    pv_tomorrow_kwh: float = 30.0,
    weather_factor_tomorrow: float | None = 0.8,
    goal: str = GOAL_SELF_CONSUMPTION,
    gain_level: str = GAIN_NORMAL,
    baseline_load_w: float = 400.0,
    night_load_w: float = 400.0,
    modulateds: list[P.ModulatedState] | None = None,
    wallbox_w: float | None = None,
    ev_force: bool = False,
    priority_mode: str = "auto",
    switchables: list[P.SwitchableState] | None = None,
    flags: P.PlanFlags | None = None,
    thermal_present: bool = False,
    thermal_temp: float | None = None,
    thermal_base: float = 48.0,
    thermal_comfort: float = 60.0,
    thermal_block_windows: list[tuple[datetime, datetime]] | None = None,
    thermal_legionella_windows: list[tuple[datetime, datetime]] | None = None,
    thermal_legionella_target: float = 60.0,
    heating_state: P.HeatingState | None = None,
    wp_model: P.WpModel | None = None,
    load_profile_w: dict[tuple[int, int], float] | None = None,
    temp_forecast_c: dict[datetime, float] | None = None,
) -> P.PlanInput:
    if storage_states is None:
        storage_states = storages(socs if socs is not None else [60, 60, 60])
    if next_sunrise is None:
        # Tag: nächster Sonnenaufgang liegt hinter dem nächsten Sonnenuntergang.
        next_sunrise = sunset + timedelta(hours=12)
    return P.PlanInput(
        now=now,
        sunset=sunset,
        sunrise=sunrise,
        next_sunrise=next_sunrise,
        pv_today_kwh=10.0,
        pv_remaining_kwh=pv_remaining_kwh,
        pv_tomorrow_kwh=pv_tomorrow_kwh,
        pv_power_now_w=3000.0,
        saldo_w=saldo_w,
        storages=storage_states,
        night_load_w=night_load_w,
        baseline_load_w=baseline_load_w,
        thermal_temp=thermal_temp,
        thermal_base=thermal_base,
        thermal_comfort=thermal_comfort,
        thermal_present=thermal_present,
        thermal_block_windows=thermal_block_windows or [],
        thermal_legionella_windows=thermal_legionella_windows or [],
        thermal_legionella_target=thermal_legionella_target,
        goal=goal,
        gain_level=gain_level,
        priority_mode=priority_mode,
        ev_force=ev_force,
        wallbox_w=wallbox_w,
        weather_factor_tomorrow=weather_factor_tomorrow,
        modulateds=modulateds if modulateds is not None else [],
        switchables=switchables if switchables is not None else [],
        heating=heating_state,
        wp_model=wp_model,
        load_profile_w=load_profile_w,
        temp_forecast_c=temp_forecast_c,
        horizon_start=now.replace(hour=0, minute=0),
        horizon_end=(now + timedelta(days=1)).replace(hour=0, minute=0),
        today_sunrise=sunrise,
        today_sunset=sunset,
        flags=flags if flags is not None else P.PlanFlags(),
    )


def zuteilung(res: P.PlanResult) -> dict[str, float]:
    """Speicher-Sollwerte je Name aus dem Regelungsergebnis."""
    if res.regelung is None:
        return {}
    return {z.name: z.watt for z in res.regelung.zuteilung}
