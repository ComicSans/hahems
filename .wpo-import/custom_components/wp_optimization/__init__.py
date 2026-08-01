"""WP-Optimierung — Effizienzmessung und Verbesserungshinweise.

Ein beratendes System. **Es schreibt nie an die Anlage**: kein
Aktuierungspfad, keine Steuer-Entities, kein Modus. Gesteuert wird am Gerät
oder im Energiemanagement; hier werden Empfehlungen nur veröffentlicht. So
können zwei Integrationen sich nie um denselben Sollwert streiten.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import WpOptimizationCoordinator

_LOGGER = logging.getLogger(__name__)

PLATTFORMEN: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Eine Einrichtung laden."""
    coordinator = WpOptimizationCoordinator(hass, entry)
    try:
        await coordinator.async_vorbereiten()
    except ValueError as err:
        raise ConfigEntryNotReady(str(err)) from err

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATTFORMEN)
    entry.async_on_unload(entry.add_update_listener(_neu_laden))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Eine Einrichtung entladen."""
    entladen = await hass.config_entries.async_unload_platforms(entry, PLATTFORMEN)
    if entladen:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return entladen


async def _neu_laden(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Nach geänderten Optionen neu laden.

    Eine nachträglich verdrahtete Rolle soll ohne Neuinstallation greifen.
    """
    await hass.config_entries.async_reload(entry.entry_id)
