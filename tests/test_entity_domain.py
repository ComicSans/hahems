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

from hems.entity_domain import (
    BETRIEBSART_FREMD,
    BETRIEBSART_HEIZEN,
    BETRIEBSART_KUEHLEN,
    betriebsart,
    ist_an,
    schalt_service,
)


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


# --- Betriebsart --------------------------------------------------------------
#
# Zweiter Teil derselben Geschichte: „an" zu erkennen reichte nicht. Am
# 04.08.2026 nahm die Sommersperre eine Anlage weg, die im Modus `heat_cool` bei
# 39 °C kühlte — für HEMS war sie schlicht „an", und alles Weitere entschieden
# Heizungsregeln, die vom Kühlen nichts wissen.
def test_switch_hat_immer_die_betriebsart_heizen():
    """Eine Steckdose oder ein SG-Ready-Kontakt kennt keinen Modus."""
    assert betriebsart("switch.wp", "on") == BETRIEBSART_HEIZEN
    assert betriebsart("switch.wp", "off") == BETRIEBSART_HEIZEN


def test_heiz_modus_ist_heizen():
    assert betriebsart("climate.wp", "heat", "heat", "cool") == BETRIEBSART_HEIZEN


def test_kuehl_modus_ist_kuehlen():
    assert betriebsart("climate.wp", "cool", "heat", "cool") == BETRIEBSART_KUEHLEN


@pytest.mark.parametrize("modus", ["heat_cool", "auto", "dry", "fan_only"])
def test_nicht_zugeordneter_modus_ist_fremd(modus: str):
    """Dort entscheidet die Anlage selbst, ob sie heizt oder kühlt."""
    assert betriebsart("climate.wp", modus, "heat", "cool") == BETRIEBSART_FREMD


def test_ohne_kuehl_modus_ist_cool_fremd():
    """Wer keinen Kühl-Modus angibt, bekommt die vorsichtige Auslegung: HEMS
    regelt dann nur das Heizen und lässt die Kühlung unangetastet."""
    assert betriebsart("climate.wp", "cool", "heat") == BETRIEBSART_FREMD


def test_off_gilt_als_heizen():
    """Sonst könnten Frostschutz und Heizgrenze eine abgeschaltete Anlage nicht
    mehr beurteilen. Wer den zuletzt aktiven Modus kennt, reicht ihn statt `off`
    herein — das tut der Coordinator."""
    assert betriebsart("climate.wp", "off", "heat", "cool") == BETRIEBSART_HEIZEN


@pytest.mark.parametrize("zustand", ["unavailable", "unknown", None])
def test_nicht_erreichbar_gilt_als_heizen(zustand):
    assert betriebsart("climate.wp", zustand, "heat", "cool") == BETRIEBSART_HEIZEN


# --- Einschalten in die richtige Betriebsart ----------------------------------
def test_einschalten_im_kuehlbetrieb_trifft_den_kuehl_modus():
    """Ohne das käme eine im Kühlbetrieb abgeschaltete Anlage als Heizung
    wieder hoch — bei 39 °C Außentemperatur die Umkehr dessen, was gebraucht
    wird."""
    _, _, daten = schalt_service(
        "climate.wp", True, "heat", "cool", BETRIEBSART_KUEHLEN
    )
    assert daten == {"hvac_mode": "cool"}


def test_einschalten_im_heizbetrieb_trifft_den_heiz_modus():
    _, _, daten = schalt_service(
        "climate.wp", True, "heat", "cool", BETRIEBSART_HEIZEN
    )
    assert daten == {"hvac_mode": "heat"}


def test_kuehlbetrieb_ohne_kuehl_modus_faellt_auf_den_heiz_modus():
    """Kann nur auftreten, wenn die Konfiguration nachträglich geleert wurde —
    dann ist der Heiz-Modus die einzige belegte Angabe."""
    _, _, daten = schalt_service("climate.wp", True, "heat", None, BETRIEBSART_KUEHLEN)
    assert daten == {"hvac_mode": "heat"}


def test_ausschalten_ignoriert_die_betriebsart():
    _, _, daten = schalt_service(
        "climate.wp", False, "heat", "cool", BETRIEBSART_KUEHLEN
    )
    assert daten == {"hvac_mode": "off"}
