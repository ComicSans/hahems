"""Die Architekturregeln als Test.

Eine Regel, die nur in der CLAUDE.md steht, wird beim ersten eiligen Commit
gebrochen und faellt niemandem auf. Diese beiden fallen sofort auf.
"""
from __future__ import annotations

import ast

from conftest import INTEGRATION

ANALYSE = INTEGRATION / "analysis"


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
    # Diese Integration hat keinen Aktuierungspfad. Ein Dienstaufruf in der
    # Fachlogik waere der erste Schritt dorthin.
    for pfad in sorted(ANALYSE.glob("*.py")):
        quelle = pfad.read_text(encoding="utf-8")
        assert "async_call" not in quelle, pfad.name
        assert "services.call" not in quelle, pfad.name
