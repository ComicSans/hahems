"""Geräte-agnostisches Rollenmodell.

Der Planner arbeitet ausschließlich gegen diese Rollen; welche realen Geräte
dahinterstehen, entscheidet die Konfiguration (Options-Flow).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .const import (
    DEFAULT_BASE_TARGET,
    DEFAULT_BOOST_SALDO_OFF_W,
    DEFAULT_BOOST_SALDO_ON_W,
    DEFAULT_BOOST_SOC_OFF,
    DEFAULT_BOOST_SOC_ON,
    DEFAULT_COMFORT_TARGET,
    DEFAULT_COOL_OFF_C,
    DEFAULT_COOL_ON_C,
    DEFAULT_COOL_VLT_C,
    DEFAULT_CURVE_BASE_C,
    DEFAULT_CURVE_SLOPE,
    DEFAULT_HEAT_LOCK_FROM,
    DEFAULT_HEAT_LOCK_TO,
    DEFAULT_HEAT_OFF_C,
    DEFAULT_HEAT_ON_C,
    DEFAULT_LEGIONELLA_TARGET,
    DEFAULT_MAX_CHARGE_W,
    DEFAULT_MAX_DISCHARGE_W,
    DEFAULT_RESERVE_SOC,
    DEFAULT_VLT_MAX_C,
    DEFAULT_VLT_MIN_C,
    DEFAULT_VLT_MIN_COLD_C,
    ROLE_FORECAST,
    ROLE_HEATING,
    ROLE_MODULATED,
    ROLE_STORAGE,
    ROLE_SWITCHABLE,
    ROLE_THERMAL,
)


@dataclass
class ForecastSource:
    id: str
    name: str
    energy_today: str
    energy_remaining: str
    energy_tomorrow: str


@dataclass
class Storage:
    id: str
    name: str
    soc_entity: str
    capacity_kwh: float
    reserve_soc: float = DEFAULT_RESERVE_SOC
    max_charge_w: float = DEFAULT_MAX_CHARGE_W
    max_discharge_w: float = DEFAULT_MAX_DISCHARGE_W
    power_entity: str | None = None
    # Stellgrößen: aktuelle Lade-/Entladeleistung in W setzen (z. B. Zendure
    # Input/Output-Limit). Ohne diese Entitäten wird der Speicher nur beobachtet.
    charge_setpoint_entity: str | None = None
    discharge_setpoint_entity: str | None = None
    # Optionaler Richtungs-Select (z. B. Zendure ac_mode): wird beim Laden auf
    # mode_charge_option, beim Entladen auf mode_discharge_option gestellt.
    # Ohne diese drei Felder werden nur die Leistungslimits geschrieben.
    mode_entity: str | None = None
    mode_charge_option: str | None = None
    mode_discharge_option: str | None = None
    # Optionaler geräteseitiger Ziel-SoC (z. B. Zendure soc_set): wird jeden
    # Zyklus auf den Ladedeckel gesetzt. Nötig für Geräte, die im Lademodus
    # nach ihrem EIGENEN Ziel-SoC laden und den Leistungs-Setpoint (charge)
    # dabei ignorieren — dann kappt erst der Ziel-SoC das Laden am Deckel.
    soc_set_entity: str | None = None
    # Kaltreserve: nimmt am Entladen erst teil, wenn der mittlere SoC der
    # übrigen Speicher die Reserve-Schwelle unterschreitet (mit Hysterese).
    # Geladen wird sie immer mit, proportional zur freien Kapazität.
    cold_reserve: bool = False


@dataclass
class ThermalStore:
    id: str
    name: str
    temp_entity: str | None = None
    # Steuer-Entity (water_heater) für den Auto-Modus: On/Off + Sollwert.
    # Ohne dieses Entity wird die WW-Empfehlung nur angezeigt, nicht gestellt.
    control_entity: str | None = None
    base_target: float = DEFAULT_BASE_TARGET
    comfort_target: float = DEFAULT_COMFORT_TARGET
    # Sperrzeit als lokale Uhrzeiten "HH:MM:SS". In diesem Fenster wird weder
    # Basis- noch Komfortladung empfohlen; block_end < block_start bedeutet ein
    # Fenster über Mitternacht (z. B. 18:00 → 06:00). Gleiche Zeiten = keine
    # Sperre.
    block_start: str | None = None
    block_end: str | None = None
    # Legionellenschutz: wöchentliches Fenster (Wochentag + lokale Uhrzeiten),
    # in dem der Sollwert unabhängig vom Überschuss auf legionella_target
    # angehoben wird — notfalls aus dem Netz. "none"/leer = deaktiviert.
    legionella_weekday: str | int | None = None  # 0 = Montag … 6 = Sonntag
    legionella_start: str | None = None
    legionella_end: str | None = None
    legionella_target: float = DEFAULT_LEGIONELLA_TARGET
    # PV-Boost auf den Komfort-Sollwert nur, wenn der Speicher fast voll ist
    # UND kräftig eingespeist wird. Jeweils Ein-/Aus-Schwelle (Hysterese).
    boost_soc_on: float = DEFAULT_BOOST_SOC_ON
    boost_soc_off: float = DEFAULT_BOOST_SOC_OFF
    boost_saldo_on_w: float = DEFAULT_BOOST_SALDO_ON_W
    boost_saldo_off_w: float = DEFAULT_BOOST_SALDO_OFF_W


@dataclass
class HeatingCircuit:
    """Witterungsgeführter Heizkreis (z. B. Wärmepumpe): Modus-Empfehlung
    (heizen/kühlen/aus) über Außentemperatur-Schwellen mit Hysterese plus
    Vorlauf-Sollwert aus der Heizkurve."""

    id: str
    name: str
    outdoor_temp_entity: str
    # Wärmeanforderung der Räume in % (0–100), z. B. aus einem PID-Thermostat
    # oder einem Template-Sensor; hebt die Vorlaufkurve an. Ohne Anforderung
    # (< 1 %) fällt der Vorlauf auf das Minimum (Absenkbetrieb).
    demand_entity: str | None = None
    # Steuer-Entities für den Auto-Modus (alle optional): climate für Modus +
    # Vorlauf-Soll, Schalter für den Flüsterbetrieb, input_select für die
    # Saison-Statistik-Richtung. Ohne control_entity nur Anzeige.
    control_entity: str | None = None
    silent_switch_entity: str | None = None
    season_select_entity: str | None = None
    heat_on_c: float = DEFAULT_HEAT_ON_C
    heat_off_c: float = DEFAULT_HEAT_OFF_C
    cool_on_c: float = DEFAULT_COOL_ON_C
    cool_off_c: float = DEFAULT_COOL_OFF_C
    # Sommersperre: in diesen Monaten (einschließlich) wird Heizen nie
    # empfohlen, egal wie kalt es ist.
    heat_lock_from_month: int = DEFAULT_HEAT_LOCK_FROM
    heat_lock_to_month: int = DEFAULT_HEAT_LOCK_TO
    curve_base_c: float = DEFAULT_CURVE_BASE_C
    curve_slope: float = DEFAULT_CURVE_SLOPE
    vlt_min_c: float = DEFAULT_VLT_MIN_C
    vlt_min_cold_c: float = DEFAULT_VLT_MIN_COLD_C
    vlt_max_c: float = DEFAULT_VLT_MAX_C
    cool_vlt_c: float = DEFAULT_COOL_VLT_C


@dataclass
class SwitchableLoad:
    id: str
    name: str
    switch_entity: str
    power_entity: str | None = None
    min_on_min: int = 20
    min_off_min: int = 10
    max_block_min: int = 120
    priority: int = 1


@dataclass
class ModulatedLoad:
    id: str
    name: str
    current_entity: str
    switch_entity: str | None = None
    power_entity: str | None = None
    min_a: float = 6
    max_a: float = 16
    phases: int = 3
    min_on_min: int = 10
    min_off_min: int = 10
    priority: int = 1


@dataclass
class DeviceRegistry:
    forecasts: list[ForecastSource] = field(default_factory=list)
    storages: list[Storage] = field(default_factory=list)
    thermals: list[ThermalStore] = field(default_factory=list)
    heatings: list[HeatingCircuit] = field(default_factory=list)
    switchables: list[SwitchableLoad] = field(default_factory=list)
    modulateds: list[ModulatedLoad] = field(default_factory=list)


_ROLE_CLASSES = {
    ROLE_FORECAST: (ForecastSource, "forecasts"),
    ROLE_STORAGE: (Storage, "storages"),
    ROLE_THERMAL: (ThermalStore, "thermals"),
    ROLE_HEATING: (HeatingCircuit, "heatings"),
    ROLE_SWITCHABLE: (SwitchableLoad, "switchables"),
    ROLE_MODULATED: (ModulatedLoad, "modulateds"),
}


def parse_devices(raw: list[dict]) -> DeviceRegistry:
    """Options-Liste (dicts mit 'role') in das Rollenmodell übersetzen."""
    registry = DeviceRegistry()
    for item in raw:
        role = item.get("role")
        if role not in _ROLE_CLASSES:
            continue
        cls, attr = _ROLE_CLASSES[role]
        fields = {k: v for k, v in item.items() if k in cls.__dataclass_fields__}
        getattr(registry, attr).append(cls(**fields))
    # Lasten in Nutzer-Priorität (1 = höchste), damit Konsumenten sie der
    # Reihe nach abarbeiten können.
    registry.switchables.sort(key=lambda d: d.priority)
    registry.modulateds.sort(key=lambda d: d.priority)
    return registry
