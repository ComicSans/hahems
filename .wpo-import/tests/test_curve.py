"""Waermeverlust und Heizkurvenvorschlag."""
from __future__ import annotations

import pytest
from analysis import curve
from analysis.types import (
    DATENBASIS_BELASTBAR,
    DATENBASIS_KEINE,
    DATENBASIS_UNZUREICHEND,
    DATENBASIS_VORLAEUFIG,
)


def _verlust_punkte(n: int = 400, w_pro_k: float = 250.0, grenze: float = 15.0):
    """Synthetischer Heizbetrieb: Leistung faellt linear bis zur Heizgrenze."""
    return [
        (t, max(0.0, (grenze - t) * w_pro_k))
        for t in (-10.0 + i * 0.05 for i in range(n))
    ]


def _kurven_punkte(n: int, steilheit: float = 0.8, fuss: float = 24.0):
    return [
        (t, fuss + (15.0 - t) * steilheit)
        for t in (-10.0 + i * 0.05 for i in range(n))
    ]


def test_waermeverlust_findet_koeffizient_und_heizgrenze():
    ergebnis = curve.waermeverlust(_verlust_punkte())
    assert ergebnis is not None
    w_pro_k, grenze = ergebnis
    assert w_pro_k == pytest.approx(250.0, rel=0.05)
    assert grenze == pytest.approx(15.0, abs=1.0)


def test_reine_sommerdaten_ergeben_keinen_waermeverlust():
    # Ohne fallende Kennlinie ist kein Heizverhalten erkennbar.
    assert curve.waermeverlust([(20.0, 0.0), (25.0, 0.0), (30.0, 0.0)]) is None


def test_zu_wenige_punkte_ergeben_keine_empfehlung():
    leer = curve.empfiehl_kurve([])
    assert leer.datenbasis == DATENBASIS_KEINE
    assert leer.fusspunkt_c is None

    duenn = curve.empfiehl_kurve(_kurven_punkte(10))
    assert duenn.datenbasis == DATENBASIS_UNZUREICHEND


def test_datenbasis_waechst_mit_der_beobachtungsdauer():
    assert curve.empfiehl_kurve(_kurven_punkte(100)).datenbasis == DATENBASIS_VORLAEUFIG
    assert curve.empfiehl_kurve(_kurven_punkte(400)).datenbasis == DATENBASIS_BELASTBAR


def test_empfehlung_bildet_den_beobachteten_betrieb_ab():
    vorschlag = curve.empfiehl_kurve(_kurven_punkte(400, steilheit=0.8, fuss=24.0), 15.0)
    assert vorschlag.steilheit == pytest.approx(0.8, abs=0.05)
    # Fusspunkt an der Heizgrenze plus Sicherheitsreserve.
    assert vorschlag.fusspunkt_c == pytest.approx(24.0 + curve.RESERVE_K, abs=0.5)
    assert vorschlag.heizgrenze_c == pytest.approx(15.0)


def test_steigender_vorlauf_bei_waermerem_wetter_ergibt_keine_empfehlung():
    # Das ist keine witterungsgefuehrte Kurve; daraus etwas abzuleiten waere
    # geraten.
    punkte = [(t, 20.0 + t) for t in (-10.0 + i * 0.05 for i in range(400))]
    assert curve.empfiehl_kurve(punkte).datenbasis == DATENBASIS_UNZUREICHEND


def test_unsinnig_hoher_fusspunkt_wird_nicht_empfohlen():
    punkte = _kurven_punkte(400, steilheit=0.8, fuss=90.0)
    assert curve.empfiehl_kurve(punkte, 15.0).fusspunkt_c is None
