"""Bedarfsmodell (strategies/demand.py) mit gelerntem Lastprofil.

Regressionswächter für den Domänen-Refactor: diese Eingaben aktivieren den
Profilzweig von `_expected_load_w` — einen Pfad, den die übrigen Tests (ohne
load_profile_w) nie durchlaufen. Ein beim Move verlorener Import fiele erst
hier auf.
"""
from __future__ import annotations

from factories import plan_input
from hems import planner as P


def _mit_profil():
    inp = plan_input(socs=[60, 60, 60], saldo_w=-1500)
    inp.load_profile_w = {(0, h): 500.0 for h in range(24)}
    inp.load_profile_w.update({(1, h): 450.0 for h in range(24)})
    return inp


def test_compute_plan_mit_lastprofil_laeuft_durch():
    # Deckt den Profilzweig von _expected_load_w ab — kein NameError,
    # plausibler Plan.
    r = P.compute_plan(_mit_profil())
    assert r.regelung is not None
    assert r.soc_prognose  # nutzt _expected_load_w
    assert r.ueberschuss_rest_kwh >= 0.0


def test_profil_beeinflusst_erwartungswerte():
    # Ohne Profil greift die Grundlast, mit Profil die gelernte Last — die
    # erwartete Rest-Energie unterscheidet sich (der Profilzweig wird genutzt).
    ohne = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-1500))
    mit = P.compute_plan(_mit_profil())
    assert ohne.ueberschuss_rest_kwh != mit.ueberschuss_rest_kwh


def test_profil_wird_nach_ortszeit_gelesen():
    # Das Profil ist nach Wanduhr gelernt (22.09.2026). Eine Last, die nur um
    # 19 Uhr Ortszeit anliegt, muss bei UTC+2 um 17 Uhr UTC erwartet werden.
    from datetime import timedelta

    from factories import lokal
    from hems.strategies.demand import _expected_load_w

    inp = plan_input(socs=[60, 60, 60], saldo_w=-1500)
    inp.load_profile_w = {(d, h): 100.0 for d in (0, 1) for h in range(24)}
    inp.load_profile_w[(0, 19)] = 2000.0
    inp.load_profile_w[(1, 19)] = 2000.0
    assert _expected_load_w(inp, lokal(19)) == 2000.0
    assert _expected_load_w(inp, lokal(19) + timedelta(hours=2)) == 100.0


def test_speicher_ohne_soc_zaehlt_nicht_in_die_planbare_kapazitaet():
    # Fällt ein SoC-Sensor kurz aus, darf der Gesamt-SoC nicht um dessen
    # Anteil einbrechen: Stand und Kapazität beschreiben dieselben Speicher.
    from factories import storage

    voll = P.compute_plan(plan_input(
        saldo_w=0, storage_states=[storage("L1", 60), storage("L2", 60)]
    ))
    halb = P.compute_plan(plan_input(
        saldo_w=0, storage_states=[storage("L1", 60), storage("L2", None)]
    ))
    assert voll.speicher_soc == halb.speicher_soc == 60.0
    assert halb.speicher_kapazitaet_kwh == voll.speicher_kapazitaet_kwh
