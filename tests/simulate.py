"""Zeitverlauf-Simulator: treibt compute_plan über einen Tagesverlauf.

Plant-Modell (idealer Aktuator): der Speicher folgt dem empfohlenen Sollwert,
begrenzt durch Ladeleistung und SoC-Grenzen; der SoC wird über die Zeit
integriert. Der Netzsaldo ergibt sich aus PV, Hauslast und Speicherleistung:

    saldo = last - pv - speicherleistung        (speicherleistung + = entladen)

Damit lässt sich prüfen, ob ein Optimierungsziel über den Tag korrekt erfüllt
wird (Export ~0 bei Nulleinspeisung, Vollladung bei Vollladen usw.).
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from factories import storages
from hems import planner as P
from hems.const import GOAL_SELF_CONSUMPTION
from hems.strategies.types import PlanFlags

DATA = pathlib.Path(__file__).parent / "data" / "day_profile.json"

CAP_KWH_JE = 2.0        # je Speicher
MAX_W_JE = 1200.0       # Lade-/Entladegrenze je Speicher
N_STORAGES = 3
RESERVE_SOC = 10.0
CAP_KWH = CAP_KWH_JE * N_STORAGES


@dataclass
class Step:
    minute: int
    pv_w: float
    last_w: float
    soc: float          # SoC am Ende des Schritts
    bat_w: float        # Speicherleistung (+ = entladen)
    saldo_w: float      # Netzsaldo nach Aktuierung (+ = Bezug, - = Einspeisung)
    modus: str


def load_profile() -> dict:
    return json.loads(DATA.read_text())


def simulate(goal: str = GOAL_SELF_CONSUMPTION, *, start_soc: float | None = None,
             gain_level: str = "normal", profile: dict | None = None) -> list[Step]:
    prof = profile or load_profile()
    step_min = prof["raster_min"]
    step_h = step_min / 60.0
    pv = prof["pv_w"]
    last = prof["last_w"]
    soc = start_soc if start_soc is not None else prof["start_soc"]
    bat_w = 0.0
    flags = PlanFlags()
    out: list[Step] = []

    # Sonnenzeiten grob (lokal->als reine Uhrzeit im Modell): Aufgang 05:00,
    # Untergang 21:00. Der Planner braucht sie fuer Deckel/Nachtdefizit.
    from datetime import datetime, timedelta, timezone
    UTC = timezone.utc
    day = datetime(2026, 7, 21, tzinfo=UTC)
    sunrise = day.replace(hour=5)
    sunset = day.replace(hour=21)

    for i, minute in enumerate(range(0, 24 * 60, step_min)):
        now = day + timedelta(minutes=minute)
        # Nacht: naechster Sonnenaufgang ist vor dem naechsten Untergang
        if now < sunrise:
            next_sunrise = sunrise
            eff_sunset = sunset
        elif now >= sunset:
            next_sunrise = sunrise + timedelta(days=1)
            eff_sunset = sunset + timedelta(days=1)
        else:
            next_sunrise = sunrise + timedelta(days=1)
            eff_sunset = sunset

        saldo = last[i] - pv[i] - bat_w
        ss = storages([soc] * N_STORAGES, capacity_kwh=CAP_KWH_JE,
                      max_charge_w=MAX_W_JE, max_discharge_w=MAX_W_JE,
                      reserve_soc=RESERVE_SOC)
        for s in ss:
            s.power_w = bat_w / N_STORAGES

        inp = P.PlanInput(
            now=now, sunset=eff_sunset, sunrise=sunrise, next_sunrise=next_sunrise,
            pv_today_kwh=sum(pv) * step_h / 1000,
            pv_remaining_kwh=sum(pv[i:]) * step_h / 1000,
            pv_tomorrow_kwh=sum(pv) * step_h / 1000,
            pv_power_now_w=pv[i], saldo_w=saldo, storages=ss,
            night_load_w=200.0, baseline_load_w=300.0,
            thermal_temp=None, thermal_base=48, thermal_comfort=60,
            thermal_present=False, goal=goal, gain_level=gain_level,
            weather_factor_tomorrow=0.8,
            horizon_start=day, horizon_end=day + timedelta(days=1),
            today_sunrise=sunrise, today_sunset=sunset, flags=flags,
        )
        r = P.compute_plan(inp)
        flags = r.flags

        # Tatsächlich kommandierte Speicherleistung = Summe der Zuteilung (nicht
        # das rohe Regelziel soll_w): die Zuteilung respektiert Ladedeckel und
        # Reserve, genau das schreibt der Actuator. Vorzeichen aus dem Modus
        # (+ = entladen).
        if r.regelung is None:
            soll = 0.0
        else:
            summe = sum(z.watt for z in r.regelung.zuteilung)
            soll = summe if r.regelung.modus == "entladen" else -summe
        # SoC-Machbarkeit ueber den Schritt begrenzen
        if soll < 0:  # laden
            frei_kwh = (100.0 - soc) / 100.0 * CAP_KWH
            max_lade_w = min(MAX_W_JE * N_STORAGES, frei_kwh / step_h * 1000)
            bat_w = -min(-soll, max_lade_w)
        else:         # entladen
            verf_kwh = (soc - RESERVE_SOC) / 100.0 * CAP_KWH
            max_ent_w = min(MAX_W_JE * N_STORAGES, max(0.0, verf_kwh) / step_h * 1000)
            bat_w = min(soll, max_ent_w)

        # SoC integrieren (laden erhoeht, entladen senkt)
        soc += (-bat_w * step_h / 1000) / CAP_KWH * 100
        soc = max(0.0, min(100.0, soc))

        saldo_nach = last[i] - pv[i] - bat_w
        out.append(Step(minute, pv[i], last[i], round(soc, 2), round(bat_w),
                        round(saldo_nach), r.regelung.modus if r.regelung else "—"))
    return out


# --- Kennzahlen über den Tag --------------------------------------------------
def export_kwh(steps: list[Step], step_h: float = 0.25) -> float:
    """Ins Netz eingespeiste Energie (kWh, positiv)."""
    return sum(-s.saldo_w for s in steps if s.saldo_w < 0) * step_h / 1000


def import_kwh(steps: list[Step], step_h: float = 0.25) -> float:
    """Aus dem Netz bezogene Energie (kWh)."""
    return sum(s.saldo_w for s in steps if s.saldo_w > 0) * step_h / 1000


def export_kwh_below(steps: list[Step], soc_max: float, step_h: float = 0.25) -> float:
    """Export, während der Speicher noch Platz hatte (SoC < soc_max)."""
    return sum(-s.saldo_w for s in steps if s.saldo_w < 0 and s.soc < soc_max) * step_h / 1000
