"""WP-Optimierung — Effizienzmessung und Verbesserungshinweise.

Diese Datei ist die Home-Assistant-Schicht. Sie ist derzeit ein Platzhalter:
der fachliche Kern unter `analysis/` steht und ist getestet, die Anbindung an
Home Assistant (Konfigurationsdialog, Entities, Karte) folgt.

`config_flow` steht im Manifest deshalb bewusst auf `false` — eine
Integration, die einen Dialog verspricht, den es noch nicht gibt, laesst sich
zwar installieren, aber nicht einrichten.

Grundregel dieser Integration: **sie schreibt nie an die Anlage.** Es gibt
keinen Aktuierungspfad und keine Steuer-Entities. Empfehlungen werden
veroeffentlicht, umgesetzt werden sie vom Energiemanagement. So koennen zwei
Integrationen sich nie um denselben Sollwert streiten.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

__all__ = ["DOMAIN", "async_setup"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Noch nichts einzurichten — siehe Modulbeschreibung."""
    return True
