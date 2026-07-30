"""Geschätzter Volumenstrom und seine Folgen für die Datenbasis.

Die Anlage, für die das gebaut wurde, hat keinen Volumenstromzähler — und der
Fall ist verbreitet. Er darf funktionieren, aber er darf nicht wie eine
Messung aussehen.
"""
from __future__ import annotations

import pytest
from analysis import evaluate, presets, thermal
from analysis.types import (
    BETRIEB_HEIZEN,
    DATENBASIS_BELASTBAR,
    DATENBASIS_VORLAEUFIG,
    GRUND_KEIN_DURCHFLUSS,
    GRUND_KEINE_LEISTUNG,
    GRUND_OK,
    Messwert,
)
from conftest import PRESET_DIR

LG = "lg-therma-v-r32-split-5-7-9"


def _preset(schluessel: str = LG):
    return presets.lade_presets(PRESET_DIR)[schluessel]


def _messwert(**kw) -> Messwert:
    grund = dict(
        ts=0.0,
        vorlauf_c=35.0,
        ruecklauf_c=30.0,
        p_el_w=1400.0,
        t_aussen_c=5.0,
        betrieb=BETRIEB_HEIZEN,
    )
    grund.update(kw)
    return Messwert(**grund)


def test_lg_presets_tragen_einen_nennvolumenstrom():
    # Abgeleitet aus der Nennwärmeleistung bei 5 K Auslegungsspreizung.
    p = _preset()
    assert p.durchfluss_nominal_lh == pytest.approx(911.0)


def test_gemessener_durchfluss_hat_vorrang():
    fluss, geschaetzt = thermal.durchfluss_effektiv(
        _messwert(durchfluss_lh=1200.0), _preset()
    )
    assert fluss == pytest.approx(1200.0)
    assert not geschaetzt


def test_ohne_zaehler_tritt_der_nennwert_ein():
    fluss, geschaetzt = thermal.durchfluss_effektiv(_messwert(), _preset())
    assert fluss == pytest.approx(911.0)
    assert geschaetzt


def test_generisches_preset_hat_keinen_nennwert():
    # Bei unbekanntem Gerät wäre eine Nennmenge frei erfunden.
    p = _preset("generisch-luft-wasser-mittel")
    assert p.durchfluss_nominal_lh is None
    fluss, geschaetzt = thermal.durchfluss_effektiv(_messwert(), p)
    assert fluss is None and not geschaetzt
    assert thermal.bewerte(_messwert(), p).grund == GRUND_KEIN_DURCHFLUSS


def test_geschaetzter_durchfluss_deckelt_die_datenbasis():
    a = evaluate.analysiere(
        evaluate.AnalyseEingang(messwert=_messwert(), preset=_preset())
    )
    assert a.verwerfungsgrund == GRUND_OK
    assert a.cop_momentan is not None
    assert a.durchfluss_geschaetzt
    # Nie belastbar: der COP hängt linear am angenommenen Volumenstrom.
    assert a.datenbasis == DATENBASIS_VORLAEUFIG


def test_gemessener_durchfluss_wird_nicht_gedeckelt():
    a = evaluate.analysiere(
        evaluate.AnalyseEingang(
            messwert=_messwert(durchfluss_lh=900.0), preset=_preset()
        )
    )
    assert not a.durchfluss_geschaetzt
    assert a.datenbasis == DATENBASIS_BELASTBAR


def test_standby_schwelle_kommt_aus_dem_preset():
    # Gemessener Sockel der Anlage rund 216 W; das Preset führt 220 W. Ein
    # fest verdrahteter Wert von 150 W hätte den Sockel als Verdichterbetrieb
    # durchgelassen und einen erfundenen COP erzeugt.
    p = _preset()
    assert p.standby_w == pytest.approx(220.0)
    assert thermal.bewerte(_messwert(p_el_w=216.0), p).grund == GRUND_KEINE_LEISTUNG
    assert thermal.bewerte(_messwert(p_el_w=1400.0), p).grund == GRUND_OK
