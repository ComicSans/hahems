"""Binärsensoren: freie Kapazität, Config-Check, WP-Störung und die
Hinweise der Wärmepumpen-Analyse."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_FREE_H, CONF_FREE_KWH, DEFAULT_FREE_H, DEFAULT_FREE_KWH, DOMAIN
from .coordinator import HemsCoordinator
from .models import HeatPumpAnalysis
from .waermepumpe.entities import HINWEISE as ANALYSE_HINWEISE
from .waermepumpe.entities import AnalyseHinweisDescription


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HemsCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [
        HemsCapacityFreeSensor(coordinator),
        HemsConfigCheckSensor(coordinator),
        HemsFaultSensor(coordinator),
    ]
    for rolle in coordinator.registry.analyses:
        entities += [
            AnalyseHinweisSensor(coordinator, rolle, desc)
            for desc in ANALYSE_HINWEISE
        ]
    async_add_entities(entities)


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


class HemsConfigCheckSensor(
    CoordinatorEntity[HemsCoordinator], BinarySensorEntity
):
    """Config-Sanity-Check für den Auto-Modus. An = Problem (harte Fehler, oder
    im Auto-Modus eine Überlappung mit aktiven Automationen). Details als
    Attribute: was der Auto-Modus schaltet, Fehler, Warnungen, Überlappungen."""

    _attr_has_entity_name = True
    _attr_name = "Konfiguration"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: HemsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_konfiguration"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="HEMS",
            manufacturer="Tobias Reithmeier",
            model="HEMS Planner",
        )

    @property
    def is_on(self) -> bool:
        check = self.coordinator.data.config_check
        return check is not None and check.problem(self.coordinator.mode)

    @property
    def extra_state_attributes(self) -> dict:
        check = self.coordinator.data.config_check
        if check is None:
            return {}
        return {
            "bereit_fuer_auto": not check.errors,
            "auto_schaltet": check.actuated or ["(nichts – keine Steuer-Entities)"],
            "fehler": check.errors,
            "warnungen": check.warnings,
            "ueberlappung": check.overlaps,
            "hinweise": check.info,
            "ueberlappungspruefung": "ok" if check.scan_ok else "nicht verfügbar",
        }


class HemsFaultSensor(CoordinatorEntity[HemsCoordinator], BinarySensorEntity):
    """An, wenn eine als Störungsquelle konfigurierte Wärmepumpe eine
    (entprellte) Betriebsstörung meldet. Als Push-Quelle gedacht: eine
    Nutzer-Automation triggert auf diesen Sensor und ruft `notify.mobile_app_…`.
    Die einzelnen Störungen (Anlage, Code, Klartext) stehen als Attribute."""

    _attr_has_entity_name = True
    _attr_name = "Wärmepumpen-Störung"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: HemsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_waermepumpen_stoerung"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="HEMS",
            manufacturer="Tobias Reithmeier",
            model="HEMS Planner",
        )

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.fault_alerts)

    @property
    def extra_state_attributes(self) -> dict:
        alerts = self.coordinator.fault_alerts
        return {
            "anzahl": len(alerts),
            "stoerungen": [
                {
                    "anlage": a.placeholders.get("name", ""),
                    "code": a.placeholders.get("code", ""),
                    "meldung": a.message,
                }
                for a in alerts
            ],
            # Ein-Zeilen-Zusammenfassung, direkt für die Push-Nachricht nutzbar.
            "meldung": " | ".join(a.title for a in alerts),
        }


class AnalyseHinweisSensor(CoordinatorEntity[HemsCoordinator], BinarySensorEntity):
    """Ein Hinweis der Wärmepumpen-Analyse.

    Je Hinweisart eine eigene Entity mit stabiler Kennung, nicht eine Liste in
    einem Attribut: nur so bleiben sie in der Registry verankert und in
    Automationen adressierbar.

    Ohne Analyse ist der Zustand `None` und nicht `off`. Ein Hinweis, der
    „alles in Ordnung" meldet, während gar nichts gemessen wird, wäre eine
    Falschaussage.
    """

    _attr_has_entity_name = True
    entity_description: AnalyseHinweisDescription

    def __init__(
        self,
        coordinator: HemsCoordinator,
        rolle: HeatPumpAnalysis,
        description: AnalyseHinweisDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._rolle = rolle
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_{rolle.id}_{description.key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_{rolle.id}")},
            via_device=(DOMAIN, coordinator.entry.entry_id),
            name=rolle.name,
            manufacturer="Tobias Reithmeier",
            model="HEMS Wärmepumpen-Analyse",
        )

    @property
    def is_on(self) -> bool | None:
        analyse = self.coordinator.data.analysen.get(self._rolle.id)
        if analyse is None:
            return None
        return self.entity_description.wert(analyse)
