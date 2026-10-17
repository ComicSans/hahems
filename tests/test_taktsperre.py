"""Taktsperre der Heizung (strategies/heating.py, T-017).

Mindestabstand zwischen zwei Starts: Schaltet der Überschuss die Heizung
innerhalb des Intervalls nach ihrem Start ab, bleibt sie bis zum Intervallende
aus und wird dann zwangsweise wieder eingeschaltet. Der Frostschutz steht
darüber.
"""
from __future__ import annotations

import ast
from pathlib import Path

from factories import heating, plan_input, switchable
from hems import planner as P

MIN = 60


def _plan(heiz, *, sw=None, saldo_w=-5000.0):
    """Eine Heizung (Taktsperre 60 min) mit passender Schaltlast-Sicht."""
    if sw is None:
        sw = switchable(
            "Wärmepumpe",
            erwartet_w=1500,
            ist_an=heiz.ist_an,
            aus_seit_s=heiz.aus_seit_s,
            min_off_min=heiz.min_off_min,
            max_block_min=0,
        )
    return P.compute_plan(
        plan_input(switchables=[sw], heatings=[heiz], saldo_w=saldo_w)
    )


def _anlage(res):
    return res.heizung.anlagen[0]


def _an(res):
    return res.schaltbare.lasten[0].an


def _aus_nach_kurzem_lauf(**kw):
    """Vor 20 min gestartet, vor 10 min wieder aus: Laufzeit 10 min < 60."""
    werte = dict(
        takt_sperre_min=60, start_seit_s=20 * MIN, aus_seit_s=10 * MIN
    )
    werte.update(kw)
    return heating(**werte)


def test_sperre_haelt_trotz_ueberschuss_aus():
    r = _plan(_aus_nach_kurzem_lauf())
    a = _anlage(r)
    assert a.status == "taktsperre"
    assert a.sperre is True and a.zwang_an is False
    assert "noch 40 min" in a.grund
    assert _an(r) is False


def test_wiederanlauf_am_intervallende_ohne_ueberschuss():
    heiz = _aus_nach_kurzem_lauf(start_seit_s=61 * MIN, aus_seit_s=51 * MIN)
    r = _plan(heiz, saldo_w=3000.0)  # Netzbezug, kein Überschuss
    a = _anlage(r)
    assert a.zwang_an is True and a.sperre is False
    assert a.status == "heizen"
    assert "Wiederanlauf" in a.grund
    assert _an(r) is True


def test_wiederanlauf_auch_ohne_netzsaldo():
    """Der Zwang hängt an `plan.heizung`, nicht an der Überschussregelung."""
    heiz = _aus_nach_kurzem_lauf(start_seit_s=61 * MIN, aus_seit_s=51 * MIN)
    r = P.compute_plan(
        plan_input(
            switchables=[switchable("Wärmepumpe", ist_an=False)],
            heatings=[heiz],
            saldo_w=None,
        )
    )
    assert r.schaltbare is None
    assert _anlage(r).zwang_an is True


def test_wiederanlauf_wartet_auf_die_mindestpause():
    """Start vor 61 min, Aus erst vor 5 min: min_off (10) läuft noch."""
    heiz = _aus_nach_kurzem_lauf(
        start_seit_s=61 * MIN, aus_seit_s=5 * MIN + 1, min_off_min=10
    )
    # Laufzeit 56 min < 60: Sperre greift, Rest = Mindestpause.
    a = _anlage(_plan(heiz))
    assert a.status == "taktsperre" and a.zwang_an is False
    assert "noch 5 min" in a.grund


def test_frost_uebersteuert_die_sperre():
    r = _plan(_aus_nach_kurzem_lauf(outdoor_temp_c=1.0), saldo_w=3000.0)
    a = _anlage(r)
    assert a.status == "frostschutz"
    assert a.zwang_an is True and a.sperre is False
    assert _an(r) is True


