"""Geräte-agnostisches Rollenmodell.

Der Planner arbeitet ausschließlich gegen diese Rollen; welche realen Geräte
dahinterstehen, entscheidet die Konfiguration (Options-Flow).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .const import (
    DEFAULT_BASE_TARGET,
    DEFAULT_COMFORT_TARGET,
    DEFAULT_MAX_CHARGE_W,
    DEFAULT_MAX_DISCHARGE_W,
    DEFAULT_RESERVE_SOC,
    ROLE_FORECAST,
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
    # Stellgrößen: aktuelle Lade-/Einspeiseleistung in W setzen (z. B. Zendure
    # Input/Output-Limit). Ohne diese Entitäten wird der Speicher nur beobachtet.
    charge_setpoint_entity: str | None = None
    discharge_setpoint_entity: str | None = None


@dataclass
class ThermalStore:
    id: str
    name: str
    temp_entity: str | None = None
    base_target: float = DEFAULT_BASE_TARGET
    comfort_target: float = DEFAULT_COMFORT_TARGET
    # Sperrzeit als lokale Uhrzeiten "HH:MM:SS". In diesem Fenster wird weder
    # Basis- noch Komfortladung empfohlen; block_end < block_start bedeutet ein
    # Fenster über Mitternacht (z. B. 18:00 → 06:00). Gleiche Zeiten = keine
    # Sperre.
    block_start: str | None = None
    block_end: str | None = None


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
    priority: int = 1


@dataclass
class DeviceRegistry:
    forecasts: list[ForecastSource] = field(default_factory=list)
    storages: list[Storage] = field(default_factory=list)
    thermals: list[ThermalStore] = field(default_factory=list)
    switchables: list[SwitchableLoad] = field(default_factory=list)
    modulateds: list[ModulatedLoad] = field(default_factory=list)


_ROLE_CLASSES = {
    ROLE_FORECAST: (ForecastSource, "forecasts"),
    ROLE_STORAGE: (Storage, "storages"),
    ROLE_THERMAL: (ThermalStore, "thermals"),
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
