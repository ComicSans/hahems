"""Schalter: E-Auto-Zwangsladung und „Akkus aufladen".

**E-Auto-Zwangsladung** lädt das Auto unabhängig von Überschuss und Wallbox-
Mindestleistung. Die Wallbox-Last wird dabei aus dem Saldo herausgerechnet, den
die Speicher-Regelung sieht, damit der Hausakku nicht ins Auto leerläuft ("Akku
schonen") — das Zwangs-Delta kommt aus dem Netz.

**Akkus aufladen** (T-004, Tobias 12.10.2026; ersetzt Notstromreserve und
Speicher-Zwangsladung) lädt die Speicher mit voller Leistung, unabhängig vom
Netzsaldo — also notfalls aus dem Netz — und hält sie danach voll: kein
Entladen, bis der Schalter wieder ausgeschaltet wird. Er ist der einzige Pfad
in HEMS, der Netzstrom in den Speicher schiebt; die Saldo-Regelung lädt sonst
ausschließlich gegen Export. Bewusst ein Schalter und keine Option im
Konfigurations-Flow: Ein angekündigter Ausfall ist ein Zustand von Stunden,
kein Setup — so lässt er sich auch aus einer Automation heraus setzen.
"""
from __future__ import annotations

import logging

from homeassistant.components import persistent_notification
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .aufladen import alte_reserve_unique_id, aufladen_hinweis
from .const import DOMAIN
from .coordinator import HemsCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HemsCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []
    # Nur sinnvoll mit konfigurierter Wallbox (modulierbare Last); ohne eine
    # solche hätte der Schalter keine Wirkung.
    if coordinator.registry.modulateds:
        entities.append(HemsEvForceSwitch(coordinator))
    # Dasselbe für „Akkus aufladen": ohne Speicher gibt es nichts zu füllen.
    if coordinator.registry.storages:
        entities.append(HemsBatteryForceSwitch(coordinator))
    # Der frühere Schalter „Speicher als Notstromreserve" ist in „Akkus
    # aufladen" aufgegangen (T-004). Seinen Registry-Eintrag entfernen, sonst
    # stünde er dauerhaft als „nicht verfügbar" in der Geräteseite.
    ent_reg = er.async_get(hass)
    if alt := ent_reg.async_get_entity_id(
        "switch", DOMAIN, alte_reserve_unique_id(entry.entry_id)
    ):
        _LOGGER.info(
            "HEMS: Schalter „Speicher als Notstromreserve“ entfällt (%s); "
            "dafür „Akkus aufladen“ einschalten",
            alt,
        )
        ent_reg.async_remove(alt)
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


class HemsBatteryForceSwitch(SwitchEntity, RestoreEntity):
    """Akkus aufladen: voll laden, koste es Netzstrom, und voll halten.

    Die Saldo-Regelung lädt den Akku nur gegen Export — steht der Netzsaldo im
    Bezug, lädt sie nicht, egal wie leer der Speicher ist. Dieser Schalter ist
    die Ausnahme: Er setzt den Sollwert der Regelung fest auf die volle
    Ladeleistung der Speicher unter dem physischen Ladeende und nimmt den
    Netzbezug in Kauf. Dazu gehören Ziel-SoC 100 %, Ladedeckel sofort statt
    just in time, keine Mittags-Ladepause, Ladevorrang vor den Lasten und die
    volle Regel-Schrittweite.

    Sind alle Speicher voll, hält er sie voll: Laden aus Überschuss ja,
    Entladen nein. Sinkt ein Speicher wieder unter das Ladeende, lädt er nach.
    Er schaltet sich nicht selbst ab (Entscheidung Tobias 12.10.2026, T-004) —
    die Akkus bleiben bereit, bis jemand ihn ausschaltet.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "speicher_zwang"
    _attr_icon = "mdi:battery-charging-high"

    def __init__(self, coordinator: HemsCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_is_on = False
        self._attr_unique_id = f"{coordinator.entry.entry_id}_speicher_zwang"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="HEMS",
        )

    # Seit wann der Schalter steht (T-013): als Attribut persistiert, damit der
    # Dauer-Hinweis nach einem Neustart den echten Einschaltzeitpunkt nennt.
    _seit: str | None = None

    @property
    def extra_state_attributes(self) -> dict:
        return {"eingeschaltet_seit": self._seit} if self._attr_is_on else {}

    @property
    def _hinweis_id(self) -> str:
        return f"hems_akkus_aufladen_{self._coordinator.entry.entry_id}"

    def _hinweis(self) -> None:
        """Dauer-Hinweis, solange aufgeladen wird (Tobias 12.10.2026, T-013):
        Der Schalter kauft notfalls Netzstrom und endet nicht von selbst — auch
        nicht nach einem Neustart. Das soll niemand übersehen."""
        if not self._attr_is_on:
            persistent_notification.async_dismiss(self.hass, self._hinweis_id)
            return
        seit = dt_util.parse_datetime(self._seit) if self._seit else None
        titel, text = aufladen_hinweis(dt_util.as_local(seit) if seit else None)
        persistent_notification.async_create(
            self.hass, text, title=titel, notification_id=self._hinweis_id
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._attr_is_on = last.state == "on"
            self._seit = last.attributes.get("eingeschaltet_seit")
            self._coordinator.battery_force = self._attr_is_on
        self._hinweis()

    async def async_will_remove_from_hass(self) -> None:
        # Integration entladen oder neu geladen: Der Hinweis gehört zur
        # laufenden Instanz; beim Wiederanlegen setzt `async_added_to_hass`
        # ihn neu.
        persistent_notification.async_dismiss(self.hass, self._hinweis_id)
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        if on and not self._attr_is_on:
            self._seit = dt_util.utcnow().isoformat()
        self._attr_is_on = on
        self._coordinator.battery_force = on
        self.async_write_ha_state()
        self._hinweis()
        await self._coordinator.async_request_refresh()
