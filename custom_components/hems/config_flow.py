"""Config- und Options-Flow: alle Geräte werden über die UI angelegt und gepflegt."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BASELINE_W,
    CONF_DEVICES,
    CONF_FREE_H,
    CONF_FREE_KWH,
    CONF_INVERT,
    CONF_INVERT_PV,
    CONF_METER,
    CONF_NIGHT_W,
    CONF_PRIORITY_MODE,
    CONF_PV_MINUS_BATTERY,
    CONF_PV_POWER,
    CONF_WEATHER,
    DEFAULT_ANTITAKT_PAUSE_MIN,
    DEFAULT_ANTITAKT_STARTS,
    DEFAULT_ANTITAKT_WINDOW_MIN,
    DEFAULT_BASE_TARGET,
    DEFAULT_BASELINE_W,
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
    DEFAULT_DEWPOINT_MARGIN_K,
    DEFAULT_FREE_H,
    DEFAULT_FREE_KWH,
    DEFAULT_HEAT_FROST_OFF_C,
    DEFAULT_HEAT_FROST_ON_C,
    DEFAULT_HEAT_LOCK_FROM,
    DEFAULT_HEAT_LOCK_TO,
    DEFAULT_HEAT_OFF_C,
    DEFAULT_HEAT_ON_C,
    DEFAULT_LEGIONELLA_TARGET,
    DEFAULT_MAX_CHARGE_W,
    DEFAULT_MAX_DISCHARGE_W,
    DEFAULT_NIGHT_W,
    DEFAULT_RESERVE_SOC,
    DEFAULT_VLT_MAX_C,
    DEFAULT_VLT_MIN_C,
    DEFAULT_VLT_MIN_COLD_C,
    DOMAIN,
    PRIORITY_AUTO,
    PRIORITY_BATTERY_FIRST,
    PRIORITY_EV_FIRST,
    ROLE_ANALYSIS,
    ROLE_FORECAST,
    ROLE_HEATING,
    ROLE_MODULATED,
    ROLE_STORAGE,
    ROLE_SWITCHABLE,
    ROLE_THERMAL,
)

# Labels für dynamisch erzeugte Auswahllisten (Übersetzungen greifen dort nicht)
ROLE_LABELS = {
    ROLE_FORECAST: "PV-Prognose",
    ROLE_STORAGE: "Speicher",
    ROLE_THERMAL: "Warmwasser",
    ROLE_HEATING: "Heizkreis",
    ROLE_SWITCHABLE: "Schaltbare Last",
    ROLE_MODULATED: "Modulierbare Last",
    ROLE_ANALYSIS: "Wärmepumpen-Analyse",
}


def _entity(
    domain: str | list[str] = "sensor", device_class: str | None = None
) -> selector.EntitySelector:
    config = selector.EntitySelectorConfig(domain=domain)
    if device_class is not None:
        config["device_class"] = device_class
    return selector.EntitySelector(config)


def _number(
    minimum: float, maximum: float, unit: str, step: float = 1
) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _weekday() -> selector.SelectSelector:
    """Wochentag fürs Legionellen-Fenster; "none" = deaktiviert."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=["none", "0", "1", "2", "3", "4", "5", "6"],
            translation_key="weekday",
        )
    )


def _priority_mode() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[PRIORITY_AUTO, PRIORITY_BATTERY_FIRST, PRIORITY_EV_FIRST],
            translation_key="priority_mode",
        )
    )


FORECAST_SCHEMA = vol.Schema(
    {
        vol.Required("name"): selector.TextSelector(),
        # Kein device_class-Filter: viele Prognose-Integrationen liefern
        # kWh-Sensoren ohne device_class "energy".
        vol.Required("energy_today"): _entity(),
        vol.Required("energy_remaining"): _entity(),
        vol.Required("energy_tomorrow"): _entity(),
    }
)

