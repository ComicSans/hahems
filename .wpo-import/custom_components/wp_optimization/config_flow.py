"""Einrichtung über die Oberfläche.

Nur Eingänge und ein Preset — es gibt nichts zu schalten. Die Integration ist
beratend: Steuerung passiert am Gerät oder im Energiemanagement.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .analysis.presets import lade_presets
from .const import (
    CONF_AUSSENTEMPERATUR,
    CONF_BETRIEBSART,
    CONF_DURCHFLUSS,
    CONF_LEISTUNG,
    CONF_PRESET,
    CONF_RUECKLAUF,
    CONF_STANDBY_W,
    CONF_STEUERUNG_AKTIV,
    CONF_STEUERUNG_GRUND,
    CONF_VERDICHTER,
    CONF_VORLAUF,
    DOMAIN,
)

PRESET_DIR = Path(__file__).parent / "presets"


def _temperatur() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["sensor", "number", "input_number"])
    )


def _beliebig(domains: list[str]) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domains))


def _schema(presets: dict[str, str], vorgabe: dict[str, Any]) -> vol.Schema:
    """Formular für Einrichtung und Optionen — bewusst dasselbe.

    Wer eine Rolle nachträglich verdrahtet, soll nicht neu einrichten müssen.
    """

    def vorhanden(schluessel: str, pflicht: bool = False):
        wert = vorgabe.get(schluessel)
        if wert not in (None, ""):
            return vol.Optional(schluessel, default=wert)
        return vol.Required(schluessel) if pflicht else vol.Optional(schluessel)

    return vol.Schema(
        {
            vorhanden(CONF_PRESET, True): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=k, label=v)
                        for k, v in sorted(presets.items(), key=lambda p: p[1])
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vorhanden(CONF_VORLAUF, True): _temperatur(),
            vorhanden(CONF_RUECKLAUF, True): _temperatur(),
            vorhanden(CONF_LEISTUNG, True): _beliebig(["sensor"]),
            vorhanden(CONF_AUSSENTEMPERATUR, True): _temperatur(),
            # Ohne Zähler tritt der Nennvolumenstrom des Presets ein; der COP
            # ist dann geschätzt und wird nie als belastbar gemeldet.
            vorhanden(CONF_DURCHFLUSS): _beliebig(["sensor"]),
            vorhanden(CONF_VERDICHTER): _beliebig(["sensor"]),
            vorhanden(CONF_BETRIEBSART): _beliebig(
                ["sensor", "select", "input_select", "climate"]
            ),
            vorhanden(CONF_STEUERUNG_AKTIV): _beliebig(
                ["binary_sensor", "switch", "input_boolean"]
            ),
            vorhanden(CONF_STEUERUNG_GRUND): _beliebig(
                ["sensor", "select", "input_select"]
            ),
            vol.Optional(
                CONF_STANDBY_W, default=vorgabe.get(CONF_STANDBY_W, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=2000, step=1, unit_of_measurement="W",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _preset_namen(hass) -> dict[str, str]:
    return {k: p.anzeigename for k, p in lade_presets(PRESET_DIR).items()}


class WpOptimizationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Einrichtungsdialog."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        presets = await self.hass.async_add_executor_job(_preset_namen, self.hass)
        if user_input is not None:
            aufgeraeumt = {k: v for k, v in user_input.items() if v not in (None, "")}
            return self.async_create_entry(
                title=presets.get(aufgeraeumt[CONF_PRESET], "WP-Optimierung"),
                data={},
                options=aufgeraeumt,
            )
        return self.async_show_form(
            step_id="user", data_schema=_schema(presets, {})
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return WpOptimizationOptionsFlow()


class WpOptimizationOptionsFlow(OptionsFlow):
    """Nachträglich Rollen verdrahten oder das Preset wechseln."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        presets = await self.hass.async_add_executor_job(_preset_namen, self.hass)
        if user_input is not None:
            aufgeraeumt = {k: v for k, v in user_input.items() if v not in (None, "")}
            return self.async_create_entry(title="", data=aufgeraeumt)
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(presets, dict(self.config_entry.options)),
        )
