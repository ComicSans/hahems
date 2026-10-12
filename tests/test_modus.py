"""Betriebsmodus: nur noch „beobachten“ und „auto“ (T-003, Tobias 12.10.2026).

„invers-auto“ und „aus“ sind entfallen. `speicher_modus_option` ist HA-frei
und direkt testbar; die Nähte zu Coordinator und Select importieren Home
Assistant und werden wie in `test_speicher_quittung.py` über den Syntaxbaum
gelesen.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hems import const
from hems.actuation import speicher_modus_option, wiederhergestellter_modus
from hems.const import MODE_AUTO, MODE_OBSERVE, MODES_ACTUATING

BASIS = Path(__file__).resolve().parents[1] / "custom_components" / "hems"

EIN = "Eingangsmodus"
AUS = "Ausgangsmodus"


def _funktion(datei: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    baum = ast.parse((BASIS / datei).read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if (
            isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef))
            and knoten.name == name
        ):
            return knoten
    raise AssertionError(f"{name} nicht in {datei} gefunden")


def test_nur_beobachten_und_auto():
    assert MODES_ACTUATING == (MODE_AUTO,)
    assert MODE_OBSERVE not in MODES_ACTUATING
    assert not hasattr(const, "MODE_INVERS_AUTO")
    assert not hasattr(const, "MODE_OFF")


def test_select_bietet_nur_die_beiden_modi():
    quelle = (BASIS / "select.py").read_text(encoding="utf-8")
    assert "[MODE_OBSERVE, MODE_AUTO]" in quelle


def test_alte_modi_fallen_nach_neustart_auf_beobachten():
    optionen = [MODE_OBSERVE, MODE_AUTO]
    for alt in ("invers-auto", "aus", "unavailable", None):
        assert wiederhergestellter_modus(alt, optionen, MODE_OBSERVE) == MODE_OBSERVE
    assert wiederhergestellter_modus("auto", optionen, MODE_OBSERVE) == MODE_AUTO
    assert (
        wiederhergestellter_modus("beobachten", optionen, MODE_OBSERVE) == MODE_OBSERVE
    )


def test_laedt_im_eingangsmodus_und_entlaedt_im_ausgangsmodus():
    assert speicher_modus_option("laden", lade_option=EIN, entlade_option=AUS) == EIN
    assert (
        speicher_modus_option("entladen", lade_option=EIN, entlade_option=AUS) == AUS
    )


def test_pause_stellt_nichts():
    # In der Pause bleibt der zuletzt gesetzte Modus stehen, sonst flippt der
    # Select bei jedem Deadband-Durchgang.
    assert speicher_modus_option("pausiert", lade_option=EIN, entlade_option=AUS) is None


def test_ohne_vollstaendiges_optionspaar_wird_nicht_gestellt():
    assert speicher_modus_option("laden", lade_option=EIN, entlade_option=None) is None
    assert (
        speicher_modus_option("entladen", lade_option=None, entlade_option=AUS) is None
    )


def test_beide_modus_pruefungen_laufen_ueber_die_liste():
    """Schalten und Verlassen hängen an derselben Liste.

    Die zweite Prüfung (`_prev_mode`) gibt den Akku beim Wechsel nach
    „beobachten“ frei, damit er nicht mit der zuletzt kommandierten Rate blind
    weiterläuft — der Fall, für den `release_battery` existiert.
    """
    fn = _funktion("coordinator.py", "_async_update_data")
    vergleiche = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Compare)
        and isinstance(k.left, ast.Attribute)
        and k.left.attr in ("mode", "_prev_mode")
    ]
    gegen_liste = [
        k
        for k in vergleiche
        if isinstance(k.ops[0], ast.In)
        and getattr(k.comparators[0], "id", None) == "MODES_ACTUATING"
    ]
    assert len(gegen_liste) == 2, "self.mode und self._prev_mode gehören an die Liste"


def test_actuator_kennt_keinen_invers_modus_mehr():
    for datei, name in (
        ("actuator.py", "apply"),
        ("actuator.py", "_apply_battery"),
        ("actuation.py", "speicher_modus_option"),
    ):
        fn = _funktion(datei, name)
        alle = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        assert not [a.arg for a in alle if "invers" in a.arg], (datei, name)


def test_leistungs_setpoints_haengen_nur_am_regelmodus():
    """Die Sollwert-Zweige in `_apply_battery` hängen nur an `ctrl.modus`.

    Übernommen aus dem früheren Invers-Test: Der Regler schließt über die
    gemessene Speicherleistung; die Sollwerte dürfen von nichts anderem als
    dem Regelmodus abhängen.
    """
    fn = _funktion("actuator.py", "_apply_battery")
    zuweisungen = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Assign)
        and any(
            isinstance(z, ast.Tuple)
            and {getattr(e, "id", None) for e in z.elts} == {"charge_w", "discharge_w"}
            for z in k.targets
        )
    ]
    assert zuweisungen, "die Sollwert-Zweige wurden umbenannt — Test nachziehen"
    for zuw in zuweisungen:
        namen = {k.id for k in ast.walk(zuw) if isinstance(k, ast.Name)}
        assert namen <= {"charge_w", "discharge_w", "watt"}, namen
