"""Betriebsmodus: beobachten (Phase 1) oder aus. 'auto' folgt in Phase 2."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, MODE_OBSERVE, MODE_OFF
from .coordinator import HemsCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HemsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HemsModeSelect(coordinator)])


class HemsModeSelect(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Modus"
    _attr_options = [MODE_OBSERVE, MODE_OFF]

    def __init__(self, coordinator: HemsCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_current_option = MODE_OBSERVE
        self._attr_unique_id = f"{coordinator.entry.entry_id}_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="HEMS",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state in self.options:
            self._attr_current_option = last.state
            self._coordinator.mode = last.state

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self._coordinator.mode = option
        self.async_write_ha_state()
