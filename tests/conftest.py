"""Test-Setup: die reine Planner-Logik HA-frei importierbar machen.

Das Integrations-Paket ``custom_components/hems/__init__.py`` importiert Home
Assistant beim Laden — jeder ``custom_components.hems.*``-Import würde das
triggern und ohne installiertes HA scheitern. Die eigentliche Regel-Logik
(``planner``/``strategies``/``const``) ist aber bewusst HA-frei gehalten
("reine Funktionen ohne Home-Assistant-Abhängigkeiten, damit die Logik testbar
bleibt", siehe planner-Docstring).

Darum wird das Verzeichnis ``custom_components/hems`` hier als schlankes Paket
``hems`` registriert, ohne dessen HA-``__init__`` auszuführen. Tests
importieren entsprechend ``from hems.strategies import battery`` usw. Nebeneffekt:
Importiert ein getestetes Modul doch HA, schlägt der Import fehl — ein
struktureller Wächter dafür, dass die Logik HA-frei bleibt.
"""
from __future__ import annotations

import pathlib
import sys
import types

_HEMS_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "hems"

if "hems" not in sys.modules:
    _pkg = types.ModuleType("hems")
    _pkg.__path__ = [str(_HEMS_DIR)]  # macht 'hems' zu einem Namespace-Paket
    sys.modules["hems"] = _pkg
