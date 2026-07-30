"""Reine Warmwasser-Aktuierungsentscheidung (actuation.plan_ww_action).

Die Aktuierung selbst (actuator.py) importiert Home Assistant und ist in der
HA-freien Suite nicht erreichbar. Die Entscheidung, WELCHER Service mit welchem
Wert nötig ist, ist bewusst als pure Funktion herausgezogen und wird hier
festgenagelt — inklusive der beiden Gerätformen (water_heater trägt den Sollwert
selbst, ein Schalter braucht eine separate Number) und der Idempotenz.
"""
from __future__ import annotations

from hems.actuation import WwAction, plan_ww_action


def _act(**kw) -> WwAction | None:
    base = dict(
        status="basis",
        soll_c=48.0,
        domain="water_heater",
        state="on",
        min_runtime_elapsed=True,
        current_setpoint=48.0,
        has_setpoint_entity=False,
    )
    base.update(kw)
    return plan_ww_action(**base)


# --- Gerät nicht ansprechbar -------------------------------------------------


def test_unavailable_keine_aktion():
    assert _act(state="unavailable") is None


def test_unknown_keine_aktion():
    assert _act(state="unknown") is None


def test_state_none_keine_aktion():
    assert _act(state=None) is None


# --- Ausschalten -------------------------------------------------------------


def test_status_aus_schaltet_ab():
    assert _act(status="aus", state="on") == WwAction("turn_off")


def test_fehlender_sollwert_schaltet_ab():
    # soll_c None wird wie "aus" behandelt, unabhängig vom status.
    assert _act(status="basis", soll_c=None, state="on") == WwAction("turn_off")


def test_aus_respektiert_mindestlaufzeit():
    # Noch keine Mindestlaufzeit erreicht -> nicht abschalten (gegen Takten).
    assert _act(status="aus", state="on", min_runtime_elapsed=False) is None


def test_aus_wenn_bereits_aus_keine_aktion():
    assert _act(status="aus", state="off") is None


# --- Einschalten -------------------------------------------------------------


def test_einschalten_bevor_sollwert():
    # Ausgeschaltet, aber Betrieb erwünscht: erst einschalten, Sollwert folgt
    # im nächsten Zyklus (auch wenn der Sollwert bereits abweicht).
    assert _act(state="off", current_setpoint=30.0) == WwAction("turn_on")


def test_einschalten_switch_variante():
    assert _act(domain="switch", state="off", has_setpoint_entity=True) == WwAction(
        "turn_on"
    )


# --- Sollwert: water_heater --------------------------------------------------


def test_water_heater_sollwert_wird_gestellt():
    assert _act(current_setpoint=40.0) == WwAction("set_temperature", 48.0)


def test_water_heater_sollwert_passt_keine_aktion():
    assert _act(current_setpoint=48.0) is None


def test_water_heater_sollwert_unbekannt_wird_gestellt():
    assert _act(current_setpoint=None) == WwAction("set_temperature", 48.0)


def test_water_heater_vergleich_ganzzahlig():
    # Vergleich auf ganze °C (wie die abgelöste Automation): 48.9 gilt als 48.
    assert _act(current_setpoint=48.9, soll_c=48.0) is None


# --- Sollwert: Schalter + Number ---------------------------------------------


def test_switch_sollwert_ueber_number():
    assert _act(
        domain="switch", current_setpoint=40.0, has_setpoint_entity=True
    ) == WwAction("set_number", 48.0)


def test_switch_sollwert_passt_keine_aktion():
    assert (
        _act(domain="switch", current_setpoint=48.0, has_setpoint_entity=True) is None
    )


def test_switch_ohne_number_nur_schalten():
    # Kein Sollwert-Ziel konfiguriert: eingeschaltet lassen, nichts stellen.
    assert (
        _act(domain="switch", current_setpoint=None, has_setpoint_entity=False) is None
    )


def test_input_boolean_wie_switch():
    assert _act(
        domain="input_boolean", current_setpoint=40.0, has_setpoint_entity=True
    ) == WwAction("set_number", 48.0)


def test_turn_off_hat_keinen_wert():
    assert _act(status="aus", state="on").value is None
