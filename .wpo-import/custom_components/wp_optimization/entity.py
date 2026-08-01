"""Gemeinsame Gerätezuordnung.

Alle Entities hängen an einem Gerät je Einrichtung, damit sie in der
Oberfläche zusammen erscheinen und gemeinsam benannt werden können.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def geraet(entry: ConfigEntry) -> DeviceInfo:
    """Geräteeintrag der Einrichtung."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="wp-optimization",
        model="Effizienzanalyse",
        entry_type=None,
    )