STORAGE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): selector.TextSelector(),
        # Kein device_class-Filter: SoC-Sensoren vieler Wechselrichter-
        # Integrationen haben keine device_class "battery".
        vol.Required("soc_entity"): _entity(),
        vol.Optional("power_entity"): _entity(device_class="power"),
        vol.Optional("charge_setpoint_entity"): _entity(["number", "input_number"]),
        vol.Optional("discharge_setpoint_entity"): _entity(["number", "input_number"]),
        vol.Optional("mode_entity"): _entity(["select", "input_select"]),
        vol.Optional("mode_charge_option"): selector.TextSelector(),
        vol.Optional("mode_discharge_option"): selector.TextSelector(),
        vol.Optional("soc_set_entity"): _entity(["number", "input_number"]),
        vol.Required("capacity_kwh"): _number(0.1, 100, "kWh", 0.01),
        vol.Required("reserve_soc", default=DEFAULT_RESERVE_SOC): _number(0, 100, "%"),
        vol.Required("max_charge_w", default=DEFAULT_MAX_CHARGE_W): _number(
            100, 20000, "W"
        ),
        vol.Required("max_discharge_w", default=DEFAULT_MAX_DISCHARGE_W): _number(
            100, 20000, "W"
        ),
        vol.Required("cold_reserve", default=False): selector.BooleanSelector(),
    }
)

THERMAL_SCHEMA = vol.Schema(
    {
        vol.Required("name"): selector.TextSelector(),
        vol.Optional("temp_entity"): _entity(device_class="temperature"),
        vol.Optional("control_entity"): _entity(
            ["water_heater", "switch", "input_boolean"]
        ),
        vol.Optional("setpoint_entity"): _entity(["number", "input_number"]),
        vol.Required("base_target", default=DEFAULT_BASE_TARGET): _number(
            30, 70, "°C"
        ),
        vol.Required("comfort_target", default=DEFAULT_COMFORT_TARGET): _number(
            30, 70, "°C"
        ),
        vol.Optional("block_start"): selector.TimeSelector(),
        vol.Optional("block_end"): selector.TimeSelector(),
        vol.Required("legionella_weekday", default="none"): _weekday(),
        vol.Optional("legionella_start"): selector.TimeSelector(),
        vol.Optional("legionella_end"): selector.TimeSelector(),
        vol.Required(
            "legionella_target", default=DEFAULT_LEGIONELLA_TARGET
        ): _number(50, 75, "°C"),
        vol.Required("boost_soc_on", default=DEFAULT_BOOST_SOC_ON): _number(
            0, 100, "%"
        ),
        vol.Required("boost_soc_off", default=DEFAULT_BOOST_SOC_OFF): _number(
            0, 100, "%"
        ),
        vol.Required(
            "boost_saldo_on_w", default=DEFAULT_BOOST_SALDO_ON_W
        ): _number(-20000, 0, "W", 50),
        vol.Required(
            "boost_saldo_off_w", default=DEFAULT_BOOST_SALDO_OFF_W
        ): _number(-5000, 5000, "W", 50),
    }
)

