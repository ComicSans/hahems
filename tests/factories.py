"""Baukästen für Planner-Testeingaben.

Bewusst schlank: sonnige Mittagslage mit Überschuss als Default, einzelne
Szenarien überschreiben gezielt. Alle Zeiten in UTC (der Planner kennt keine
Zeitzone).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hems import planner as P
from hems.const import GAIN_NORMAL, GOAL_SELF_CONSUMPTION

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
        power_w=power_w,
        energie_heute_kwh=energie_heute_kwh,
        ist_an=ist_an,
        an_seit_s=an_seit_s,
        nachfrage=nachfrage,
        leer=leer,
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
    flags: P.PlanFlags | None = None,
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
        thermal_temp=None,
        thermal_base=48,
        thermal_comfort=60,
        thermal_present=False,
        goal=goal,
        gain_level=gain_level,
        ev_force=ev_force,
        wallbox_w=wallbox_w,
        weather_factor_tomorrow=weather_factor_tomorrow,
        modulateds=modulateds if modulateds is not None else [],
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
