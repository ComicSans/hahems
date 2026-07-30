"""Hinweise als eigene Binärsensoren.

Je Hinweisart eine eigene Entity mit stabiler Kennung — keine Liste in einem
Attribut. Nur so bleiben Hinweise in der Registry verankert und in
Automationen adressierbar.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .analysis.types import Analyse
from .const import DOMAIN
from .coordinator import WpOptimizationCoordinator
from .entity import geraet


@dataclass(frozen=True, kw_only=True)
class WpHinweisDescription(BinarySensorEntityDescription):
    """Beschreibung samt Zugriff auf den Hinweiszustand."""

    aktiv: Callable[[Analyse], bool]


HINWEISE: tuple[WpHinweisDescription, ...] = (
    WpHinweisDescription(
        key="hinweis_spreizung_niedrig",
        translation_key="hinweis_spreizung_niedrig",
        aktiv=lambda a: a.hinweise.spreizung_niedrig,
    ),
    WpHinweisDescription(
        key="hinweis_spreizung_hoch",
        translation_key="hinweis_spreizung_hoch",
        aktiv=lambda a: a.hinweise.spreizung_hoch,
    ),
    WpHinweisDescription(
        key="hinweis_taktung_hoch",
        translation_key="hinweis_taktung_hoch",
        aktiv=lambda a: a.hinweise.taktung_hoch,
    ),
    WpHinweisDescription(
        key="hinweis_vorlauf_zu_hoch",
        translation_key="hinweis_vorlauf_zu_hoch",
        aktiv=lambda a: a.hinweise.vorlauf_zu_hoch,
    ),
    WpHinweisDescription(
        key="hinweis_effizienz_unter_erwartung",
        translation_key="hinweis_effizienz_unter_erwartung",
        aktiv=lambda a: a.hinweise.effizienz_unter_erwartung,
    ),
    # Kein Anlagen-, sondern ein Messproblem — deshalb als Störung
    # gekennzeichnet und nicht als Optimierungstipp.
    WpHinweisDescription(
        key="hinweis_temperaturen_identisch",
        translation_key="hinweis_temperaturen_identisch",
        device_class=BinarySensorDeviceClass.PROBLEM,
        aktiv=lambda a: a.hinweise.temperaturen_identisch,
    ),
    WpHinweisDescription(
        key="durchfluss_geschaetzt",
        translation_key="durchfluss_geschaetzt",
        entity_registry_enabled_default=False,
        aktiv=lambda a: a.durchfluss_geschaetzt,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Hinweise anlegen."""
    coordinator: WpOptimizationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        WpHinweis(coordinator, entry, beschreibung) for beschreibung in HINWEISE
    )


class WpHinweis(CoordinatorEntity[WpOptimizationCoordinator], BinarySensorEntity):
    """Ein Hinweis."""

    _attr_has_entity_name = True
    entity_description: WpHinweisDescription

    def __init__(
        self,
        coordinator: WpOptimizationCoordinator,
        entry: ConfigEntry,
        beschreibung: WpHinweisDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = beschreibung
        self._attr_unique_id = f"{entry.entry_id}_{beschreibung.key}"
        self._attr_device_info = geraet(entry)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.aktiv(self.coordinator.data)
