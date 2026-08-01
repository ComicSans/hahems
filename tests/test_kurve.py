"""Übernahme der gemessenen Heizkurve.

Der Teil mit dem größten Schadenspotenzial im ganzen Heizkreis: Die Empfehlung
entsteht aus Betrieb, den HEMS mit der vorigen Empfehlung selbst erzeugt hat.
Ohne Dämpfung senkt sich die Kurve Schritt für Schritt, und jeder Schritt sieht
für sich genommen begründet aus. Getestet wird deshalb vor allem, dass die drei
Bremsen greifen — nicht nur, dass eine Übernahme überhaupt stattfindet.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import factories as f
from hems.const import (
    DATENBASIS_BELASTBAR,
    KURVE_FUSSPUNKT_MAX_C,
    KURVE_FUSSPUNKT_MIN_C,
    KURVE_MIN_ABSTAND_H,
)
from hems.strategies import types as P
from hems.strategies.kurve import (
    QUELLE_EMPFEHLUNG,
    QUELLE_KONFIGURIERT,
    QUELLE_WARTET,
    kurven_wahl,
)


def _lauf(heizkreis: P.HeatingState, flags: P.PlanFlags | None = None, *, now=f.NOON):
    inp = f.plan_input(heating_state=heizkreis, now=now)
    if flags is not None:
        inp = replace(inp, flags=flags)
    res = P.PlanResult()
    return kurven_wahl(inp, res), res


def _empfohlen(**kw) -> P.HeatingState:
    grund = dict(
        curve_base_c=40.0,
        curve_slope=0.8,
        vlt_min_c=28.0,
        curve_from_analysis=True,
        empfehlung_fusspunkt_c=34.0,
        empfehlung_steilheit=0.6,
        empfehlung_vorlauf_min_c=26.0,
        empfehlung_datenbasis=DATENBASIS_BELASTBAR,
    )
    grund.update(kw)
    return f.heating(**grund)


# --- Der Normalfall: ausgeschaltet -------------------------------------------


def test_ohne_schalter_gilt_die_konfigurierte_kurve():
    wahl, res = _lauf(f.heating(curve_base_c=40.0, curve_slope=0.8))
    assert (wahl.fusspunkt_c, wahl.steilheit) == (40.0, 0.8)
    assert wahl.quelle == QUELLE_KONFIGURIERT
    # Nichts in den Flags: ein ausgeschalteter Schalter darf keinen Zustand
    # hinterlassen, der beim Wiedereinschalten sofort wieder greift.
    assert res.flags.kurve_fusspunkt_c is None
    assert res.flags.kurve_uebernommen_am is None


def test_ausschalten_gibt_die_konfigurierte_kurve_zurueck():
    vorher = P.PlanFlags(
        kurve_fusspunkt_c=34.0,
        kurve_steilheit=0.6,
        kurve_vorlauf_min_c=26.0,
        kurve_uebernommen_am=f.NOON - timedelta(days=3),
    )
    wahl, res = _lauf(_empfohlen(curve_from_analysis=False), vorher)
    assert (wahl.fusspunkt_c, wahl.steilheit) == (40.0, 0.8)
    assert res.flags.kurve_fusspunkt_c is None


# --- Bremse 1: Datenbasis -----------------------------------------------------


def test_ohne_belastbare_datenbasis_wird_nichts_uebernommen():
    wahl, res = _lauf(_empfohlen(empfehlung_datenbasis="vorlaeufig"))
    assert (wahl.fusspunkt_c, wahl.steilheit) == (40.0, 0.8)
    assert wahl.quelle == QUELLE_WARTET
    assert "vorlaeufig" in wahl.grund
    assert res.flags.kurve_uebernommen_am is None


def test_ohne_analyse_wartet_die_uebernahme_und_sagt_es():
    wahl, _ = _lauf(
        _empfehlen := _empfohlen(
            empfehlung_datenbasis=None,
            empfehlung_fusspunkt_c=None,
            empfehlung_steilheit=None,
        )
    )
    assert wahl.quelle == QUELLE_WARTET
    assert "Keine Wärmepumpen-Analyse" in wahl.grund


def test_eine_uebernommene_kurve_bleibt_wenn_die_datenbasis_abfaellt():
    """Sonst wäre der Rücksprung eine zweite Änderung ohne neue Erkenntnis.

    Und zwar genau dann, wenn die Messkette gerade ausgefallen ist — also in
    dem Moment, in dem am wenigsten über den richtigen Wert bekannt ist.
    """
    vorher = P.PlanFlags(
        kurve_fusspunkt_c=34.0,
        kurve_steilheit=0.6,
        kurve_vorlauf_min_c=26.0,
        kurve_uebernommen_am=f.NOON - timedelta(days=3),
    )
    wahl, res = _lauf(_empfohlen(empfehlung_datenbasis="keine_daten"), vorher)
    assert (wahl.fusspunkt_c, wahl.steilheit) == (34.0, 0.6)
    assert wahl.quelle == QUELLE_EMPFEHLUNG
    assert res.flags.kurve_uebernommen_am == vorher.kurve_uebernommen_am


# --- Bremse 2: Tagesabstand ---------------------------------------------------


def test_erste_uebernahme_greift_sofort():
    wahl, res = _lauf(_empfohlen())
    assert (wahl.fusspunkt_c, wahl.steilheit, wahl.vorlauf_min_c) == (34.0, 0.6, 26.0)
    assert wahl.quelle == QUELLE_EMPFEHLUNG
    assert res.flags.kurve_uebernommen_am == f.NOON


def test_innerhalb_eines_tages_wird_nicht_erneut_uebernommen():
    vorher = P.PlanFlags(
        kurve_fusspunkt_c=34.0,
        kurve_steilheit=0.6,
        kurve_vorlauf_min_c=26.0,
        kurve_uebernommen_am=f.NOON - timedelta(hours=KURVE_MIN_ABSTAND_H - 1),
    )
    wahl, res = _lauf(_empfohlen(empfehlung_fusspunkt_c=30.0), vorher)
    assert wahl.fusspunkt_c == 34.0
    assert "frühestens" in wahl.grund
    assert res.flags.kurve_uebernommen_am == vorher.kurve_uebernommen_am


def test_nach_einem_tag_wird_erneut_uebernommen():
    vorher = P.PlanFlags(
        kurve_fusspunkt_c=34.0,
        kurve_steilheit=0.6,
        kurve_vorlauf_min_c=26.0,
        kurve_uebernommen_am=f.NOON - timedelta(hours=KURVE_MIN_ABSTAND_H),
    )
    wahl, res = _lauf(_empfohlen(empfehlung_fusspunkt_c=30.0), vorher)
    assert wahl.fusspunkt_c == 30.0
    assert res.flags.kurve_uebernommen_am == f.NOON


# --- Bremse 3: Mindeständerung ------------------------------------------------


def test_eine_zu_kleine_abweichung_aendert_nichts():
    """Unter einem Kelvin löst die Anlage den Unterschied gar nicht auf.

    Ohne diese Schwelle liefe der Tagesrhythmus dauerhaft mit, ohne je etwas
    zu bewirken — und jede Übernahme setzte die 24 Stunden neu, sodass eine
    echte Änderung länger warten müsste als nötig.
    """
    stand = f.NOON - timedelta(days=2)
    vorher = P.PlanFlags(
        kurve_fusspunkt_c=34.0,
        kurve_steilheit=0.6,
        kurve_vorlauf_min_c=26.0,
        kurve_uebernommen_am=stand,
    )
    wahl, res = _lauf(
        _empfohlen(empfehlung_fusspunkt_c=34.4, empfehlung_steilheit=0.62), vorher
    )
    assert (wahl.fusspunkt_c, wahl.steilheit) == (34.0, 0.6)
    assert "zu wenig" in wahl.grund
    assert res.flags.kurve_uebernommen_am == stand


def test_eine_kleine_aenderung_allein_an_der_steilheit_reicht():
    vorher = P.PlanFlags(
        kurve_fusspunkt_c=34.0,
        kurve_steilheit=0.6,
        kurve_vorlauf_min_c=26.0,
        kurve_uebernommen_am=f.NOON - timedelta(days=2),
    )
    wahl, _ = _lauf(
        _empfohlen(empfehlung_fusspunkt_c=34.2, empfehlung_steilheit=0.75), vorher
    )
    assert wahl.steilheit == 0.75


# --- Grenzen ------------------------------------------------------------------


def test_absurde_empfehlungen_werden_begrenzt_statt_verworfen():
    """Eine Regression über wenige Wochen kann Unsinn liefern.

    Die Datenbasis merkt das nicht — sie misst die Länge der Beobachtung, nicht
    die Plausibilität des Ergebnisses. Begrenzt auf denselben Bereich, den der
    Konfigurationsdialog zulässt.
    """
    wahl, _ = _lauf(_empfohlen(empfehlung_fusspunkt_c=180.0, empfehlung_steilheit=-4.0))
    assert wahl.fusspunkt_c == KURVE_FUSSPUNKT_MAX_C
    assert wahl.steilheit == 0.0

    wahl, _ = _lauf(_empfohlen(empfehlung_fusspunkt_c=-20.0))
    assert wahl.fusspunkt_c == KURVE_FUSSPUNKT_MIN_C


def test_vorlauf_minimum_bleibt_unter_dem_maximum():
    """Sonst stünde die Untergrenze über der Obergrenze und der Vorlauf fest."""
    wahl, _ = _lauf(_empfohlen(empfehlung_vorlauf_min_c=60.0, vlt_max_c=45.0))
    assert wahl.vorlauf_min_c == 45.0


def test_ohne_eigene_empfehlung_bleibt_das_konfigurierte_minimum():
    wahl, _ = _lauf(_empfohlen(empfehlung_vorlauf_min_c=None, vlt_min_c=28.0))
    assert wahl.vorlauf_min_c == 28.0


def test_mehrere_analysen_uebernehmen_nichts_und_sagen_warum():
    wahl, res = _lauf(_empfohlen(empfehlung_mehrdeutig=True))
    assert (wahl.fusspunkt_c, wahl.steilheit) == (40.0, 0.8)
    assert "Mehrere" in wahl.grund
    assert res.flags.kurve_uebernommen_am is None


# --- Zusammenspiel mit dem Heizkreis -----------------------------------------


def test_der_vorlauf_folgt_der_uebernommenen_kurve():
    """Der eigentliche Zweck: die Übernahme muss am Sollwert ankommen."""
    from hems.planner import compute_plan

    ohne = compute_plan(
        f.plan_input(heating_state=_empfohlen(curve_from_analysis=False), now=f.NOON)
    )
    mit = compute_plan(f.plan_input(heating_state=_empfohlen(), now=f.NOON))
    # 5 °C außen, 50 % Anforderung (+2,5 K): 38,5 gegen 33,5, beide auf ganze
    # Grad gerundet — Python rundet die halbe Stelle zur geraden Zahl.
    assert ohne.heizung.vlt_ziel_c == 38.0
    assert mit.heizung.vlt_ziel_c == 34.0
    # 4,5 K weniger Vorlauf vor der Rundung: die Übernahme kommt an.
    assert mit.heizung.vlt_ziel_c < ohne.heizung.vlt_ziel_c
    assert mit.heizung.kurve_quelle == QUELLE_EMPFEHLUNG
    assert mit.heizung.kurve_fusspunkt_c == 34.0


def test_die_herkunft_steht_am_ergebnis():
    from hems.planner import compute_plan

    plan = compute_plan(
        f.plan_input(heating_state=_empfohlen(empfehlung_datenbasis="unzureichend"))
    )
    assert plan.heizung.kurve_quelle == QUELLE_WARTET
    assert plan.heizung.kurve_fusspunkt_c == 40.0


def test_datenbasis_belastbar_heisst_in_der_analyse_dasselbe():
    """`strategies/` importiert nichts aus `waermepumpe/` — die Zeichenkette
    steht deshalb zweimal. Läuft sie auseinander, übernähme HEMS nie wieder
    eine Kurve, ohne dass irgendetwas fehlschlüge."""
    from hems.waermepumpe.analysis import types as analyse_types

    assert DATENBASIS_BELASTBAR == analyse_types.DATENBASIS_BELASTBAR