def test_heizgrenze_verhindert_den_wiederanlauf():
    heiz = _aus_nach_kurzem_lauf(
        outdoor_temp_c=20.0, start_seit_s=61 * MIN, aus_seit_s=51 * MIN
    )
    a = _anlage(_plan(heiz))
    assert a.status == "heizgrenze"
    assert a.zwang_an is False


def test_sommersperre_verhindert_den_wiederanlauf():
    heiz = _aus_nach_kurzem_lauf(
        month=7, start_seit_s=61 * MIN, aus_seit_s=51 * MIN
    )
    a = _anlage(_plan(heiz))
    assert a.status == "sommersperre"
    assert a.zwang_an is False


def test_laufende_anlage_bleibt_unberuehrt():
    heiz = heating(takt_sperre_min=60, ist_an=True, start_seit_s=5 * MIN)
    a = _anlage(_plan(heiz))
    assert a.status == "heizen"
    assert a.sperre is False and a.zwang_an is False


def test_nach_langem_lauf_entscheidet_der_ueberschuss():
    """Lief 2 h am Stück: kein Zwang, sonst ginge sie nie mehr aus."""
    heiz = heating(takt_sperre_min=60, start_seit_s=125 * MIN, aus_seit_s=5 * MIN)
    r = _plan(heiz, saldo_w=3000.0)
    a = _anlage(r)
    assert a.status == "heizen"
    assert a.sperre is False and a.zwang_an is False
    assert _an(r) is False


def test_start_unbekannt_weder_sperre_noch_zwang():
    """Nach einem Neustart ist der letzte Start unbekannt."""
    heiz = heating(takt_sperre_min=60, start_seit_s=None, aus_seit_s=5 * MIN)
    a = _anlage(_plan(heiz))
    assert a.status == "heizen"
    assert a.sperre is False and a.zwang_an is False


def test_null_heisst_aus():
    heiz = _aus_nach_kurzem_lauf(takt_sperre_min=0)
    r = _plan(heiz)
    a = _anlage(r)
    assert a.status == "heizen" and a.sperre is False and a.zwang_an is False
    assert _an(r) is True  # Überschuss deckt die Last, wie bisher


def test_kuehlbetrieb_kennt_keine_taktsperre():
    heiz = _aus_nach_kurzem_lauf(betriebsart="kuehlen")
    a = _anlage(_plan(heiz))
    assert a.status == "kuehlen"
    assert a.sperre is False and a.zwang_an is False


# --- Verdrahtung (Actuator und Coordinator importieren HA) ---------------------
BASIS = Path(__file__).resolve().parents[1] / "custom_components" / "hems"


def _funktion(datei: str, name: str):
    baum = ast.parse((BASIS / datei).read_text(encoding="utf-8"))
    return next(
        k
        for k in ast.walk(baum)
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)) and k.name == name
    )


def test_actuator_wiederholt_nur_den_frostschutz():
    """Der Wiederanlauf läuft über `zwang_an`, darf aber nicht wie der
    Frostschutz nachgeschrieben werden."""
    fn = _funktion("actuator.py", "_apply_heating")
    werte = [
        kw.value
        for k in ast.walk(fn)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "_turn_heizung"
        for kw in k.keywords
        if kw.arg == "frostschutz"
    ]
    assert werte, "frostschutz wird nicht mehr übergeben"
    for wert in werte:
        assert not isinstance(wert, ast.Constant), "frostschutz pauschal gesetzt"
        assert "frostschutz" in ast.unparse(wert)


def test_coordinator_merkt_den_start_und_reicht_die_lage_durch():
    lage = ast.unparse(_funktion("coordinator.py", "_takt_lage"))
    assert "self._letzter_start[h.id] = st.last_changed" in lage
    states = ast.unparse(_funktion("coordinator.py", "_heating_states"))
    assert "takt_sperre_min=h.takt_sperre_min" in states
    assert "min_off_min=h.min_off_min" in states
    assert "self._takt_lage(h, now)" in states
