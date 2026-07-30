"""Sensoren — die Ausgaberollen des Kontrakts.

Jede Rolle ist ein eigener Zustand mit Einheit und Zustandsklasse. Tragende
Werte stehen bewusst nie in Attributen: Attribute sind nicht in der Registry
verankert und brechen still.
"""
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
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .analysis.types import Analyse
from .const import DOMAIN, KONTRAKT_VERSION
from .coordinator import WpOptimizationCoordinator
from .entity import geraet


@dataclass(frozen=True, kw_only=True)
class WpSensorDescription(SensorEntityDescription):
    """Beschreibung samt Zugriff auf die Analyse."""

    wert: Callable[[Analyse], float | str | None]


SENSOREN: tuple[WpSensorDescription, ...] = (
    WpSensorDescription(
        key="cop_momentan",
        translation_key="cop_momentan",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        wert=lambda a: a.cop_momentan,
    ),
    WpSensorDescription(
        key="cop_soll",
        translation_key="cop_soll",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        wert=lambda a: a.cop_soll,
    ),
    WpSensorDescription(
        key="cop_soll_unsicherheit",
        translation_key="cop_soll_unsicherheit",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.cop_soll_unsicherheit,
    ),
    WpSensorDescription(
        key="cop_abweichung",
        translation_key="cop_abweichung",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.cop_abweichung,
    ),
    WpSensorDescription(
        key="waermeleistung",
        translation_key="waermeleistung",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        wert=lambda a: a.waermeleistung_w,
    ),
    WpSensorDescription(
        key="spreizung",
        translation_key="spreizung",
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        wert=lambda a: a.spreizung_k,
    ),
    WpSensorDescription(
        key="waermeverlust_koeffizient",
        translation_key="waermeverlust_koeffizient",
        native_unit_of_measurement="W/K",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.waermeverlust_w_pro_k,
    ),
    # Zähler: monoton wachsend. Das Stundenmittel einer Startzahl ist
    # bedeutungslos, Aussagen über einen Zeitraum entstehen aus der Differenz.
    WpSensorDescription(
        key="takte",
        translation_key="takte",
        state_class=SensorStateClass.TOTAL_INCREASING,
        wert=lambda a: a.takt.starts,
    ),
    WpSensorDescription(
        key="laufzeit_summe",
        translation_key="laufzeit_summe",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        wert=lambda a: round(a.takt.laufzeit_s / 3600.0, 3),
    ),
    WpSensorDescription(
        key="laufzeit_mittel",
        translation_key="laufzeit_mittel",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.laufzeit_mittel_min,
    ),
    # Empfehlungen. Sie werden veröffentlicht, nicht geschrieben.
    WpSensorDescription(
        key="empfehlung_fusspunkt",
        translation_key="empfehlung_fusspunkt",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.kurve.fusspunkt_c,
    ),
    WpSensorDescription(
        key="empfehlung_steilheit",
        translation_key="empfehlung_steilheit",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        wert=lambda a: a.kurve.steilheit,
    ),
    WpSensorDescription(
        key="empfehlung_vorlauf_min",
        translation_key="empfehlung_vorlauf_min",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.kurve.vorlauf_min_c,
    ),
    # Datenbasis: zweimal, weil zwei verschiedene Dinge gemeint sind.
    WpSensorDescription(
        key="datenbasis",
        translation_key="datenbasis",
        device_class=SensorDeviceClass.ENUM,
        options=["keine_daten", "unzureichend", "vorlaeufig", "belastbar"],
        wert=lambda a: a.datenbasis,
    ),
    WpSensorDescription(
        key="datenbasis_empfehlung",
        translation_key="datenbasis_empfehlung",
        device_class=SensorDeviceClass.ENUM,
        options=["keine_daten", "unzureichend", "vorlaeufig", "belastbar"],
        wert=lambda a: a.datenbasis_empfehlung,
    ),
    WpSensorDescription(
        key="verwerfungsgrund",
        translation_key="verwerfungsgrund",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "ok",
            "spreizung_zu_klein",
            "keine_leistung",
            "kein_durchfluss",
            "abtauen",
            "warmwasser",
            "unplausibel",
        ],
        wert=lambda a: a.verwerfungsgrund,
    ),
    WpSensorDescription(
        key="kontrakt_version",
        translation_key="kontrakt_version",
        entity_registry_enabled_default=False,
        wert=lambda _a: KONTRAKT_VERSION,
    ),
)

# Energie als eigene Klasse: sie wird integriert, nicht aus der Analyse
# gelesen.
ENERGIE_KEY = "waermemenge"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Sensoren anlegen."""
    coordinator: WpOptimizationCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        WpSensor(coordinator, entry, beschreibung) for beschreibung in SENSOREN
    ]
    entities.append(WaermemengeSensor(coordinator, entry))
    async_add_entities(entities)


class WpSensor(CoordinatorEntity[WpOptimizationCoordinator], SensorEntity):
    """Ein Wert aus der Analyse."""

    _attr_has_entity_name = True
    entity_description: WpSensorDescription

    def __init__(
        self,
        coordinator: WpOptimizationCoordinator,
        entry: ConfigEntry,
        beschreibung: WpSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = beschreibung
        self._attr_unique_id = f"{entry.entry_id}_{beschreibung.key}"
        self._attr_device_info = geraet(entry)

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.entity_description.wert(self.coordinator.data)


class WaermemengeSensor(
    CoordinatorEntity[WpOptimizationCoordinator], RestoreEntity, SensorEntity
):
    """Aufsummierte Wärmemenge.

    Integriert die geprüfte thermische Leistung über die Zeit. Bewusst hier
    und nicht in der Fachlogik: die Integration über die Zeit braucht die Uhr,
    und die Analyse soll ohne Uhr auskommen.

    Der Stand wird über Neustarts hinweg wiederhergestellt. Ein
    `total_increasing`-Zähler, der bei jedem Neustart auf null fällt, ist
    schlimmer als keiner: die Langzeitstatistik deutet den Rücksprung als
    neuen Zyklus und addiert den alten Stand dazu.
    """

    _attr_has_entity_name = True
    _attr_translation_key = ENERGIE_KEY
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(
        self, coordinator: WpOptimizationCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{ENERGIE_KEY}"
        self._attr_device_info = geraet(entry)
        self._kwh = 0.0
        self._letzter_ts: float | None = None

    async def async_added_to_hass(self) -> None:
        """Letzten Stand zurückholen."""
        await super().async_added_to_hass()
        alt = await self.async_get_last_state()
        if alt is not None and alt.state not in (None, "unknown", "unavailable"):
            try:
                self._kwh = float(alt.state)
            except (TypeError, ValueError):
                self._kwh = 0.0

    @property
    def native_value(self) -> float:
        return round(self._kwh, 3)

    def _handle_coordinator_update(self) -> None:
        analyse = self.coordinator.data
        if analyse is not None and analyse.waermeleistung_w:
            jetzt = analyse.takt.letzter_ts
            if jetzt is not None and self._letzter_ts is not None:
                delta = jetzt - self._letzter_ts
                # Lücken nach einem Neustart nicht als Dauerleistung buchen.
                if 0 < delta <= 900:
                    self._kwh += analyse.waermeleistung_w * delta / 3_600_000.0
            self._letzter_ts = jetzt
        super()._handle_coordinator_update()
