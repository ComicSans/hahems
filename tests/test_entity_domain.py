"""Domänen-Unterschiede beim Lesen und Schalten (entity_domain.py).

Hintergrund: `switch_entity` durfte immer schon eine `climate`-Entität sein,
aber Lesen und Schreiben verglichen hart gegen `"on"`. Eine climate-Entität
steht nie auf `on`, sondern auf ihrem HVAC-Modus — die Anlage galt damit
dauerhaft als aus, obwohl sie heizte: `min_on` wirkungslos, die gelernte
Leistung nie aktualisiert, im Lastfluss „aus". Und beim Schreiben griff der
Idempotenz-Vergleich nur in die Aus-Richtung, sodass HEMS immer wieder
`climate.turn_on` rief — einen Service, den viele Integrationen gar nicht
anbieten.
"""
from __future__ import annotations

import pytest

from hems.entity_domain import ist_an, schalt_service


# --- Lesen --------------------------------------------------------------------
@pytest.mark.parametrize("zustand", ["heat", "cool", "auto", "heat_cool", "dry"])
def test_climate_ist_an_in_jedem_modus_ausser_off(zustand: str):
    assert ist_an("climate.wp", zustand) is True


def test_climate_off_ist_aus():
    assert ist_an("climate.wp", "off") is False


def test_switch_bleibt_beim_alten_verhalten():
    assert ist_an("switch.wp", "on") is True
    assert ist_an("switch.wp", "off") is False


@pytest.mark.parametrize("zustand", ["unavailable", "unknown", None, ""])
def test_nicht_erreichbar_zaehlt_als_aus(zustand):
    """Sonst ginge `unavailable` bei climate als „läuft" durch."""
    assert ist_an("climate.wp", zustand) is False
    assert ist_an("switch.wp", zustand) is False


# --- Schreiben ----------------------------------------------------------------
def test_climate_wird_ueber_set_hvac_mode_geschaltet():
    """Nicht turn_on: das ist ein optionales Geräte-Feature."""
    assert schalt_service("climate.wp", True, "heat") == (
        "climate",
        "set_hvac_mode",
        {"hvac_mode": "heat"},
    )


def test_climate_aus_ist_der_modus_off():
    assert schalt_service("climate.wp", False, "heat") == (
        "climate",
        "set_hvac_mode",
        {"hvac_mode": "off"},
    )


def test_climate_ohne_konfigurierten_modus_faellt_auf_heat():
    """`heat` kennt jede Heizungs-Integration; `auto` nicht."""
    _, _, daten = schalt_service("climate.wp", True)
    assert daten == {"hvac_mode": "heat"}


def test_eigener_modus_wird_uebernommen():
    _, _, daten = schalt_service("climate.wp", True, "auto")
    assert daten == {"hvac_mode": "auto"}


def test_switch_nutzt_weiter_turn_on_und_turn_off():
    assert schalt_service("switch.wp", True) == ("switch", "turn_on", {})
    assert schalt_service("switch.wp", False) == ("switch", "turn_off", {})


def test_input_boolean_wird_wie_ein_switch_behandelt():
    assert schalt_service("input_boolean.wp", True) == (
        "input_boolean",
        "turn_on",
        {},
    )
