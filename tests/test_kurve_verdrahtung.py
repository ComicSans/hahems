"""Die Naht zwischen Koordinator, Analyse und Heizkreis.

`coordinator.py` importiert Home Assistant und ist für diese Suite unsichtbar.
Genau dort laufen aber zwei Dinge zusammen, die getrennt gepflegt werden:
`_kurven_empfehlung` baut ein dict, das als `**`-Splat in `HeatingState`
geht — ein Schlüssel daneben ist ein `TypeError` beim ersten Planlauf, also
erst in Home Assistant. Und es liest `analyse.kurve.…`, wo eine Umbenennung
in `analysis/types.py` still durchginge.

Beide Enden sind HA-frei importierbar, nur die Datei dazwischen nicht. Also
wird sie wie in `test_config_ws_labels.py` über den Syntaxbaum gelesen.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hems.strategies.types import HeatingState
from hems.waermepumpe.analysis.types import Analyse

QUELLE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hems"
    / "coordinator.py"
)


def _funktion(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    baum = ast.parse(QUELLE.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(
            knoten, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and knoten.name == name:
            return knoten
    raise AssertionError(f"{name} nicht in coordinator.py gefunden")


def _rueckgabe_schluessel(name: str) -> set[str]:
    """Alle Schlüssel aller dict-Literale, die die Funktion zurückgibt."""
    schluessel: set[str] = set()
    for knoten in ast.walk(_funktion(name)):
        if isinstance(knoten, ast.Return) and isinstance(knoten.value, ast.Dict):
            for k in knoten.value.keys:
                assert isinstance(k, ast.Constant), "nur Zeichenketten erwartet"
                schluessel.add(k.value)
    return schluessel


def test_kurven_empfehlung_trifft_felder_des_heizkreises() -> None:
    """Das dict geht als `**` in HeatingState — jeder Schlüssel muss passen."""
    schluessel = _rueckgabe_schluessel("_kurven_empfehlung")
    assert schluessel, "keine Rückgabeschlüssel gefunden"
    unbekannt = schluessel - set(HeatingState.__dataclass_fields__)
    assert not unbekannt, f"kein Feld in HeatingState: {sorted(unbekannt)}"


def test_kurven_empfehlung_deckt_alle_empfehlungsfelder_ab() -> None:
    """Ein neues `empfehlung_*`-Feld, das niemand befüllt, bliebe stumm.

    `empfehlung_mehrdeutig` steht im eigenen Zweig und wird deshalb
    mitgezählt, obwohl es nicht im selben dict liegt.
    """
    felder = {
        f for f in HeatingState.__dataclass_fields__ if f.startswith("empfehlung_")
    }
    assert felder - _rueckgabe_schluessel("_kurven_empfehlung") == set()


def test_zugriffe_auf_die_analyse_treffen_echte_felder() -> None:
    """`analyse.kurve.fusspunkt_c` und Geschwister gegen eine echte Analyse."""
    analyse = Analyse()
    geprueft = 0
    for knoten in ast.walk(_funktion("_kurven_empfehlung")):
        if not isinstance(knoten, ast.Attribute):
            continue
        teile: list[str] = []
        laufend: ast.AST = knoten
        while isinstance(laufend, ast.Attribute):
            teile.insert(0, laufend.attr)
            laufend = laufend.value
        if not (isinstance(laufend, ast.Name) and laufend.id == "analyse"):
            continue
        objekt = analyse
        for teil in teile:
            assert hasattr(objekt, teil), f"analyse.{'.'.join(teile)} gibt es nicht"
            objekt = getattr(objekt, teil)
        geprueft += 1
    assert geprueft >= 4, f"nur {geprueft} Zugriffe gefunden — Aufbau geändert?"


def test_gesicherte_kurve_passt_auf_die_plan_flags() -> None:
    """Was `_kurve_sichern` schreibt, muss `async_kurve_laden` wieder finden.

    Zwei Funktionen, ein Dateiformat, keine gemeinsame Datenklasse. Läuft es
    auseinander, verliert HEMS die Tagesfrist bei jedem Reload — und zwar
    genau dann, wenn jemand an der Konfiguration arbeitet und deshalb oft neu
    lädt.
    """
    geschrieben = _rueckgabe_schluessel("_kurve_sichern") or {
        k.value
        for knoten in ast.walk(_funktion("_kurve_sichern"))
        if isinstance(knoten, ast.Dict)
        for k in knoten.keys
        if isinstance(k, ast.Constant)
    }
    gelesen = {
        knoten.args[0].value
        for knoten in ast.walk(_funktion("async_kurve_laden"))
        if isinstance(knoten, ast.Call)
        and isinstance(knoten.func, ast.Attribute)
        and knoten.func.attr == "get"
        and knoten.args
        and isinstance(knoten.args[0], ast.Constant)
    }
    assert geschrieben, "kein Speicherformat gefunden"
    assert geschrieben == gelesen, (
        f"geschrieben {sorted(geschrieben)}, gelesen {sorted(gelesen)}"
    )