HEATING_SCHEMA = vol.Schema(
    {
        vol.Required("name"): selector.TextSelector(),
        vol.Required("outdoor_temp_entity"): _entity(device_class="temperature"),
        vol.Optional("demand_entity"): _entity(),
        vol.Optional("control_entity"): _entity(["climate", "select", "input_select"]),
        vol.Optional("setpoint_entity"): _entity(["number", "input_number"]),
        vol.Optional("mode_heat_option"): selector.TextSelector(),
        vol.Optional("mode_cool_option"): selector.TextSelector(),
        vol.Optional("mode_off_option"): selector.TextSelector(),
        vol.Optional("silent_switch_entity"): _entity(["switch", "input_boolean"]),
        vol.Optional("season_select_entity"): _entity(["input_select", "select"]),
        vol.Optional("fault_entity"): _entity(["binary_sensor", "sensor"]),
        vol.Optional("dhw_active_entity"): _entity(
            ["binary_sensor", "switch", "input_boolean"]
        ),
        vol.Optional("compressor_entity"): _entity(
            ["binary_sensor", "switch", "input_boolean"]
        ),
        vol.Optional("room_temp_entity"): _entity(device_class="temperature"),
        vol.Optional("room_humidity_entity"): _entity(device_class="humidity"),
        vol.Required(
            "dewpoint_margin_k", default=DEFAULT_DEWPOINT_MARGIN_K
        ): _number(0, 6, "K", 0.5),
        vol.Required("antitakt_starts", default=DEFAULT_ANTITAKT_STARTS): _number(
            0, 20, "Starts", 1
        ),
        vol.Required(
            "antitakt_window_min", default=DEFAULT_ANTITAKT_WINDOW_MIN
        ): _number(15, 240, "min", 5),
        vol.Required(
            "antitakt_pause_min", default=DEFAULT_ANTITAKT_PAUSE_MIN
        ): _number(5, 120, "min", 5),
        vol.Required("heat_on_c", default=DEFAULT_HEAT_ON_C): _number(
            -10, 25, "°C", 0.5
        ),
        vol.Required("heat_off_c", default=DEFAULT_HEAT_OFF_C): _number(
            -10, 30, "°C", 0.5
        ),
        vol.Required("cool_on_c", default=DEFAULT_COOL_ON_C): _number(
            15, 40, "°C", 0.5
        ),
        vol.Required("cool_off_c", default=DEFAULT_COOL_OFF_C): _number(
            10, 35, "°C", 0.5
        ),
        vol.Required("frost_on_c", default=DEFAULT_HEAT_FROST_ON_C): _number(
            -10, 15, "°C", 0.5
        ),
        vol.Required("frost_off_c", default=DEFAULT_HEAT_FROST_OFF_C): _number(
            -10, 15, "°C", 0.5
        ),
        vol.Required(
            "heat_lock_from_month", default=DEFAULT_HEAT_LOCK_FROM
        ): _number(1, 12, ""),
        vol.Required("heat_lock_to_month", default=DEFAULT_HEAT_LOCK_TO): _number(
            1, 12, ""
        ),
        vol.Required("curve_base_c", default=DEFAULT_CURVE_BASE_C): _number(
            25, 60, "°C", 0.1
        ),
        vol.Required("curve_slope", default=DEFAULT_CURVE_SLOPE): _number(
            0, 3, "K/K", 0.01
        ),
        vol.Required(
            "curve_from_analysis", default=False
        ): selector.BooleanSelector(),
        vol.Required("vlt_min_c", default=DEFAULT_VLT_MIN_C): _number(
            15, 40, "°C", 0.5
        ),
        vol.Required("vlt_min_cold_c", default=DEFAULT_VLT_MIN_COLD_C): _number(
            15, 40, "°C", 0.5
        ),
        vol.Required("vlt_max_c", default=DEFAULT_VLT_MAX_C): _number(
            25, 70, "°C", 0.5
        ),
        vol.Required("cool_vlt_c", default=DEFAULT_COOL_VLT_C): _number(
            10, 30, "°C", 0.5
        ),
    }
)

SWITCHABLE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): selector.TextSelector(),
        vol.Required("switch_entity"): _entity(["switch", "climate"]),
        vol.Optional("power_entity"): _entity(device_class="power"),
        vol.Required("min_on_min", default=20): _number(0, 240, "min"),
        vol.Required("min_off_min", default=10): _number(0, 240, "min"),
        vol.Required("max_block_min", default=120): _number(0, 720, "min"),
        vol.Required("priority", default=1): _number(1, 10, ""),
        vol.Required("heat_coupled", default=False): selector.BooleanSelector(),
    }
)

