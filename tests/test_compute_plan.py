"""End-to-End-Charakterisierung von `compute_plan`: Grundinvarianten des
Gesamtplans, die den Domänen-Refactor (Schritt 3) verhaltensneutral absichern.
"""
from __future__ import annotations

from factories import plan_input
from hems import planner as P


def test_plan_grundfelder():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-1500))
    assert r.speicher_kapazitaet_kwh == 6.0
    assert r.speicher_soc == 60.0
    assert r.regelung is not None
    assert r.soc_prognose  # nicht leer
    assert r.empfehlung  # nicht leer


def test_flags_werden_fortgeschrieben():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-1500))
    # PlanFlags-Objekt im Ergebnis, unabhängig vom Eingabe-Objekt.
    assert isinstance(r.flags, P.PlanFlags)


def test_leere_speicher_liste_kein_absturz():
    r = P.compute_plan(plan_input(storage_states=[], saldo_w=-1500))
    assert r.regelung is None
    assert r.speicher_kapazitaet_kwh == 0.0


def test_soc_prognose_startet_beim_ist_soc():
    r = P.compute_plan(plan_input(socs=[50, 50, 50], saldo_w=-1500))
    assert r.soc_prognose[0].soc == 50.0
