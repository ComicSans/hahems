"""Zielwerte zu den Hinweisen.

Ein Hinweis ohne Zahl lässt offen, was zu tun ist. Die Zahl bezieht sich
allerdings auf den **Volumenstrom** und nicht auf die Pumpenstufe: eine
Umwälzpumpe fördert nicht linear zu ihrer Prozentanzeige, und ihre Kennlinie
ist hier nicht bekannt.
"""
from __future__ import annotations

import pytest
from conftest import PRESET_DIR
from hems.waermepumpe.analysis import evaluate, hints, presets, thermal
from hems.waermepumpe.analysis.types import BETRIEB_HEIZEN, HinweisZustand, Messwert

ZIEL_K = 5.0


def _preset():
    return presets.lade_presets(PRESET_DIR)["lg-therma-v-r32-split-5-7-9"]


def test_auslegungsspreizung_ist_der_bezug():
    # Derselbe Wert, aus dem der Nennvolumenstrom abgeleitet wurde.
    assert _preset().spreizung_ziel_k == pytest.approx(ZIEL_K)


def test_zu_kleine_spreizung_heisst_drosseln():
    # 4 K statt 5 K: der Volumenstrom liegt 25 % über dem Ziel — genau die
    # Aussage „drosseln, 25 % zu viel".
    assert thermal.durchfluss_ziel_prozent(4.0, ZIEL_K) == pytest.approx(80.0)
    assert thermal.durchfluss_abweichung_prozent(4.0, ZIEL_K) == pytest.approx(25.0)


def test_halbe_spreizung_heisst_halber_volumenstrom():
    assert thermal.durchfluss_ziel_prozent(2.5, ZIEL_K) == pytest.approx(50.0)
    assert thermal.durchfluss_abweichung_prozent(2.5, ZIEL_K) == pytest.approx(100.0)


def test_zu_grosse_spreizung_heisst_mehr_volumenstrom():
    # 8 K: es kommt zu wenig durch, die Abweichung ist negativ.
    assert thermal.durchfluss_ziel_prozent(8.0, ZIEL_K) == pytest.approx(160.0)
    assert thermal.durchfluss_abweichung_prozent(8.0, ZIEL_K) == pytest.approx(
        -37.5
    )


def test_passende_spreizung_ergibt_keine_abweichung():
    assert thermal.durchfluss_ziel_prozent(5.0, ZIEL_K) == pytest.approx(100.0)
    assert thermal.durchfluss_abweichung_prozent(5.0, ZIEL_K) == pytest.approx(0.0)


def test_ohne_spreizung_kein_zielwert():
    assert thermal.durchfluss_ziel_prozent(None, ZIEL_K) is None
    assert thermal.durchfluss_ziel_prozent(0.0, ZIEL_K) is None
    assert thermal.durchfluss_abweichung_prozent(-1.0, ZIEL_K) is None


def _eingang(bild: hints.Tagesbild, zustand: HinweisZustand | None = None):
    return evaluate.AnalyseEingang(
        messwert=Messwert(
            ts=0.0,
            vorlauf_c=35.0,
            ruecklauf_c=30.0,
            p_el_w=1400.0,
            t_aussen_c=5.0,
            betrieb=BETRIEB_HEIZEN,
        ),
        preset=_preset(),
        tagesbild=bild,
        hinweise=zustand or HinweisZustand(),
    )


def test_analyse_liefert_den_zielwert_zum_hinweis():
    a = evaluate.analysiere(_eingang(hints.Tagesbild(spreizung_mittel_k=4.0)))
    assert a.hinweise.spreizung_niedrig is False  # 4 K liegt noch unter der Schwelle
    assert a.durchfluss_ziel_prozent == pytest.approx(80.0)
    assert a.durchfluss_abweichung_prozent == pytest.approx(25.0)


def test_zielwert_bei_aktivem_pumpenhinweis():
    a = evaluate.analysiere(_eingang(hints.Tagesbild(spreizung_mittel_k=2.5)))
    assert a.hinweise.spreizung_niedrig
    assert a.durchfluss_abweichung_prozent == pytest.approx(100.0)


def test_kein_zielwert_wenn_die_temperaturen_identisch_sind():
    # Zwei Sensoren auf derselben Quelle: jeder Zielwert wäre Unsinn, und ein
    # Vorschlag „Volumenstrom halbieren" wäre aus einem Messfehler abgeleitet.
    a = evaluate.analysiere(
        _eingang(
            hints.Tagesbild(spreizung_mittel_k=0.0, anteil_spreizung_null=0.98)
        )
    )
    assert a.hinweise.temperaturen_identisch
    assert a.durchfluss_ziel_prozent is None
    assert a.durchfluss_abweichung_prozent is None


def test_zielwert_folgt_dem_tagesmittel_nicht_dem_momentanwert():
    # Der Messwert hat 5 K Spreizung, das Tagesmittel 2,5 K. Maßgeblich ist
    # das Mittel — ein Ziel, das im Abfragetakt springt, ist unbrauchbar.
    a = evaluate.analysiere(_eingang(hints.Tagesbild(spreizung_mittel_k=2.5)))
    assert a.spreizung_k == pytest.approx(5.0)
    assert a.durchfluss_ziel_prozent == pytest.approx(50.0)
