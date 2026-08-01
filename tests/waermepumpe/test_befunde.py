"""Befunde des Auswertelaufs überleben den ersten Abfragetakt.

`_auswerten` leert die Befundliste bei jedem Lauf — sonst stünde eine einmal
gemeldete fehlende Einheit für immer da, auch wenn der Sensor längst wieder
liefert. Was beim **Start** festgestellt wurde, darf davon aber nicht
mitgerissen werden: „kein Volumenstrom verdrahtet" ändert sich bis zum
nächsten Reload nicht, und die Meldung wäre nach dreißig Sekunden weg.

Genau das wäre beim Bauen passiert: `async_start` hängte die Meldung an, und
der unmittelbar folgende erste Tick löschte sie wieder. Aufgefallen ist es
beim Lesen, nicht beim Testen — deshalb dieser Test.

`runner.py` importiert Home Assistant, deshalb über den Syntaxbaum.
"""
from __future__ import annotations

import ast

from conftest import PAKET

QUELLE = PAKET / "runner.py"


def _baum() -> ast.Module:
    return ast.parse(QUELLE.read_text(encoding="utf-8"))


def _funktion(name: str):
    for knoten in ast.walk(_baum()):
        if isinstance(
            knoten, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and knoten.name == name:
            return knoten
    raise AssertionError(f"{name} nicht in runner.py gefunden")


def _zuweisungen(funktion) -> set[str]:
    """Attribute von `self`, die die Funktion überschreibt."""
    namen: set[str] = set()
    for knoten in ast.walk(funktion):
        if not isinstance(knoten, ast.Assign):
            continue
        for ziel in knoten.targets:
            if (
                isinstance(ziel, ast.Attribute)
                and isinstance(ziel.value, ast.Name)
                and ziel.value.id == "self"
            ):
                namen.add(ziel.attr)
    return namen


def test_der_abfragetakt_leert_nur_seine_eigenen_befunde() -> None:
    geleert = _zuweisungen(_funktion("_auswerten"))
    assert "_je_abfrage" in geleert, "der Tick setzt seine Liste nicht zurück"
    assert "_dauerhaft" not in geleert, (
        "der Tick überschreibt die Startbefunde — sie wären nach dem ersten "
        "Lauf weg"
    )
    assert "konfigfehler" not in geleert, (
        "konfigfehler ist die Zusammenfassung beider Listen und darf nicht "
        "direkt gesetzt werden"
    )


def test_der_start_meldet_dauerhaft_und_nicht_je_abfrage() -> None:
    gesetzt = {
        knoten.func.value.attr
        for knoten in ast.walk(_funktion("async_start"))
        if isinstance(knoten, ast.Call)
        and isinstance(knoten.func, ast.Attribute)
        and knoten.func.attr == "append"
        and isinstance(knoten.func.value, ast.Attribute)
    }
    assert gesetzt == {"_dauerhaft"}, gesetzt


def test_konfigfehler_fasst_beide_listen_zusammen() -> None:
    quelle = ast.unparse(_funktion("konfigfehler"))
    assert "_dauerhaft" in quelle and "_je_abfrage" in quelle, quelle


def test_der_fehlende_volumenstrom_wird_ueberhaupt_gemeldet() -> None:
    """Ohne diese Meldung steht die Analyse still auf `kein_durchfluss`.

    Kein COP, keine Wärmemenge, kein Wärmeverlustkoeffizient — und weder
    Datenbasis noch Hinweis zeigen darauf. Sechs der zehn Presets bringen
    keinen Nennwert mit, das ist also kein Sonderfall.
    """
    quelle = ast.unparse(_funktion("async_start"))
    assert "durchfluss_nominal_lh" in quelle, quelle
    assert "kein Volumenstrom" in quelle, quelle
