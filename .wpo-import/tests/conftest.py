"""Die Analyse ohne Home Assistant importierbar machen.

`custom_components/wp_optimization/__init__.py` ist die HA-Schicht und
importiert `homeassistant`. Wuerden die Tests das Paket regulaer importieren,
zoegen sie eine HA-Installation nach sich. Stattdessen kommt das
Integrationsverzeichnis selbst auf den Suchpfad, sodass `analysis` als
eigenstaendiges Paket geladen wird und die HA-Schicht unberuehrt bleibt.
"""
from __future__ import annotations

import sys
from pathlib import Path

INTEGRATION = (
    Path(__file__).resolve().parents[1] / "custom_components" / "wp_optimization"
)
sys.path.insert(0, str(INTEGRATION))

PRESET_DIR = INTEGRATION / "presets"
