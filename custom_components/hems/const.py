"""Konstanten für die HEMS-Integration."""

DOMAIN = "hems"

CONF_METER = "meter_entity"
CONF_PV_POWER = "pv_power_entity"
CONF_INVERT = "invert_meter"
CONF_BASELINE_W = "baseline_load_w"
CONF_NIGHT_W = "night_load_w"
CONF_DEVICES = "devices"
CONF_PRIORITY_MODE = "priority_mode"
CONF_WEATHER = "weather_entity"
CONF_FREE_KWH = "free_capacity_kwh"
CONF_FREE_H = "free_capacity_h"

PRIORITY_AUTO = "auto"
PRIORITY_BATTERY_FIRST = "battery_first"
PRIORITY_EV_FIRST = "ev_first"

ROLE_FORECAST = "forecast"
ROLE_STORAGE = "storage"
ROLE_THERMAL = "thermal"
ROLE_SWITCHABLE = "switchable_load"
ROLE_MODULATED = "modulated_load"

MODE_OBSERVE = "beobachten"
MODE_OFF = "aus"

DEFAULT_BASELINE_W = 500
DEFAULT_NIGHT_W = 400
DEFAULT_RESERVE_SOC = 10
DEFAULT_MAX_CHARGE_W = 1200
DEFAULT_BASE_TARGET = 48
DEFAULT_COMFORT_TARGET = 60
DEFAULT_FREE_KWH = 3.0
DEFAULT_FREE_H = 1.0

# PV-Ertragsfaktor (0–1) je Wetterlage, falls die Vorhersage keinen
# Bewölkungsgrad liefert. Diffuses Licht bringt auch bedeckt noch Ertrag.
WEATHER_CONDITION_FACTORS = {
    "sunny": 1.0,
    "clear-night": 1.0,
    "windy": 0.9,
    "windy-variant": 0.8,
    "partlycloudy": 0.65,
    "cloudy": 0.35,
    "fog": 0.25,
    "rainy": 0.25,
    "lightning": 0.25,
    "lightning-rainy": 0.2,
    "pouring": 0.15,
    "hail": 0.15,
    "snowy": 0.15,
    "snowy-rainy": 0.15,
    "exceptional": 0.5,
}
