"""Wo die Wärmepumpen-Analyse liegt, für die Tests, die auf Dateien schauen.

Den Suchpfad richtet bereits `tests/conftest.py` ein: es registriert
`custom_components/hems` als schlankes Paket `hems`, ohne dessen HA-`__init__`
auszuführen. Die Fachlogik ist darüber als `hems.waermepumpe.analysis`
importierbar, ohne dass eine Home-Assistant-Installation nötig wäre.

Hier stehen nur noch die Pfade für die Tests, die Dateien prüfen statt
Funktionen — die Architekturregeln und das Preset-Verzeichnis.
"""
from __future__ import annotations

import pathlib

PAKET = (
    pathlib.Path(__file__).resolve().parents[2]
    / "custom_components"
    / "hems"
    / "waermepumpe"
)

ANALYSE = PAKET / "analysis"
PRESET_DIR = PAKET / "presets"
