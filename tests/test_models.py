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
