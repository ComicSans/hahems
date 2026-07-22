"""Ladedeckel (Akku-Schonung): tagsüber HOLD, zum Abend voll.

Charakterisiert das eingebaute Verhalten aus custom_components/hems/planner.py:
`_lade_deckel_soc` / `compute_plan.lade_deckel_soc` und dessen Wirkung auf die
Ladezuteilung. Überlebt den Domänen-Refactor unverändert (nur Importpfade
ziehen ggf. mit).
"""
from __future__ import annotations

from datetime import timedelta

from factories import NOON, SUNSET, plan_input, storages, zuteilung
from hems import planner as P
from hems.const import (
    GOAL_FULL_CHARGE,
    STORAGE_DAY_HOLD_SOC,
    STORAGE_FULL_CHARGE_LEAD_H,
)


def _deckel(**kw) -> float:
    return P.compute_plan(plan_input(**kw)).lade_deckel_soc


def test_mittags_deckel_ist_hold():
    assert _deckel(socs=[60, 60, 60], saldo_w=-1500) == STORAGE_DAY_HOLD_SOC


def test_mittags_ueber_hold_keine_ladung():
    r = P.compute_plan(plan_input(socs=[80, 82, 85], saldo_w=-1500))
    assert sum(zuteilung(r).values()) == 0


def test_unter_hold_laedt_noch():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-1500))
    assert r.regelung.modus == "laden"
    assert sum(zuteilung(r).values()) > 0


def test_rampe_eine_stunde_vor_sonnenuntergang():
    spaet = SUNSET - timedelta(hours=1)
    deckel = _deckel(now=spaet, socs=[80, 82, 85], saldo_w=-1500)
    erwartet = STORAGE_DAY_HOLD_SOC + (
        1 - 1 / STORAGE_FULL_CHARGE_LEAD_H
    ) * (100 - STORAGE_DAY_HOLD_SOC)
    assert abs(deckel - round(erwartet, 1)) < 0.05


def test_ziel_vollladen_hebt_deckel_auf():
    assert _deckel(socs=[80, 82, 85], saldo_w=-1500, goal=GOAL_FULL_CHARGE) == 100.0


def test_heute_knapp_hebt_deckel_auf():
    # Wenig PV-Rest, hohe Grundlast => Restertrag reicht nicht zum Nachladen.
    deckel = _deckel(
        socs=[60, 60, 60], saldo_w=-1500, pv_remaining_kwh=1.0, baseline_load_w=1500.0
    )
    assert deckel == 100.0


def test_nacht_deckel_ist_100():
    day = NOON.replace(hour=1)  # 01:00, vor Sonnenaufgang
    inp = plan_input(
        now=day,
        socs=[60, 60, 60],
        saldo_w=-1500,
        next_sunrise=day.replace(hour=5),
        sunset=day.replace(hour=19),
    )
    assert P.compute_plan(inp).lade_deckel_soc == 100.0


def test_prognose_tagsueber_unter_hold():
    r = P.compute_plan(plan_input(socs=[30, 30, 30], saldo_w=-3000))
    fenster = [
        pt.soc for pt in r.soc_prognose if NOON <= pt.zeit <= NOON + timedelta(hours=3)
    ]
    assert fenster and all(s <= STORAGE_DAY_HOLD_SOC + 0.6 for s in fenster)
