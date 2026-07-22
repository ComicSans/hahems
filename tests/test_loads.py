"""Charakterisierung der Wallbox-/Lastregelung (`_modulated_control`) und der
Kopplung an die Speicherregelung (EV↔Akku).

Der EV↔Akku-Koordinationsfix (Schritt 4) baut auf diesen Erwartungen auf:
der Repro-Test für das Hunting lebt in test_ev_battery_coordination.py.
"""
from __future__ import annotations

from factories import load, plan_input, zuteilung
from hems import planner as P


def test_ohne_lasten_keine_ev_regelung():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-3000))
    assert r.ev_regelung is None


def test_zu_kleiner_ueberschuss_startet_wallbox_nicht():
    # Überschuss 4000 W < Wallbox-Minimum (6 A × 3 × 230 = 4140 W).
    wb = load("WB", power_w=0.0, ist_an=False, nachfrage=False)
    r = P.compute_plan(
        plan_input(socs=[60, 60, 60], saldo_w=-4000, modulateds=[wb], wallbox_w=0.0)
    )
    assert r.ev_regelung.lasten[0].laden is False
    assert r.ev_regelung.soll_summe_w == 0


def test_wallbox_laedt_und_akku_bekommt_residuum():
    # WB zieht 4200 W, Überschuss vor Akku 6200 W. WB bekommt den Löwenanteil,
    # der Akku regelt auf das kleine Residuum.
    wb = load("WB", power_w=4200.0, ist_an=True, an_seit_s=1200, nachfrage=True)
    r = P.compute_plan(
        plan_input(socs=[60, 60, 60], saldo_w=-2000, modulateds=[wb], wallbox_w=4200.0)
    )
    ev = r.ev_regelung
    assert ev.ueberschuss_w == 6200
    assert ev.lasten[0].laden is True
    assert ev.soll_summe_w == 5520
    # Akku sieht den um das EV-Target bereinigten Saldo und lädt nur den Rest.
    assert r.regelung.modus == "laden"
    assert sum(zuteilung(r).values()) > 0


def test_zwangsladung_volle_ampere():
    wb = load("WB", power_w=0.0, ist_an=False)
    r = P.compute_plan(
        plan_input(
            socs=[60, 60, 60],
            saldo_w=500,
            modulateds=[wb],
            wallbox_w=0.0,
            ev_force=True,
        )
    )
    ev = r.ev_regelung
    assert ev.zwang is True
    assert ev.lasten[0].laden is True
    assert ev.lasten[0].strom_a == wb.max_a
