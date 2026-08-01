"""Auswertelauf von Ende zu Ende."""
from __future__ import annotations

import pytest
from conftest import PRESET_DIR
from hems.waermepumpe.analysis import evaluate, hints, presets
from hems.waermepumpe.analysis.types import (
    BETRIEB_HEIZEN,
    DATENBASIS_BELASTBAR,
    DATENBASIS_UNZUREICHEND,
    DATENBASIS_VORLAEUFIG,
    GRUND_OK,
    GRUND_SPREIZUNG_ZU_KLEIN,
    Messwert,
    TaktZustand,
)


def _preset(schluessel: str = "lg-therma-v-r32-split-5-7-9"):
    return presets.lade_presets(PRESET_DIR)[schluessel]


def _eingang(**kw) -> evaluate.AnalyseEingang:
    messwert = kw.pop(
        "messwert",
        Messwert(
            ts=0.0,
            vorlauf_c=35.0,
            ruecklauf_c=30.0,
            durchfluss_lh=900.0,
            p_el_w=1400.0,
            t_aussen_c=5.0,
            betrieb=BETRIEB_HEIZEN,
        ),
    )
    return evaluate.AnalyseEingang(
        messwert=messwert, preset=kw.pop("preset", _preset()), **kw
    )


def test_lauf_liefert_plausible_kennzahlen():
    a = evaluate.analysiere(_eingang())
    assert a.verwerfungsgrund == GRUND_OK
    assert a.spreizung_k == pytest.approx(5.0)
    # 900 l/h * 5 K * 1,163 = 5233,5 W
    assert a.waermeleistung_w == pytest.approx(5234.0, abs=1.0)
    assert a.cop_momentan == pytest.approx(3.74, abs=0.02)
    assert a.cop_soll is not None
    assert a.cop_soll_unsicherheit == pytest.approx(16.6, abs=0.1)


def test_verworfener_messwert_liefert_keinen_cop():
    a = evaluate.analysiere(
        _eingang(
            messwert=Messwert(
                ts=0.0,
                vorlauf_c=35.0,
                ruecklauf_c=34.0,
                durchfluss_lh=900.0,
                p_el_w=1400.0,
                t_aussen_c=5.0,
                betrieb=BETRIEB_HEIZEN,
            )
        )
    )
    assert a.verwerfungsgrund == GRUND_SPREIZUNG_ZU_KLEIN
    assert a.cop_momentan is None
    # Die Erwartung haengt nicht am Messwert und bleibt bestehen.
    assert a.cop_soll is not None


def test_abweichung_ist_vorzeichenrichtig():
    # Sehr schlechter Ist-Wert bei guten Bedingungen: Abweichung negativ.
    a = evaluate.analysiere(
        _eingang(
            messwert=Messwert(
                ts=0.0,
                vorlauf_c=35.0,
                ruecklauf_c=30.0,
                durchfluss_lh=400.0,
                p_el_w=2500.0,
                t_aussen_c=7.0,
                betrieb=BETRIEB_HEIZEN,
            )
        )
    )
    assert a.cop_abweichung < 0


def test_unbekannter_steuerungsgrund_wertet_ab_statt_zu_brechen():
    grund, bekannt = evaluate.bewerte_steuerung("nachtabsenkung_v2")
    assert grund == "normal"
    assert not bekannt

    a = evaluate.analysiere(
        _eingang(
            messwert=Messwert(
                ts=0.0,
                vorlauf_c=35.0,
                ruecklauf_c=30.0,
                durchfluss_lh=900.0,
                p_el_w=1400.0,
                t_aussen_c=5.0,
                betrieb=BETRIEB_HEIZEN,
                steuerung_aktiv=True,
                steuerung_grund="nachtabsenkung_v2",
            )
        )
    )
    assert a.datenbasis == DATENBASIS_UNZUREICHEND


def test_bekannte_gruende_werden_akzeptiert():
    for grund in ("normal", "pv_ueberschuss", "lastspitze", "sperre"):
        _wert, bekannt = evaluate.bewerte_steuerung(grund)
        assert bekannt, grund


