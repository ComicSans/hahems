"""Die Architekturregeln der Wärmepumpen-Analyse als Test.

Eine Regel, die nur in einem Dokument steht, wird beim ersten eiligen Commit
gebrochen und fällt niemandem auf. Diese drei fallen sofort auf.

Die dritte trägt seit der Zusammenführung mehr Gewicht als vorher: Solange
die Analyse ein eigenes Repository war, hielt schon die Repo-Grenze
Schreibzugriffe fern. Jetzt liegt sie neben einem Aktuator, der wirklich
schaltet — und nur noch dieser Test hält die Grenze.
"""
from __future__ import annotations

import ast

from conftest import ANALYSE


def _importierte_module(quelle: str) -> set[str]:
    """Tatsaechlich importierte Modulnamen.

    Ueber den Syntaxbaum statt per Textsuche: sonst schlaegt schon eine
    Erwaehnung im Docstring an, und ein Test, der bei korrektem Code
    fehlschlaegt, wird bald abgeschaltet.
    """
    namen: set[str] = set()
    for knoten in ast.walk(ast.parse(quelle)):
        if isinstance(knoten, ast.Import):
            namen.update(alias.name for alias in knoten.names)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            namen.add(knoten.module)
    return namen


def test_analyse_importiert_kein_home_assistant():
    # Sobald ein Modul hier `homeassistant` anfasst, faellt es aus der
    # Testsuite heraus — dann braeuchte jeder Test eine HA-Installation.
    treffer = [
        pfad.name
        for pfad in sorted(ANALYSE.glob("*.py"))
        if any(
            name.split(".")[0] == "homeassistant"
            for name in _importierte_module(pfad.read_text(encoding="utf-8"))
        )
    ]
    assert treffer == [], f"HA-Import in der Fachlogik: {', '.join(treffer)}"


def test_types_importiert_aus_keinem_anderen_analysemodul():
    # `types.py` ist die gemeinsame Heimat der Laufzeittypen. Importiert es
    # seinerseits ein Analysemodul, kann ein Importzyklus entstehen.
    importiert = _importierte_module((ANALYSE / "types.py").read_text(encoding="utf-8"))
    geschwister = {
        pfad.stem
        for pfad in ANALYSE.glob("*.py")
        if pfad.stem not in ("types", "__init__")
    }
    verboten = {name.lstrip(".").split(".")[0] for name in importiert} & geschwister
    assert not verboten, f"types.py importiert Analysemodule: {verboten}"


def test_fachlogik_schreibt_nicht_an_die_anlage():
    # Die Analyse ist beratend: sie veroeffentlicht Empfehlungen, umgesetzt
    # werden sie vom Aktuator. Ein Dienstaufruf hier waere der erste Schritt
    # dahin, dass zwei Stellen denselben Sollwert stellen.
    for pfad in sorted(ANALYSE.glob("*.py")):
        quelle = pfad.read_text(encoding="utf-8")
        assert "async_call" not in quelle, pfad.name
        assert "services.call" not in quelle, pfad.name
