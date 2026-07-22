"""Bedarfsmodell (strategies/demand.py) mit WP-Modell, gelerntem Lastprofil und
Temperaturvorhersage.

Regressionswächter für den Domänen-Refactor: diese Eingaben aktivieren
`_wp_expected_w`, `_forecast_temp_at` und den Profilzweig von `_expected_load_w`
— Pfade, die die übrigen Tests (ohne wp_model/load_profile_w/temp_forecast_c)
nie durchlaufen. Ein beim Move verlorener Import fiele erst hier auf.
"""
from __future__ import annotations

from datetime import timedelta

from factories import NOON, plan_input
from hems import planner as P
from hems.strategies.types import WpModel


def _mit_modell():
    inp = plan_input(socs=[60, 60, 60], saldo_w=-1500)
    inp.wp_model = WpModel(base_w=300.0, k_w_per_k=80.0, limit_c=17.0, max_w=3000.0)
    inp.load_profile_w = {(0, h): 500.0 for h in range(24)}
    inp.load_profile_w.update({(1, h): 450.0 for h in range(24)})
    inp.temp_forecast_c = {
        NOON.replace(minute=0, second=0, microsecond=0) + timedelta(hours=k): 2.0
        for k in range(-14, 26)
    }
    return inp


def test_compute_plan_mit_wp_modell_laeuft_durch():
    # Deckt _wp_expected_w / _forecast_temp_at / _expected_load_w (Profilzweig)
    # ab — kein NameError, plausibler Plan.
    r = P.compute_plan(_mit_modell())
    assert r.regelung is not None
    assert r.soc_prognose  # nutzt _total_load_w -> _expected_load_w + _wp_expected_w
    assert r.ueberschuss_rest_kwh >= 0.0


def test_profil_beeinflusst_erwartungswerte():
    # Ohne Profil greift die Grundlast, mit Profil die gelernte Last — die
    # erwartete Rest-Energie unterscheidet sich (der Profilzweig wird genutzt).
    ohne = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-1500))
    mit = P.compute_plan(_mit_modell())
    assert ohne.ueberschuss_rest_kwh != mit.ueberschuss_rest_kwh
