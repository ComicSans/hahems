"""Rollenmodell (models.py): Options-Liste → DeviceRegistry.

Deckt die Rollentrennung ab: Ein Wärmeerzeuger ist eine eigene Rolle (Heizung)
und keine Schaltlast mit Flag mehr. Beide werden nach Nutzer-Priorität
sortiert, damit Konsumenten sie der Reihe nach abarbeiten können.
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


def _heating(**kw) -> dict:
    return {
        "id": kw.pop("id", "h"),
        "role": "heating",
        "name": kw.pop("name", "Wärmepumpe"),
        "switch_entity": kw.pop("switch_entity", "climate.wp"),
        **kw,
    }


def test_heizung_ist_eine_eigene_rolle():
    reg = parse_devices([_switchable(), _heating()])
    assert [s.name for s in reg.switchables] == ["Last"]
    assert [h.name for h in reg.heatings] == ["Wärmepumpe"]


def test_heizung_erbt_die_schaltlast_defaults():
    """Anti-Takt und Priorität gelten für die Heizung wie für jede Schaltlast."""
    h = parse_devices([_heating()]).heatings[0]
    assert (h.min_on_min, h.min_off_min, h.max_block_min, h.priority) == (
        20,
        10,
        120,
        1,
    )


def test_frostschutz_hat_hysterese_ab_werk():
    """Ohne Hysterese taktet die Anlage an der Frostschwelle."""
    h = parse_devices([_heating()]).heatings[0]
    assert h.frost_off_c > h.frost_on_c


def test_mehrere_schaltlasten_bleiben_erhalten_und_sortiert():
    reg = parse_devices(
        [
            _switchable(id="b", name="Entfeuchter", priority=3),
            _switchable(id="a", name="Pool", priority=1),
        ]
    )
    assert [s.name for s in reg.switchables] == ["Pool", "Entfeuchter"]


def test_heizungen_werden_ebenfalls_nach_prioritaet_sortiert():
    reg = parse_devices(
        [
            _heating(id="b", name="Heizstab", priority=3),
            _heating(id="a", name="Wärmepumpe", priority=1),
        ]
    )
    assert [h.name for h in reg.heatings] == ["Wärmepumpe", "Heizstab"]


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


