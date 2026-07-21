"""Auswahl-Entities: Betriebsmodus (beobachten/aus) und Optimierungsziel."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DEFAULT_GAIN_LEVEL,
    DOMAIN,
    GAIN_LEVELS,
    GOAL_SELF_CONSUMPTION,
    GOALS,
    MODE_AUTO,
    MODE_OBSERVE,
    MODE_OFF,
)
from .coordinator import HemsCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HemsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            HemsModeSelect(coordinator),
            HemsGoalSelect(coordinator),
            HemsGainSelect(coordinator),
        ]
    )


class HemsModeSelect(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Modus"
    _attr_options = [MODE_OBSERVE, MODE_AUTO, MODE_OFF]

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
        # Moduswechsel (z. B. → auto) sofort wirksam machen.
        await self._coordinator.async_request_refresh()


class HemsGoalSelect(SelectEntity, RestoreEntity):
    """Optimierungsziel der Speicher-Regelung: Eigenverbrauch (Default),
    echte Nulleinspeisung oder dauerhaftes Vollladen. Orthogonal zum
    priority_mode, der nur die Überschussreihenfolge bestimmt."""

    _attr_has_entity_name = True
    _attr_translation_key = "optimierungsziel"
    _attr_options = list(GOALS)

    def __init__(self, coordinator: HemsCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_current_option = GOAL_SELF_CONSUMPTION
        self._attr_unique_id = f"{coordinator.entry.entry_id}_goal"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="HEMS",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state in self.options:
            self._attr_current_option = last.state
            self._coordinator.goal = last.state

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self._coordinator.goal = option
        self.async_write_ha_state()
        # Ziel wirkt sofort auf die Empfehlung – Neuberechnung anstoßen.
        await self._coordinator.async_request_refresh()


class HemsGainSelect(SelectEntity, RestoreEntity):
    """Regel-Aggressivität der Speicher-Regelung: min/normal/max. Skaliert die
    Regler-Gains, damit Ladelücken schneller geschlossen werden — wirkt nur auf
    die Korrektur-Schrittweite pro Zyklus, nicht auf die Umschaltrate (die durch
    den 60-s-Takt weiter bei 1×/min bleibt). Default aggressiv ('max')."""

    _attr_has_entity_name = True
    _attr_translation_key = "regel_aggressivitaet"
    _attr_options = list(GAIN_LEVELS)

    def __init__(self, coordinator: HemsCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_current_option = DEFAULT_GAIN_LEVEL
        self._attr_unique_id = f"{coordinator.entry.entry_id}_gain"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="HEMS",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state in self.options:
            self._attr_current_option = last.state
            self._coordinator.gain_level = last.state

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self._coordinator.gain_level = option
        self.async_write_ha_state()
        # Aggressivität wirkt sofort auf die Empfehlung – Neuberechnung anstoßen.
        await self._coordinator.async_request_refresh()
