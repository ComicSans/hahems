"""Charakterisierung der Warmwasser-Empfehlung.

WW ist in compute_plan/_priorities verwoben (Sperre, Legionellen, PV-Boost,
Basis/Komfort-Latches, Sollwert). Diese Tests nageln das Verhalten fest, bevor
die Logik nach strategies/water.py extrahiert wird (Schritt 3).
"""
from __future__ import annotations

from factories import NOON, plan_input
from hems import planner as P
from hems.strategies.types import PlanResult


def _ww(**kw) -> PlanResult:
    return P.compute_plan(plan_input(**kw))


def test_kein_geraet_kein_sollwert():
    r = _ww(thermal_present=False, thermal_temp=None)
    assert r.ww_soll_c is None
    assert r.ww_status == ""


def test_kaltes_wasser_basisladung():
    r = _ww(thermal_present=True, thermal_temp=40.0)
    assert r.ww_soll_c == 48.0
    assert r.ww_status == "basis"
    assert r.flags.ww_basis is True


def test_sperrzeit_schaltet_aus():
    sperr = [(NOON.replace(hour=10), NOON.replace(hour=12))]
    r = _ww(thermal_present=True, thermal_temp=40.0, thermal_block_windows=sperr)
    assert r.ww_gesperrt is True
    assert r.ww_soll_c is None
    assert r.ww_status == "aus"


def test_legionellenschutz_hat_vorrang():
    leg = [(NOON.replace(hour=10), NOON.replace(hour=12))]
    r = _ww(thermal_present=True, thermal_temp=40.0, thermal_legionella_windows=leg)
    assert r.ww_legionelle_aktiv is True
    assert r.ww_soll_c == 60.0
    assert r.ww_status == "legionellenschutz"


def test_pv_boost_auf_komfort():
    # Speicher fast voll (90 %) + kräftige Einspeisung + Temperatur unter Komfort.
    r = _ww(thermal_present=True, thermal_temp=55.0, socs=[90, 90, 90], saldo_w=-3000)
    assert r.flags.ww_boost_soc is True
    assert r.flags.ww_boost_saldo is True
    assert r.ww_soll_c == 60.0
    assert r.ww_status == "pv_boost"


def test_warm_bleibt_basis_ohne_boost():
    # Über Komfort, aber ohne Boost-Bedingungen -> Basis-Sollwert, Flags aus.
    r = _ww(thermal_present=True, thermal_temp=62.0)
    assert r.ww_status == "basis"
    assert r.flags.ww_komfort is False
