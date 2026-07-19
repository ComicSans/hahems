"""Prognose- und Empfehlungs-Sensoren."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HemsCoordinator, HemsData


@dataclass(frozen=True, kw_only=True)
class HemsSensorDescription(SensorEntityDescription):
    value_fn: Callable[[HemsData], float | str | None] = None
    attr_fn: Callable[[HemsData], dict] | None = None


SENSORS: tuple[HemsSensorDescription, ...] = (
    HemsSensorDescription(
        key="pv_heute",
        name="PV heute",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d.pv_today_kwh,
    ),
    HemsSensorDescription(
        key="pv_rest_heute",
        name="PV Rest heute",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d.pv_remaining_kwh,
    ),
    HemsSensorDescription(
        key="pv_morgen",
        name="PV morgen",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d.pv_tomorrow_kwh,
    ),
    HemsSensorDescription(
        key="pv_leistung_jetzt",
        name="PV Leistung jetzt (geschätzt)",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.pv_power_now_w,
    ),
    HemsSensorDescription(
        key="saldo",
        name="Netzsaldo",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.saldo_w,
    ),
    HemsSensorDescription(
        key="lastfluss",
        name="Lastfluss",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.haus_w,
        attr_fn=lambda d: {
            # Eine Entity als Datenquelle für die hems-flow-card
            "pv_w": d.pv_power_now_w,
            "netz_w": d.saldo_w,  # positiv = Netzbezug
            "batterie_w": d.batterie_w,  # positiv = Entladen
            "haus_w": d.haus_w,
            "wp_w": d.wp_w,
            "wallbox_w": d.wallbox_w,
            "speicher_soc": d.plan.speicher_soc,
            "pv_geschaetzt": True,
        },
    ),
    HemsSensorDescription(
        key="nachtdefizit",
        name="Nachtdefizit",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d.plan.nachtdefizit_kwh,
    ),
    HemsSensorDescription(
        key="ueberschuss_rest_heute",
        name="Überschuss Rest heute",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d.plan.ueberschuss_rest_kwh,
    ),
    HemsSensorDescription(
        key="sonnenfenster",
        name="Sonnenfenster",
        native_unit_of_measurement="h",
        value_fn=lambda d: round(d.plan.sonnenfenster_h, 1),
    ),
    HemsSensorDescription(
        key="speicher_soc",
        name="Speicher SoC gesamt",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.plan.speicher_soc,
    ),
    HemsSensorDescription(
        key="speicher_verfuegbar",
        name="Speicher verfügbar",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d.plan.speicher_verfuegbar_kwh,
    ),
    HemsSensorDescription(
        key="speicher_ziel_soc",
        name="Speicher Ziel-SoC",
        native_unit_of_measurement="%",
        value_fn=lambda d: d.plan.speicher_ziel_soc,
    ),
    HemsSensorDescription(
        key="empfehlung",
        name="Empfehlung",
        value_fn=lambda d: d.plan.empfehlung[:255],
        attr_fn=lambda d: {
            "prioritaeten": d.plan.prioritaeten,
            "speicher_bedarf_kwh": d.plan.speicher_bedarf_kwh,
            "speicher_kapazitaet_kwh": d.plan.speicher_kapazitaet_kwh,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HemsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(HemsSensor(coordinator, desc) for desc in SENSORS)


class HemsSensor(CoordinatorEntity[HemsCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: HemsCoordinator, description: HemsSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="HEMS",
            manufacturer="Tobias Reithmeier",
            model="HEMS Planner",
        )

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        if self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(self.coordinator.data)
