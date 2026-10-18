"""Wiederanlauf über den schreibenden Select statt einer Climate-Anzeige."""
from __future__ import annotations

import __future__
import ast
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from hems import entity_domain as E
from hems.models import DeviceRegistry, HeatingSystem
from hems.strategies.types import PlanResult, SwitchableState

BASIS = Path(__file__).resolve().parents[1] / "custom_components" / "hems"
ENT = "select.heat_pump"
OFF = "Off / Hot Water Only"
NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _laden(datei, namen, **namespace):
    """Produktive Methoden HA-frei mit minimalen Schnittstellen ausführen."""
    baum = ast.parse((BASIS / datei).read_text())
    knoten = [k for k in ast.walk(baum) if isinstance(
        k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ) and k.name in namen]
    code = ast.Module(body=knoten, type_ignores=[])
    exec(compile(code, str(BASIS / datei), "exec",
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace


def _heizung(**kw):
    werte = dict(id="wp", name="WP", switch_entity=ENT,
                 mode_heat_option="Auto", mode_cool_option="Cool",
                 mode_off_option=OFF, outdoor_temp_entity="sensor.outdoor")
    return HeatingSystem(**(werte | kw))


@pytest.mark.parametrize("state,on,art", [
    (OFF, False, E.BETRIEBSART_HEIZEN),
    ("Auto", True, E.BETRIEBSART_HEIZEN),
    ("Cool", True, E.BETRIEBSART_KUEHLEN),
    ("Vacation", True, E.BETRIEBSART_FREMD),
    ("unavailable", False, E.BETRIEBSART_HEIZEN),
    ("unknown", False, E.BETRIEBSART_HEIZEN),
    (None, False, E.BETRIEBSART_HEIZEN),
])
def test_select_lage_und_betriebsart(state, on, art):
    assert E.ist_an(ENT, state, OFF) is on
    assert E.betriebsart(ENT, state, "Auto", "Cool", OFF) == art


@pytest.mark.parametrize("on,art,option", [
    (True, E.BETRIEBSART_HEIZEN, "Auto"),
    (True, E.BETRIEBSART_KUEHLEN, "Cool"),
    (False, E.BETRIEBSART_HEIZEN, OFF),
    (False, E.BETRIEBSART_KUEHLEN, OFF),
])
def test_schreiben_nutzt_exakte_select_option(on, art, option):
    assert E.schalt_service(ENT, on, "Auto", "Cool", art, OFF) == (
        "select", "select_option", {"option": option}
    )


@pytest.mark.parametrize("heat,off", [(None, OFF), ("Auto", None),
                                          ("Auto", "Auto"), ("Auto", "Cool")])
def test_unvollstaendiger_select_schreibt_nichts(heat, off):
    with pytest.raises(ValueError):
        E.schalt_service(ENT, True, heat, "Cool", off_mode=off)


def _actuator(state, *, uebernehmen=True):
    ns = _laden("actuator.py", {"_turn_heizung"}, entity_domain=E,
                dt_util=SimpleNamespace(utcnow=lambda: NOW),
                HEIZUNG_QUITTUNG_FRIST=timedelta(minutes=2),
                HEIZUNG_ZWANG_WIEDERHOLUNG=timedelta(minutes=15),
                _LOGGER=logging.getLogger(__name__))
    calls = []
    obj = SimpleNamespace(_last_heizung={}, _heizung_gemeldet=set(), state=state)
    obj._state = lambda ent: obj.state

    async def call(domain, service, ent, **data):
        calls.append((domain, service, ent, data))
        if uebernehmen:
            obj.state = data["option"]
        return True

    obj._call = call
    obj.turn = lambda on, art, plan: ns["_turn_heizung"](
        obj, _heizung(), on, art, plan
    )
    return obj, calls


def test_actuator_aus_und_wiederanlauf_schreiben_raw_option_einmal():
    obj, calls = _actuator("Auto")
    plan = PlanResult()
    asyncio.run(obj.turn(False, E.BETRIEBSART_HEIZEN, plan))
    asyncio.run(obj.turn(False, E.BETRIEBSART_HEIZEN, plan))
    asyncio.run(obj.turn(True, E.BETRIEBSART_HEIZEN, plan))
    asyncio.run(obj.turn(True, E.BETRIEBSART_HEIZEN, plan))
    assert calls == [
        ("select", "select_option", ENT, {"option": OFF}),
        ("select", "select_option", ENT, {"option": "Auto"}),
    ]
    assert not obj._last_heizung
    assert not plan.heizung_nicht_uebernommen


def test_select_ignorierter_wiederanlauf_wird_gemeldet_ohne_bus_spam():
    obj, calls = _actuator(OFF, uebernehmen=False)
    plan = PlanResult()
    asyncio.run(obj.turn(True, E.BETRIEBSART_HEIZEN, plan))
    obj._last_heizung[ENT] = (True, NOW - timedelta(minutes=3))
    asyncio.run(obj.turn(True, E.BETRIEBSART_HEIZEN, plan))
    assert len(calls) == 1
    assert plan.heizung_nicht_uebernommen == ["WP"]


def test_select_fremden_modus_nicht_abschalten():
    obj, calls = _actuator("Vacation")
    asyncio.run(obj.turn(False, E.BETRIEBSART_HEIZEN, PlanResult()))
    assert calls == []


def test_coordinator_behaelt_kuehlbetrieb_und_taktzeiten_ueber_aus():
    ns = _laden("coordinator.py", {"_betriebsart", "_takt_lage"}, entity_domain=E)
    st = SimpleNamespace(state="Cool", last_changed=NOW - timedelta(minutes=5))
    obj = SimpleNamespace(hass=SimpleNamespace(states=SimpleNamespace(get=lambda _: st)),
                          _letzte_betriebsart={}, _letzter_start={})
    h = _heizung()
    assert ns["_betriebsart"](obj, h) == E.BETRIEBSART_KUEHLEN
    assert ns["_takt_lage"](obj, h, NOW)["ist_an"] is True
    st.state, st.last_changed = OFF, NOW
    assert ns["_betriebsart"](obj, h) == E.BETRIEBSART_KUEHLEN
    assert ns["_takt_lage"](obj, h, NOW + timedelta(minutes=10)) == {
        "ist_an": False, "start_seit_s": 15 * 60, "aus_seit_s": 10 * 60,
    }


@pytest.mark.parametrize("state,on", [(OFF, False), ("Auto", True), ("Cool", True)])
def test_ueberschussregelung_erkennt_die_select_schaltlage(state, on):
    ns = _laden("coordinator.py", {"_switchable_states"}, entity_domain=E,
                HeatingSystem=HeatingSystem, SwitchableState=SwitchableState)
    st = SimpleNamespace(state=state, last_changed=NOW - timedelta(minutes=10))
    obj = SimpleNamespace(hass=SimpleNamespace(states=SimpleNamespace(get=lambda _: st)),
                          power_memory=None, _power_w=lambda _: None)
    result = ns["_switchable_states"](obj, DeviceRegistry(heatings=[_heizung()]), NOW)
    assert result[0].ist_an is on
    assert result[0].an_seit_s == (600 if on else None)
    assert result[0].aus_seit_s == (None if on else 600)


@pytest.mark.parametrize("kw,fehler", [
    ({}, False), ({"mode_off_option": None}, True),
    ({"mode_heat_option": "auto"}, True),
    ({"mode_off_option": "Auto"}, True),
    ({"mode_cool_option": "Auto"}, True),
])
def test_config_prueft_select_optionen(kw, fehler):
    ns = _laden("config_check.py", {"ConfigCheck", "check_config", "_domain", "_exists"},
                dataclass=dataclass, field=field, pruefung_moeglich=lambda _: True,
                DEFAULT_SWITCHABLE_EXPECTED_W=1500,
                _scan_overlaps=lambda *args: None, _scan_fremdregler=lambda *args: None)
    st = SimpleNamespace(attributes={"options": [OFF, "Auto", "Heat", "Cool"]})
    hass = SimpleNamespace(state="running", states=SimpleNamespace(get=lambda _: st))
    reg = DeviceRegistry(heatings=[_heizung(**kw)])
    check = ns["check_config"](hass, reg)
    assert bool(check.errors) is fehler