MODULATED_SCHEMA = vol.Schema(
    {
        vol.Required("name"): selector.TextSelector(),
        vol.Required("current_entity"): _entity(["number", "input_number"]),
        vol.Optional("switch_entity"): _entity(["switch", "input_boolean"]),
        vol.Optional("power_entity"): _entity(device_class="power"),
        vol.Required("min_a", default=6): _number(1, 32, "A"),
        vol.Required("max_a", default=16): _number(1, 32, "A"),
        vol.Required("phases", default=3): _number(1, 3, ""),
        vol.Required("min_on_min", default=10): _number(0, 240, "min"),
        vol.Required("min_off_min", default=10): _number(0, 240, "min"),
        vol.Required("priority", default=1): _number(1, 10, ""),
    }
)


def _preset_optionen() -> list[selector.SelectOptionDict]:
    """Mitgelieferte Gerätekennlinien als Auswahlliste.

    Beim Import gelesen und nicht bei jedem Formularaufruf: es sind zehn
    kleine Dateien, und ein Preset kommt nur mit einer neuen Version dazu.
    Fällt das Lesen aus, bleibt die Liste leer statt zu raten — dann sieht
    man im Dialog sofort, dass etwas mit der Installation nicht stimmt.
    """
    verzeichnis = Path(__file__).parent / "waermepumpe" / "presets"
    optionen: list[selector.SelectOptionDict] = []
    for datei in sorted(verzeichnis.glob("*.json")):
        try:
            roh = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        optionen.append(
            selector.SelectOptionDict(
                value=roh.get("schluessel", datei.stem),
                label=roh.get("anzeigename", datei.stem),
            )
        )
    return optionen


# Wärmepumpen-Analyse: fünf Pflichteingänge, zwei optionale. Sie kennt weder
# Protokolle noch Register — woher die Werte kommen, ist ihr gleich. Und sie
# hat bewusst keine Steuer-Entity: gestellt wird über die Rolle Heizkreis.
ANALYSIS_SCHEMA = vol.Schema(
    {
        vol.Required("name", default="Wärmepumpe"): selector.TextSelector(),
        vol.Required("preset"): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=_preset_optionen(),
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required("vorlauf_temp"): _entity(device_class="temperature"),
        vol.Required("ruecklauf_temp"): _entity(device_class="temperature"),
        vol.Required("leistung_elektrisch"): _entity(device_class="power"),
        vol.Required("aussentemperatur"): _entity(device_class="temperature"),
        # Optional, und das ist kein Zugeständnis: an vielen Anlagen ist der
        # Volumenstrom schlicht nicht auslesbar. Ohne ihn tritt der
        # Nennvolumenstrom des Presets ein, der COP ist geschätzt statt
        # gemessen, und die Datenbasis wird entsprechend gedeckelt.
        # Ohne device_class, weil ein Volumenstrom in HA keine trägt — die
        # Einheit wird aus `unit_of_measurement` gelesen, nie geraten.
        vol.Optional("durchfluss"): _entity(["sensor", "input_number"]),
        vol.Optional("verdichter_frequenz"): _entity(["sensor"]),
        vol.Optional("betriebsart"): _entity(["sensor", "climate", "select"]),
        vol.Required("standby_w", default=0): _number(0, 1000, "W", 1),
    }
)



def _halbjahr_versetzt(monat: int) -> int:
    """Denselben Monat auf der anderen Halbkugel: plus sechs, mit Umlauf."""
    return (monat + 5) % 12 + 1


def _rollen_vorgaben(hass, role: str) -> dict:
    """Vorbelegung eines Rollen-Formulars, die vom Standort abhängt.

    Bisher genau eine: die Sommersperre des Heizkreises. Die Vorgaben Mai bis
    September sind die Nordhalbkugel — auf der Südhalbkugel wären sie genau
    die Heizmonate, und HEMS empfähle im Winter nie Heizen. Die Sperre selbst
    kann den Jahreswechsel längst überspannen (`heat_lock_from_month` größer
    als `..._to_month`); es war nur die Vorbelegung, die eine Halbkugel
    unterstellte.

    Vorbelegung und nicht Automatik: Wer auf der Südhalbkugel wohnt, sieht im
    Formular November bis März stehen und kann es ändern. Ein Wert, den HEMS
    hinter dem Formular still umrechnet, wäre nicht mehr nachvollziehbar.
    """
    if role != ROLE_HEATING:
        return {}
    breite = getattr(hass.config, "latitude", None)
    if breite is None or breite >= 0:
        return {}
    return {
        "heat_lock_from_month": _halbjahr_versetzt(DEFAULT_HEAT_LOCK_FROM),
        "heat_lock_to_month": _halbjahr_versetzt(DEFAULT_HEAT_LOCK_TO),
    }


