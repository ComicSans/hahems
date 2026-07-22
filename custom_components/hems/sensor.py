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
from homeassistant.util import dt as dt_util

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
        name="PV Leistung jetzt",
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
        key="hausverbrauch",
        name="Hausverbrauch",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        # Dieselbe Berechnung wie der Haus-Knoten der Lastfluss-Karte
        # (PV + Batterie-Entladung + Netzbezug), hier als eigene Entität für
        # Dashboards/Statistik statt nur als Kartenattribut.
        value_fn=lambda d: d.haus_w,
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
            # Pro-Speicher-Aufschlüsselung für die Karte (Name, SoC %, W)
            "speicher": d.speicher_liste,
            "haus_w": d.haus_w,
            "wp_w": d.wp_w,
            "wallbox_w": d.wallbox_w,
            "speicher_soc": d.plan.speicher_soc,
            "pv_geschaetzt": d.pv_power_estimated,
            # Status-Chips der Flow-Card
            "regelung_modus": d.plan.regelung.modus if d.plan.regelung else None,
            "regelung_w": d.plan.regelung.soll_w if d.plan.regelung else None,
            "ww_soll_c": d.plan.ww_soll_c,
            "ww_status": d.plan.ww_status,
            "wp_modus": d.plan.heizung.modus if d.plan.heizung else None,
            "wp_vlt_c": d.plan.heizung.vlt_ziel_c if d.plan.heizung else None,
        },
    ),
    HemsSensorDescription(
        key="nachtdefizit",
        name="Nachtdefizit",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda d: d.plan.nachtdefizit_kwh,
        attr_fn=lambda d: {
            # WP-Anteil ist bereits im Nachtdefizit enthalten
            "wp_anteil_kwh": d.plan.wp_nacht_kwh,
        },
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
        key="entladeplan",
        name="Entladeplan",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        # Zustand = geplante Entlade-Obergrenze jetzt; tagsüber unbekannt.
        # "Entladung" meint die Akku-Abgabe ins Haus (Nulleinspeisung-Ziel),
        # nicht die Einspeisung ins öffentliche Netz (dafür: hems_netzsaldo).
        value_fn=lambda d: d.plan.entlade_w_jetzt,
        attr_fn=lambda d: {
            "budget_kwh": d.plan.entlade_budget_kwh,
            "slots": [
                {
                    "von": dt_util.as_local(s.start).isoformat(),
                    "bis": dt_util.as_local(s.end).isoformat(),
                    "watt": s.watt,
                }
                for s in d.plan.entladeplan
            ],
            # Daten für die hems-plan-card
            "pv_kurve": [
                {
                    "von": dt_util.as_local(s.start).isoformat(),
                    "bis": dt_util.as_local(s.end).isoformat(),
                    "watt": s.watt,
                }
                for s in d.plan.pv_kurve
            ],
            # Erwarteter SoC-Verlauf ab jetzt bis zum Ende des Folgetags
            "soc_prognose": [
                {"zeit": dt_util.as_local(p.zeit).isoformat(), "soc": p.soc}
                for p in d.plan.soc_prognose
            ],
            # Warmwasser-Sperrzeiten im Darstellungshorizont
            "ww_sperren": [
                {
                    "von": dt_util.as_local(start).isoformat(),
                    "bis": dt_util.as_local(end).isoformat(),
                }
                for start, end in d.plan.ww_sperrfenster
            ],
            "ww_gesperrt": d.plan.ww_gesperrt,
            # Legionellenschutz-Fenster im Darstellungshorizont
            "ww_legionellen": [
                {
                    "von": dt_util.as_local(start).isoformat(),
                    "bis": dt_util.as_local(end).isoformat(),
                }
                for start, end in d.plan.ww_legionellen_fenster
            ],
            "ww_soll_c": d.plan.ww_soll_c,
            "ww_status": d.plan.ww_status,
            # Status-Chips: Heizkreis und Saldo-Regelung
            "wp_modus": d.plan.heizung.modus if d.plan.heizung else None,
            "wp_vlt_c": d.plan.heizung.vlt_ziel_c if d.plan.heizung else None,
            "regelung_modus": d.plan.regelung.modus if d.plan.regelung else None,
            "regelung_w": d.plan.regelung.soll_w if d.plan.regelung else None,
            "reserve_aktiv": d.plan.regelung.reserve_aktiv
            if d.plan.regelung
            else None,
            "pv_rest_heute_kwh": d.pv_remaining_kwh,
            "pv_morgen_kwh": d.pv_tomorrow_kwh,
            "speicher_soc": d.plan.speicher_soc,
            "wetter_morgen": d.wetter_morgen,
            # Quellen, aus denen die Plankarte den gemessenen Tagesverlauf
            # nachlädt (Slugs sind instanzabhängig, daher nicht ratbar)
            "verlauf_pv_entity": d.verlauf_pv_entity,
            "verlauf_soc_entity": d.verlauf_soc_entity,
        },
    ),
    HemsSensorDescription(
        key="ww_soll",
        name="Warmwasser-Soll",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        # Empfohlener Sollwert; in der Sperrzeit None ("aus", siehe Status)
        value_fn=lambda d: d.plan.ww_soll_c,
        attr_fn=lambda d: {
            "status": d.plan.ww_status,
            "gesperrt": d.plan.ww_gesperrt,
            "legionellenschutz_aktiv": d.plan.ww_legionelle_aktiv,
            "legionellen_fenster": [
                {
                    "von": dt_util.as_local(start).isoformat(),
                    "bis": dt_util.as_local(end).isoformat(),
                }
                for start, end in d.plan.ww_legionellen_fenster
            ],
            "boost_speicher_ok": d.plan.flags.ww_boost_soc,
            "boost_einspeisung_ok": d.plan.flags.ww_boost_saldo,
        },
    ),
    HemsSensorDescription(
        key="speicher_regelung",
        name="Speicher-Regelung",
        # Zustand = Modus-Empfehlung der Saldo-Regelung
        value_fn=lambda d: d.plan.regelung.modus if d.plan.regelung else None,
        attr_fn=lambda d: {
            "soll_w": d.plan.regelung.soll_w,
            "fehler_w": d.plan.regelung.fehler_w,
            "zuteilung": [
                {"name": z.name, "watt": z.watt}
                for z in d.plan.regelung.zuteilung
            ],
            "kaltreserve_aktiv": d.plan.regelung.reserve_aktiv,
            "kaltreserve_speicher": d.plan.regelung.reserve_namen,
        }
        if d.plan.regelung
        else {},
    ),
    HemsSensorDescription(
        key="heizkreis",
        name="Heizkreis",
        # Zustand = Modus-Empfehlung (heizen/kuehlen/aus)
        value_fn=lambda d: d.plan.heizung.modus if d.plan.heizung else None,
        attr_fn=lambda d: {
            "vorlauf_ziel_c": d.plan.heizung.vlt_ziel_c,
            "aussentemperatur_c": d.plan.heizung.t_aussen_c,
            "sommersperre": d.plan.heizung.sommer_sperre,
            "frostschutz": d.plan.heizung.frostschutz,
            "leise_empfohlen": d.plan.heizung.leise_empfohlen,
            # Gelerntes Verbrauchsmodell für die Bedarfsprognose
            "verbrauchsmodell": d.wp_modell,
        }
        if d.plan.heizung
        else {},
    ),
    HemsSensorDescription(
        key="empfehlung",
        name="Empfehlung",
        value_fn=lambda d: d.plan.empfehlung[:255],
        attr_fn=lambda d: {
            "ziel": d.ziel,
            "ev_zwang": d.ev_zwang,
            # Wallbox-/Lastregelung (HEMS stellt den Ladestrom selbst)
            "wallbox_ueberschuss_w": d.plan.ev_regelung.ueberschuss_w
            if d.plan.ev_regelung
            else None,
            "wallbox_soll_summe_w": d.plan.ev_regelung.soll_summe_w
            if d.plan.ev_regelung
            else None,
            "lasten": [
                {
                    "name": sp.name,
                    "laden": sp.laden,
                    "strom_a": sp.strom_a,
                    "grund": sp.grund,
                }
                for sp in d.plan.ev_regelung.lasten
            ]
            if d.plan.ev_regelung
            else [],
            "schaltbare": [
                {"name": sp.name, "an": sp.an, "grund": sp.grund}
                for sp in d.plan.schaltbare.lasten
            ]
            if d.plan.schaltbare
            else [],
            "prioritaeten": d.plan.prioritaeten,
            "speicher_bedarf_kwh": d.plan.speicher_bedarf_kwh,
            "speicher_kapazitaet_kwh": d.plan.speicher_kapazitaet_kwh,
            "wetter_morgen": d.wetter_morgen,
            "wetter_faktor_morgen": d.wetter_faktor_morgen,
            "morgen_knapp": d.plan.morgen_knapp,
            # Gelerntes Lastprofil (Phase 1: Validierung)
            "lastprofil_quelle": d.lastprofil_quelle,
            "lastprofil": d.lastprofil,
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