def test_saubere_messung_ist_sofort_belastbar():
    # Die Guete der Messkette haengt nicht an der Beobachtungsdauer — sonst
    # saehe ein tadellos gemessener COP wochenlang wertlos aus.
    assert evaluate.analysiere(_eingang()).datenbasis == DATENBASIS_BELASTBAR


def test_fehlende_betriebsart_wertet_die_datenbasis_ab():
    # Ohne sie vermischen sich Heizen und Warmwasser in einer Kennzahl.
    ohne = evaluate.analysiere(
        _eingang(
            messwert=Messwert(
                ts=0.0,
                vorlauf_c=35.0,
                ruecklauf_c=30.0,
                durchfluss_lh=900.0,
                p_el_w=1400.0,
                t_aussen_c=5.0,
                betrieb=None,
            )
        )
    )
    assert ohne.datenbasis == DATENBASIS_VORLAEUFIG


def test_generisches_preset_wertet_die_datenbasis_ab():
    a = evaluate.analysiere(_eingang(preset=_preset("generisch-luft-wasser-mittel")))
    assert a.datenbasis == DATENBASIS_VORLAEUFIG


def test_kurvenempfehlung_hat_ihre_eigene_datenbasis():
    # Messguete und Beobachtungsdauer sind zwei verschiedene Aussagen und
    # werden getrennt gefuehrt.
    a = evaluate.analysiere(_eingang())
    assert a.datenbasis == DATENBASIS_BELASTBAR
    assert a.kurve.datenbasis == "keine_daten"

    voll = [(t, 24.0 + (15.0 - t) * 0.8) for t in (-10.0 + i * 0.05 for i in range(400))]
    b = evaluate.analysiere(_eingang(kurven_punkte=voll))
    assert b.kurve.datenbasis == DATENBASIS_BELASTBAR
    assert b.kurve.fusspunkt_c is not None


def test_datenblattvergleich_braucht_messguete_und_historie():
    # Nur saubere Messung, aber keine Historie: der Hinweis bleibt aus.
    bild = hints.Tagesbild(cop_abweichung_prozent=-40.0)
    ohne_historie = evaluate.analysiere(_eingang(tagesbild=bild))
    assert not ohne_historie.hinweise.effizienz_unter_erwartung

    voll = [(t, 24.0 + (15.0 - t) * 0.8) for t in (-10.0 + i * 0.05 for i in range(400))]
    mit_historie = evaluate.analysiere(_eingang(tagesbild=bild, kurven_punkte=voll))
    assert mit_historie.hinweise.effizienz_unter_erwartung


def test_taktzaehler_werden_ueber_mehrere_laeufe_fortgeschrieben():
    # Die Auswertung bleibt zustandslos: der Zaehlerstand geht hinein und
    # kommt fortgeschrieben wieder heraus.
    takt = None
    for i, hz in enumerate((0.0, 40.0, 40.0, 2.0, 40.0)):
        eingang = _eingang(
            messwert=Messwert(ts=i * 60.0, p_el_w=1400.0, verdichter_hz=hz)
        )
        if takt is not None:
            eingang.takt = takt
        takt = evaluate.analysiere(eingang).takt

    assert takt.starts == 2
    assert takt.laufzeit_s == pytest.approx(120.0)


def test_mittlere_laufzeit_wird_mitgeliefert():
    eingang = _eingang(messwert=Messwert(ts=0.0, verdichter_hz=40.0, p_el_w=1400.0))
    eingang.takt = TaktZustand(laeuft=True, starts=4, laufzeit_s=4800.0, letzter_ts=0.0)
    assert evaluate.analysiere(eingang).laufzeit_mittel_min == pytest.approx(20.0)


def test_datenbasis_der_empfehlung_ist_direkt_erreichbar():
    voll = [(t, 24.0 + (15.0 - t) * 0.8) for t in (-10.0 + i * 0.05 for i in range(400))]
    a = evaluate.analysiere(_eingang(kurven_punkte=voll))
    assert a.datenbasis_empfehlung == a.kurve.datenbasis == DATENBASIS_BELASTBAR


def test_leerer_eingang_stuerzt_nicht_ab():
    a = evaluate.analysiere(_eingang(messwert=Messwert(ts=0.0)))
    assert a.cop_momentan is None
    assert a.kurve.fusspunkt_c is None
