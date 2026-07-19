"""Binärsensor: Ist Kapazität für einen zusätzlichen Verbraucher frei?"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_FREE_H, CONF_FREE_KWH, DEFAULT_FREE_H, DEFAULT_FREE_KWH, DOMAIN
from .coordinator import HemsCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HemsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HemsCapacityFreeSensor(coordinator)])


class HemsCapacityFreeSensor(CoordinatorEntity[HemsCoordinator], BinarySensorEntity):
    """An, wenn der konfigurierte Energiebedarf über die konfigurierte Dauer
    gedeckt werden kann, ohne Reserve und Nachtdeckung anzutasten."""

    _attr_has_entity_name = True
    _attr_name = "Kapazität frei"

    def __init__(self, coordinator: HemsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_kapazitaet_frei"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="HEMS",
            manufacturer="Tobias Reithmeier",
            model="HEMS Planner",
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.plan.kapazitaet_frei

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "frei_kwh": self.coordinator.data.plan.kapazitaet_frei_kwh,
            "bedarf_kwh": self.coordinator._opt(CONF_FREE_KWH, DEFAULT_FREE_KWH),
            "dauer_h": self.coordinator._opt(CONF_FREE_H, DEFAULT_FREE_H),
        }
