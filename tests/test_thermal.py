"""Thermische Leistung, COP und Gueltigkeitspruefung."""
from __future__ import annotations

import pytest
from analysis import thermal
from analysis.types import (
    BETRIEB_ABTAUEN,
    BETRIEB_HEIZEN,
    BETRIEB_WARMWASSER,
    GRUND_ABTAUEN,
    GRUND_KEINE_LEISTUNG,
    GRUND_OK,
    GRUND_SPREIZUNG_ZU_KLEIN,
    GRUND_WARMWASSER,
    Messwert,
    Preset,
)


def _preset(**kw) -> Preset:
    grund = dict(
        schluessel="test",
        anzeigename="Test",
        quelle="test",
        p1=0.0,
        p2=-0.1,
        p3=8.0,
        p4=0.19,
        modellfehler_prozent=15.0,
    )
    grund.update(kw)
    return Preset(**grund)


def _messwert(**kw) -> Messwert:
    grund = dict(
        ts=0.0,
        vorlauf_c=35.0,
        ruecklauf_c=30.0,
        durchfluss_lh=1000.0,
        p_el_w=1500.0,
        t_aussen_c=5.0,
        betrieb=BETRIEB_HEIZEN,
    )
    grund.update(kw)
    return Messwert(**grund)


def test_waermeleistung_liefert_watt_nicht_kilowatt():
    # Der Fehler, der das ganze Feature wertlos machen wuerde: 1000 l/h bei
    # 5 K sind 5815 W, nicht 5,8 W und nicht 5815 kW.
    assert thermal.waermeleistung_w(1000.0, 5.0) == pytest.approx(5815.0)


def test_spreizung_ist_vorlauf_minus_ruecklauf():
    assert thermal.spreizung_k(35.0, 30.0) == pytest.approx(5.0)
    assert thermal.spreizung_k(None, 30.0) is None


def test_cop_ohne_stromaufnahme_ist_none():
    assert thermal.cop(5000.0, 0.0) is None
    assert thermal.cop(5000.0, 1000.0) == pytest.approx(5.0)


def test_gueltiger_heizbetrieb_wird_angenommen():
    assert thermal.bewerte(_messwert(), _preset()).grund == GRUND_OK


def test_zu_kleine_spreizung_wird_verworfen():
    # Unter der Mindestspreizung dominiert das Rauschen der Fuehler.
    guete = thermal.bewerte(_messwert(ruecklauf_c=34.0), _preset())
    assert not guete.gueltig
    assert guete.grund == GRUND_SPREIZUNG_ZU_KLEIN


def test_warmwasser_und_abtauen_zaehlen_nicht_als_heizleistung():
    assert thermal.bewerte(_messwert(betrieb=BETRIEB_WARMWASSER), _preset()).grund == (
        GRUND_WARMWASSER
    )
    assert thermal.bewerte(_messwert(betrieb=BETRIEB_ABTAUEN), _preset()).grund == (
        GRUND_ABTAUEN
    )


def test_anlaufsockel_ohne_verdichter_wird_verworfen():
    # Regelung und Umwaelzpumpe allein ziehen rund 200 W. Ein COP daraus
    # waere frei erfunden.
    guete = thermal.bewerte(_messwert(p_el_w=120.0), _preset())
    assert not guete.gueltig
    assert guete.grund == GRUND_KEINE_LEISTUNG


def test_unplausibel_hoher_cop_wird_verworfen():
    # Deutet auf einen Einheitenfehler hin, nicht auf eine gute Waermepumpe.
    guete = thermal.bewerte(_messwert(p_el_w=200.0, durchfluss_lh=3000.0), _preset())
    assert not guete.gueltig
