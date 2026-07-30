"""Rollenmodell (models.py): Options-Liste → DeviceRegistry.

Deckt vor allem `heat_coupled` ab: nur damit markierte Schaltlasten dürfen ins
Wärmepumpen-Verbrauchsmodell einfließen. Der Default muss False sein, damit
eine neu angelegte überschussgesteuerte Last (Pool, Luftentfeuchter) die
Heizgradstunden-Regression nicht verzerrt; bestehende Einträge hebt die
Options-Migration (async_migrate_entry) einmalig auf True.
"""
from __future__ import annotations

from hems.models import parse_devices


def _switchable(**kw) -> dict:
    return {
        "id": kw.pop("id", "a"),
        "role": "switchable_load",
        "name": kw.pop("name", "Last"),
        "switch_entity": "switch.last",
        **kw,
    }


def test_heat_coupled_default_aus():
    reg = parse_devices([_switchable()])
    assert reg.switchables[0].heat_coupled is False


def test_heat_coupled_wird_uebernommen():
    reg = parse_devices([_switchable(heat_coupled=True)])
    assert reg.switchables[0].heat_coupled is True


def test_mehrere_schaltlasten_bleiben_erhalten_und_sortiert():
    reg = parse_devices(
        [
            _switchable(id="b", name="Entfeuchter", priority=3),
            _switchable(id="a", name="Wärmepumpe", priority=1, heat_coupled=True),
        ]
    )
    assert [s.name for s in reg.switchables] == ["Wärmepumpe", "Entfeuchter"]
    assert [s.heat_coupled for s in reg.switchables] == [True, False]


def test_unbekannte_felder_werden_ignoriert():
    """Options aus einer älteren/neueren Version dürfen nicht crashen."""
    reg = parse_devices([_switchable(irgendwas="x")])
    assert len(reg.switchables) == 1


def _thermal(**kw) -> dict:
    return {
        "id": kw.pop("id", "t"),
        "role": "thermal",
        "name": kw.pop("name", "WW"),
        **kw,
    }


def test_thermal_setpoint_entity_wird_uebernommen():
    """Schalter-Variante: Freigabe-Schalter + separate Sollwert-Number."""
    reg = parse_devices(
        [
            _thermal(
                control_entity="switch.ww_freigabe",
                setpoint_entity="number.ww_soll",
            )
        ]
    )
    t = reg.thermals[0]
    assert t.control_entity == "switch.ww_freigabe"
    assert t.setpoint_entity == "number.ww_soll"


def test_thermal_setpoint_entity_default_none():
    """water_heater trägt den Sollwert selbst — kein setpoint_entity nötig."""
    reg = parse_devices([_thermal(control_entity="water_heater.ww")])
    assert reg.thermals[0].setpoint_entity is None


def _heating(**kw) -> dict:
    return {
        "id": kw.pop("id", "h"),
        "role": "heating_circuit",
        "name": kw.pop("name", "WP"),
        "outdoor_temp_entity": "sensor.aussen",
        **kw,
    }


def test_heating_select_variante_wird_uebernommen():
    """Modus-Select + Vorlauf-Number + Options statt climate."""
    reg = parse_devices(
        [
            _heating(
                control_entity="select.wp_modus",
                setpoint_entity="number.wp_vorlauf_soll",
                mode_heat_option="Heizen",
                mode_cool_option="Kühlen",
                mode_off_option="Aus/nur Warmwasser",
            )
        ]
    )
    h = reg.heatings[0]
    assert h.control_entity == "select.wp_modus"
    assert h.setpoint_entity == "number.wp_vorlauf_soll"
    assert (h.mode_heat_option, h.mode_cool_option, h.mode_off_option) == (
        "Heizen",
        "Kühlen",
        "Aus/nur Warmwasser",
    )


def test_heating_climate_variante_options_default_none():
    reg = parse_devices([_heating(control_entity="climate.wp")])
    h = reg.heatings[0]
    assert h.setpoint_entity is None
    assert h.mode_heat_option is None
    assert h.mode_off_option is None
