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
