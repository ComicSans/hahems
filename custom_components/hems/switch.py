"""Schalter: E-Auto-Zwangsladung.

Lädt das E-Auto unabhängig von Überschuss und Wallbox-Mindestleistung. Die
Wallbox-Last wird dabei aus dem Saldo herausgerechnet, den die Speicher-
Regelung sieht, damit der Hausakku nicht ins Auto leerläuft ("Akku schonen") —
das Zwangs-Delta kommt aus dem Netz.
"""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import HemsCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HemsCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Nur sinnvoll mit konfigurierter Wallbox (modulierbare Last); ohne eine
    # solche hätte der Schalter keine Wirkung.
    if coordinator.registry.modulateds:
        async_add_entities([HemsEvForceSwitch(coordinator)])


class HemsEvForceSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "ev_force"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: HemsCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_is_on = False
        self._attr_unique_id = f"{coordinator.entry.entry_id}_ev_force"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="HEMS",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._attr_is_on = last.state == "on"
            self._coordinator.ev_force = self._attr_is_on

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        self._attr_is_on = on
        self._coordinator.ev_force = on
        self.async_write_ha_state()
        # Wirkt sofort auf Empfehlung und Speicher-Regelung.
        await self._coordinator.async_request_refresh()
