"""Schalter: E-Auto-Zwangsladung und Notstromreserve.

**E-Auto-Zwangsladung** lädt das Auto unabhängig von Überschuss und Wallbox-
Mindestleistung. Die Wallbox-Last wird dabei aus dem Saldo herausgerechnet, den
die Speicher-Regelung sieht, damit der Hausakku nicht ins Auto leerläuft ("Akku
schonen") — das Zwangs-Delta kommt aus dem Netz.

**Notstromreserve** stellt den Speicher auf Bereitschaft für einen Ausfall:
Ziel-SoC 100 %, sofort statt just in time, Ladevorrang vor allen Lasten und die
volle Regel-Schrittweite beim Laden. Bewusst ein Schalter und keine Option im
Konfigurations-Flow: Ein angekündigter Sturm ist ein Zustand von Stunden, kein
Setup — so lässt er sich auch aus einer Automation heraus setzen.
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
    entities: list[SwitchEntity] = []
    # Nur sinnvoll mit konfigurierter Wallbox (modulierbare Last); ohne eine
    # solche hätte der Schalter keine Wirkung.
    if coordinator.registry.modulateds:
        entities.append(HemsEvForceSwitch(coordinator))
    # Dasselbe für die Notstromreserve: ohne Speicher gibt es nichts zu füllen.
    if coordinator.registry.storages:
        entities.append(HemsEmergencyReserveSwitch(coordinator))
    async_add_entities(entities)


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


class HemsEmergencyReserveSwitch(SwitchEntity, RestoreEntity):
    """Speicher als Notstromreserve: sehr schnell sehr voll.

    Hebt Ladedeckel, Just-in-time-Rampe und Mittagspause auf, gibt dem Akku den
    Ladevorrang vor allen Lasten und lädt mit voller Regel-Schrittweite. Die
    ENTLADEgrenze bleibt die Reserve-SoC der Speicher-Rolle — wer eine Reserve
    will, die auch über die Nacht stehen bleibt, hebt sie dort an.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "notstromreserve"
    _attr_icon = "mdi:home-battery"

    def __init__(self, coordinator: HemsCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_is_on = False
        self._attr_unique_id = f"{coordinator.entry.entry_id}_notstromreserve"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="HEMS",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._attr_is_on = last.state == "on"
            self._coordinator.emergency_reserve = self._attr_is_on

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        self._attr_is_on = on
        self._coordinator.emergency_reserve = on
        self.async_write_ha_state()
        await self._coordinator.async_request_refresh()
