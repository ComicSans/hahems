"""Konstanten für die HEMS-Integration."""

DOMAIN = "hems"

CONF_METER = "meter_entity"
CONF_INVERT = "invert_meter"
CONF_BASELINE_W = "baseline_load_w"
CONF_NIGHT_W = "night_load_w"
CONF_DEVICES = "devices"
CONF_PRIORITY_MODE = "priority_mode"

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
