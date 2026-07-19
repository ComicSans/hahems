"""Heuristik-Planner, Phase 1: nur beobachten und empfehlen.

Reine Funktionen ohne Home-Assistant-Abhängigkeiten, damit die Logik
testbar bleibt und in Phase 4 unverändert für die Simulation taugt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .const import PRIORITY_AUTO, PRIORITY_BATTERY_FIRST, PRIORITY_EV_FIRST


@dataclass
class StorageState:
    name: str
    soc: float | None
    capacity_kwh: float
    reserve_soc: float
    max_charge_w: float


@dataclass
class PlanInput:
    now: datetime
    sunset: datetime
    sunrise: datetime
    pv_today_kwh: float
    pv_remaining_kwh: float
    pv_tomorrow_kwh: float
    pv_power_now_w: float | None
    saldo_w: float | None  # positiv = Netzbezug
    storages: list[StorageState]
    night_load_w: float
    baseline_load_w: float
    thermal_temp: float | None
    thermal_base: float
    thermal_comfort: float
    priority_mode: str = PRIORITY_AUTO


@dataclass
class PlanResult:
    nachtdefizit_kwh: float = 0.0
    ueberschuss_rest_kwh: float = 0.0
    speicher_soc: float | None = None
    speicher_verfuegbar_kwh: float = 0.0
    speicher_kapazitaet_kwh: float = 0.0
    speicher_ziel_soc: float | None = None
    speicher_bedarf_kwh: float = 0.0
    sonnenfenster_h: float = 0.0
    empfehlung: str = "keine Daten"
    prioritaeten: list[str] = field(default_factory=list)


def compute_plan(inp: PlanInput) -> PlanResult:
    result = PlanResult()

    # Sonnenfenster und Nachtdefizit
    result.sonnenfenster_h = max(
        0.0, (inp.sunset - inp.now).total_seconds() / 3600
    )
    night_h = max(0.0, (inp.sunrise - inp.sunset).total_seconds() / 3600)
    result.nachtdefizit_kwh = round(inp.night_load_w * night_h / 1000, 2)

    # Virtueller Gesamtspeicher aus allen Storages
    cap = sum(s.capacity_kwh for s in inp.storages)
    result.speicher_kapazitaet_kwh = round(cap, 2)
    known = [s for s in inp.storages if s.soc is not None]
    if known and cap > 0:
        available = sum(s.soc / 100 * s.capacity_kwh for s in known)
        reserve = sum(s.reserve_soc / 100 * s.capacity_kwh for s in inp.storages)
        result.speicher_verfuegbar_kwh = round(available, 2)
        result.speicher_soc = round(available / cap * 100, 1)
        ziel_kwh = min(cap, result.nachtdefizit_kwh + reserve)
        result.speicher_ziel_soc = round(ziel_kwh / cap * 100, 1)
        result.speicher_bedarf_kwh = round(max(0.0, ziel_kwh - available), 2)

    # Erwarteter Restverbrauch des Tages (v1: Grundlast; lernendes Profil folgt)
    expected_day_kwh = inp.baseline_load_w * result.sonnenfenster_h / 1000
    result.ueberschuss_rest_kwh = round(
        max(0.0, inp.pv_remaining_kwh - expected_day_kwh), 2
    )

    result.prioritaeten = _priorities(inp, result)
    result.empfehlung = (
        " → ".join(result.prioritaeten) if result.prioritaeten else "Einspeisen"
    )
    return result


def _priorities(inp: PlanInput, res: PlanResult) -> list[str]:
    """Dynamische Reihenfolge für den Überschuss. WW ist immer Priorität 1."""
    prio: list[str] = []

    if inp.thermal_temp is not None and inp.thermal_temp < inp.thermal_base:
        prio.append(
            f"WW-Basisladung ({inp.thermal_temp:.0f} → {inp.thermal_base:.0f} °C, notfalls Netz)"
        )

    surplus_now = inp.saldo_w is not None and inp.saldo_w < -100
    if not surplus_now and res.ueberschuss_rest_kwh <= 0:
        if res.speicher_bedarf_kwh > 0:
            prio.append(
                f"kein Überschuss; Akku fehlt {res.speicher_bedarf_kwh} kWh bis Ziel-SoC"
            )
        return prio

    ww_comfort_pending = (
        inp.thermal_temp is not None and inp.thermal_temp < inp.thermal_comfort
    )
    if ww_comfort_pending:
        prio.append(f"WW-Komfort ({inp.thermal_comfort:.0f} °C)")

    akku = (
        f"Akku laden bis {res.speicher_ziel_soc:.0f} % (+{res.speicher_bedarf_kwh} kWh)"
        if res.speicher_bedarf_kwh > 0
        else None
    )
    auto = "E-Auto mit Überschuss"
    if akku is None:
        prio.append(auto)
    elif inp.priority_mode == PRIORITY_BATTERY_FIRST:
        prio.extend([akku, auto])
    elif inp.priority_mode == PRIORITY_EV_FIRST:
        prio.extend([auto, akku])
    else:
        # Automatik: Reicht der Restertrag nicht für Akku UND Auto, bekommt der
        # Akku Vorrang, damit die Nacht gedeckt ist. Bei reichlich Ertrag darf
        # das Auto zuerst, der Akku wird dann trotzdem noch voll.
        knapp = res.ueberschuss_rest_kwh < res.speicher_bedarf_kwh * 1.5
        prio.extend([akku, auto] if knapp else [auto, akku])

    prio.append("Einspeisen")
    return prio