ROLE_SCHEMAS = {
    ROLE_FORECAST: FORECAST_SCHEMA,
    ROLE_STORAGE: STORAGE_SCHEMA,
    ROLE_THERMAL: THERMAL_SCHEMA,
    ROLE_HEATING: HEATING_SCHEMA,
    ROLE_SWITCHABLE: SWITCHABLE_SCHEMA,
    ROLE_MODULATED: MODULATED_SCHEMA,
    ROLE_ANALYSIS: ANALYSIS_SCHEMA,
}

# Onboarding-Assistent: Reihenfolge der Kategorie-Schritte
WIZARD_MENUS = {
    ROLE_FORECAST: ("forecast_menu", "add_forecast", "storage_menu"),
    ROLE_STORAGE: ("storage_menu", "add_storage", "thermal_menu"),
    ROLE_THERMAL: ("thermal_menu", "add_thermal", "heating_menu"),
    ROLE_HEATING: ("heating_menu", "add_heating", "switchable_menu"),
    ROLE_SWITCHABLE: ("switchable_menu", "add_switchable", "modulated_menu"),
    ROLE_MODULATED: ("modulated_menu", "add_modulated", "analysis_menu"),
    ROLE_ANALYSIS: ("analysis_menu", "add_analysis", "finish"),
}

EDIT_STEPS = {
    ROLE_FORECAST: "edit_forecast",
    ROLE_STORAGE: "edit_storage",
    ROLE_THERMAL: "edit_thermal",
    ROLE_HEATING: "edit_heating",
    ROLE_SWITCHABLE: "edit_switchable",
    ROLE_MODULATED: "edit_modulated",
    ROLE_ANALYSIS: "edit_analysis",
}

# Zähler, Grundlasten und Prioritäten: identisch bei Einrichtung und Grundwerten
GENERAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_METER): _entity(device_class="power"),
        vol.Required(CONF_INVERT, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_PV_POWER): _entity(device_class="power"),
        vol.Required(CONF_INVERT_PV, default=False): selector.BooleanSelector(),
        vol.Required(
            CONF_PV_MINUS_BATTERY, default=False
        ): selector.BooleanSelector(),
        vol.Optional(CONF_WEATHER): _entity("weather"),
        vol.Required(CONF_BASELINE_W, default=DEFAULT_BASELINE_W): _number(
            50, 5000, "W"
        ),
        vol.Required(CONF_NIGHT_W, default=DEFAULT_NIGHT_W): _number(50, 5000, "W"),
        vol.Required(CONF_PRIORITY_MODE, default=PRIORITY_AUTO): _priority_mode(),
        vol.Required(CONF_FREE_KWH, default=DEFAULT_FREE_KWH): _number(
            0.1, 50, "kWh", 0.1
        ),
        vol.Required(CONF_FREE_H, default=DEFAULT_FREE_H): _number(0.25, 24, "h", 0.25),
    }
)


class HemsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    # 2: schaltbare Lasten kennen `heat_coupled` (siehe async_migrate_entry).
    VERSION = 2

    def __init__(self) -> None:
        self._general: dict = {}
        self._devices: list[dict] = []

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            self._general = user_input
            return await self.async_step_forecast_menu()
        return self.async_show_form(step_id="user", data_schema=GENERAL_SCHEMA)

    # -- Geführter Assistent: eine Kategorie pro Schritt -------------------

    def _wizard_menu(self, role: str):
        step_id, add_step, next_step = WIZARD_MENUS[role]
        count = sum(1 for d in self._devices if d["role"] == role)
        return self.async_show_menu(
            step_id=step_id,
            menu_options=[add_step, next_step],
            description_placeholders={"count": str(count)},
        )

    async def async_step_forecast_menu(self, user_input=None):
        return self._wizard_menu(ROLE_FORECAST)

    async def async_step_storage_menu(self, user_input=None):
        return self._wizard_menu(ROLE_STORAGE)

    async def async_step_thermal_menu(self, user_input=None):
        return self._wizard_menu(ROLE_THERMAL)

    async def async_step_heating_menu(self, user_input=None):
        return self._wizard_menu(ROLE_HEATING)

    async def async_step_switchable_menu(self, user_input=None):
        return self._wizard_menu(ROLE_SWITCHABLE)

    async def async_step_modulated_menu(self, user_input=None):
        return self._wizard_menu(ROLE_MODULATED)

    async def async_step_analysis_menu(self, user_input=None):
        return self._wizard_menu(ROLE_ANALYSIS)

    async def _async_add_step(self, role: str, step_id: str, user_input):
        if user_input is not None:
            self._devices.append({"id": uuid4().hex, "role": role, **user_input})
            return self._wizard_menu(role)
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                ROLE_SCHEMAS[role], _rollen_vorgaben(self.hass, role)
            ),
        )

    async def async_step_add_forecast(self, user_input=None):
        return await self._async_add_step(ROLE_FORECAST, "add_forecast", user_input)

    async def async_step_add_storage(self, user_input=None):
        return await self._async_add_step(ROLE_STORAGE, "add_storage", user_input)

    async def async_step_add_thermal(self, user_input=None):
        return await self._async_add_step(ROLE_THERMAL, "add_thermal", user_input)

    async def async_step_add_heating(self, user_input=None):
        return await self._async_add_step(ROLE_HEATING, "add_heating", user_input)

    async def async_step_add_switchable(self, user_input=None):
        return await self._async_add_step(ROLE_SWITCHABLE, "add_switchable", user_input)

    async def async_step_add_modulated(self, user_input=None):
        return await self._async_add_step(ROLE_MODULATED, "add_modulated", user_input)

    async def async_step_add_analysis(self, user_input=None):
        return await self._async_add_step(ROLE_ANALYSIS, "add_analysis", user_input)

    async def async_step_finish(self, user_input=None):
        return self.async_create_entry(
            title="HEMS", data=self._general, options={CONF_DEVICES: self._devices}
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HemsOptionsFlow()


class HemsOptionsFlow(config_entries.OptionsFlow):
    """Geräte anlegen, bearbeiten, entfernen; Grundwerte und Prioritäten ändern."""

    _edit_id: str | None = None

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_forecast",
                "add_storage",
                "add_thermal",
                "add_heating",
                "add_switchable",
                "add_modulated",
                "add_analysis",
                "edit_device",
                "remove_device",
                "general",
            ],
        )

    # -- Helfer ------------------------------------------------------------

    def _devices(self) -> list[dict]:
        return list(self.config_entry.options.get(CONF_DEVICES, []))

    def _save_devices(self, devices: list[dict]):
        options = dict(self.config_entry.options)
        options[CONF_DEVICES] = devices
        return self.async_create_entry(title="", data=options)

    def _device_picker(self, devices: list[dict]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("device"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=d["id"],
                                label=f"{d['name']} ({ROLE_LABELS.get(d['role'], d['role'])})",
                            )
                            for d in devices
                        ]
                    )
                )
            }
        )

    # -- Geräte anlegen ----------------------------------------------------

    async def _async_add_step(self, role: str, step_id: str, user_input):
        if user_input is not None:
            devices = self._devices()
            devices.append({"id": uuid4().hex, "role": role, **user_input})
            return self._save_devices(devices)
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                ROLE_SCHEMAS[role], _rollen_vorgaben(self.hass, role)
            ),
        )

    async def async_step_add_forecast(self, user_input=None):
        return await self._async_add_step(ROLE_FORECAST, "add_forecast", user_input)

    async def async_step_add_storage(self, user_input=None):
        return await self._async_add_step(ROLE_STORAGE, "add_storage", user_input)

    async def async_step_add_thermal(self, user_input=None):
        return await self._async_add_step(ROLE_THERMAL, "add_thermal", user_input)

    async def async_step_add_heating(self, user_input=None):
        return await self._async_add_step(ROLE_HEATING, "add_heating", user_input)

    async def async_step_add_switchable(self, user_input=None):
        return await self._async_add_step(ROLE_SWITCHABLE, "add_switchable", user_input)

    async def async_step_add_modulated(self, user_input=None):
        return await self._async_add_step(ROLE_MODULATED, "add_modulated", user_input)

    async def async_step_add_analysis(self, user_input=None):
        return await self._async_add_step(ROLE_ANALYSIS, "add_analysis", user_input)

    # -- Geräte bearbeiten -------------------------------------------------

    async def async_step_edit_device(self, user_input=None):
        devices = self._devices()
        if not devices:
            return self.async_abort(reason="no_devices")
        if user_input is not None:
            self._edit_id = user_input["device"]
            device = next((d for d in devices if d["id"] == self._edit_id), None)
            if device is None or device["role"] not in EDIT_STEPS:
                return self.async_abort(reason="device_not_found")
            step = EDIT_STEPS[device["role"]]
            return await getattr(self, f"async_step_{step}")()
        return self.async_show_form(
            step_id="edit_device", data_schema=self._device_picker(devices)
        )

    async def _async_edit_step(self, role: str, step_id: str, user_input):
        devices = self._devices()
        device = next((d for d in devices if d["id"] == self._edit_id), None)
        if device is None:
            return self.async_abort(reason="device_not_found")
        if user_input is not None:
            updated = {"id": device["id"], "role": role, **user_input}
            return self._save_devices(
                [updated if d["id"] == device["id"] else d for d in devices]
            )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                ROLE_SCHEMAS[role], device
            ),
            description_placeholders={"name": device.get("name", "")},
        )

    async def async_step_edit_forecast(self, user_input=None):
        return await self._async_edit_step(ROLE_FORECAST, "edit_forecast", user_input)

    async def async_step_edit_storage(self, user_input=None):
        return await self._async_edit_step(ROLE_STORAGE, "edit_storage", user_input)

    async def async_step_edit_thermal(self, user_input=None):
        return await self._async_edit_step(ROLE_THERMAL, "edit_thermal", user_input)

    async def async_step_edit_heating(self, user_input=None):
        return await self._async_edit_step(ROLE_HEATING, "edit_heating", user_input)

    async def async_step_edit_switchable(self, user_input=None):
        return await self._async_edit_step(
            ROLE_SWITCHABLE, "edit_switchable", user_input
        )

    async def async_step_edit_modulated(self, user_input=None):
        return await self._async_edit_step(
            ROLE_MODULATED, "edit_modulated", user_input
        )

    async def async_step_edit_analysis(self, user_input=None):
        return await self._async_edit_step(ROLE_ANALYSIS, "edit_analysis", user_input)

    # -- Entfernen und Grundwerte ------------------------------------------

    async def async_step_remove_device(self, user_input=None):
        devices = self._devices()
        if user_input is not None:
            return self._save_devices(
                [d for d in devices if d["id"] != user_input["device"]]
            )
        if not devices:
            return self.async_abort(reason="no_devices")
        return self.async_show_form(
            step_id="remove_device", data_schema=self._device_picker(devices)
        )

    async def async_step_general(self, user_input=None):
        if user_input is not None:
            options = dict(self.config_entry.options)
            options.update(user_input)
            return self.async_create_entry(title="", data=options)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="general",
            data_schema=self.add_suggested_values_to_schema(GENERAL_SCHEMA, current),
        )
