"""Charakterisierung der Heizkreis-Empfehlung (`_heating_plan`).

Nagelt das Verhalten fest, bevor die Logik nach strategies/heating.py verschoben
wird (Schritt 3, reiner Move).
"""
from __future__ import annotations

from factories import heating, plan_input
from hems import planner as P
from hems.strategies.types import HeatingResult


def _hz(**kw) -> HeatingResult:
    return P.compute_plan(plan_input(thermal_present=False, **kw)).heizung


def test_heizen_witterungsgefuehrt():
    r = _hz(heating_state=heating(outdoor_temp_c=5.0, demand_pct=50.0))
    assert r.modus == "heizen"
    assert r.vlt_ziel_c == 38.0
    assert r.t_aussen_c == 5.0


def test_aus_bei_milder_temperatur():
    r = _hz(heating_state=heating(outdoor_temp_c=20.0))
    assert r.modus == "aus"
    assert r.vlt_ziel_c is None


def test_kuehlen_ueber_schwelle():
    r = _hz(heating_state=heating(outdoor_temp_c=28.0))
    assert r.modus == "kuehlen"
    assert r.vlt_ziel_c == 18.0


def test_sommersperre_kein_heizen():
    r = _hz(heating_state=heating(outdoor_temp_c=5.0, heat_locked=True))
    assert r.modus == "aus"
    assert r.sommer_sperre is True


def test_absenkbetrieb_ohne_anforderung():
    r = _hz(heating_state=heating(outdoor_temp_c=5.0, demand_pct=0.0))
    assert r.modus == "heizen"
    assert r.vlt_ziel_c == 28.0  # Vorlauf-Minimum
    assert r.leise_empfohlen is True


def test_unbekannt_ohne_aussentemperatur():
    r = _hz(heating_state=heating(outdoor_temp_c=None))
    assert r.modus == "unbekannt"
