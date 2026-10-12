"""Betriebsmodus: nur noch „beobachten“ und „auto“ (T-003, Tobias 12.10.2026).

„invers-auto“ und „aus“ sind entfallen. `speicher_modus_option` ist HA-frei
und direkt testbar; die Nähte zu Coordinator und Select importieren Home
Assistant und werden wie in `test_speicher_quittung.py` über den Syntaxbaum
gelesen.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hems import const
from hems.actuation import (
    modus_aktion,
    speicher_modus_option,
    wiederhergestellter_modus,
)
from hems.const import MODE_AUTO, MODE_OBSERVE, MODES_ACTUATING

BASIS = Path(__file__).resolve().parents[1] / "custom_components" / "hems"

EIN = "Eingangsmodus"
AUS = "Ausgangsmodus"


def _funktion(datei: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    baum = ast.parse((BASIS / datei).read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if (
            isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef))
            and knoten.name == name
        ):
            return knoten
    raise AssertionError(f"{name} nicht in {datei} gefunden")


def test_nur_beobachten_und_auto():
    assert MODES_ACTUATING == (MODE_AUTO,)
    assert MODE_OBSERVE not in MODES_ACTUATING
    assert not hasattr(const, "MODE_INVERS_AUTO")
    assert not hasattr(const, "MODE_OFF")


def test_select_bietet_nur_die_beiden_modi():
    quelle = (BASIS / "select.py").read_text(encoding="utf-8")
    assert "[MODE_OBSERVE, MODE_AUTO]" in quelle


def test_alte_modi_fallen_nach_neustart_auf_beobachten():
    optionen = [MODE_OBSERVE, MODE_AUTO]
    for alt in ("invers-auto", "aus", "unavailable", None):
        assert wiederhergestellter_modus(alt, optionen, MODE_OBSERVE) == MODE_OBSERVE
    assert wiederhergestellter_modus("auto", optionen, MODE_OBSERVE) == MODE_AUTO
    assert (
        wiederhergestellter_modus("beobachten", optionen, MODE_OBSERVE) == MODE_OBSERVE
    )


def test_laedt_im_eingangsmodus_und_entlaedt_im_ausgangsmodus():
    assert speicher_modus_option("laden", lade_option=EIN, entlade_option=AUS) == EIN
    assert (
        speicher_modus_option("entladen", lade_option=EIN, entlade_option=AUS) == AUS
    )


def test_pause_stellt_nichts():
    # In der Pause bleibt der zuletzt gesetzte Modus stehen, sonst flippt der
    # Select bei jedem Deadband-Durchgang.
    assert speicher_modus_option("pausiert", lade_option=EIN, entlade_option=AUS) is None


def test_ohne_vollstaendiges_optionspaar_wird_nicht_gestellt():
    assert speicher_modus_option("laden", lade_option=EIN, entlade_option=None) is None
    assert (
        speicher_modus_option("entladen", lade_option=None, entlade_option=AUS) is None
    )


def test_actuator_kennt_keinen_invers_modus_mehr():
    for datei, name in (
        ("actuator.py", "apply"),
        ("actuator.py", "_apply_battery"),
        ("actuation.py", "speicher_modus_option"),
    ):
        fn = _funktion(datei, name)
        alle = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        assert not [a.arg for a in alle if "invers" in a.arg], (datei, name)


def test_leistungs_setpoints_haengen_nur_am_regelmodus():
    """Die Sollwert-Zweige in `_apply_battery` hängen nur an `ctrl.modus`.

    Übernommen aus dem früheren Invers-Test: Der Regler schließt über die
    gemessene Speicherleistung; die Sollwerte dürfen von nichts anderem als
    dem Regelmodus abhängen.
    """
    fn = _funktion("actuator.py", "_apply_battery")
    zuweisungen = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Assign)
        and any(
            isinstance(z, ast.Tuple)
            and {getattr(e, "id", None) for e in z.elts} == {"charge_w", "discharge_w"}
            for z in k.targets
        )
    ]
    assert zuweisungen, "die Sollwert-Zweige wurden umbenannt — Test nachziehen"
    for zuw in zuweisungen:
        namen = {k.id for k in ast.walk(zuw) if isinstance(k, ast.Name)}
        assert namen <= {"charge_w", "discharge_w", "watt"}, namen


def test_modus_aktion_je_wechsel():
    assert modus_aktion(MODE_OBSERVE, MODE_AUTO, MODES_ACTUATING) == "stellen"
    assert modus_aktion(MODE_AUTO, MODE_AUTO, MODES_ACTUATING) == "stellen"
    # Verlassen von auto: genau einmal freigeben …
    assert modus_aktion(MODE_AUTO, MODE_OBSERVE, MODES_ACTUATING) == "freigeben"
    # … und danach nichts mehr schreiben.
    assert modus_aktion(MODE_OBSERVE, MODE_OBSERVE, MODES_ACTUATING) is None


def test_coordinator_liest_den_modus_einmal_und_nutzt_modus_aktion():
    # Ein Moduswechsel während der Aktuierung (await) darf `_prev_mode` nicht
    # auf den neuen Wert setzen, ohne dass der Wechsel behandelt wurde.
    fn = _funktion("coordinator.py", "_async_update_data")
    quelle = ast.unparse(fn)
    assert "modus_aktion(self._prev_mode, modus, MODES_ACTUATING)" in quelle
    assert "self._prev_mode = modus" in quelle


def test_nachtritt_mit_backoff():
    from datetime import datetime, timedelta

    from hems.actuation import nachtritt_faellig

    t = datetime(2026, 10, 12, 22, 0)
    assert nachtritt_faellig(timedelta(seconds=60), t, None) is False
    assert nachtritt_faellig(timedelta(seconds=90), t, None) is True
    # Danach nicht im Minutentakt …
    assert nachtritt_faellig(timedelta(minutes=3), t, t - timedelta(minutes=1)) is False
    # … sondern erst nach fünf Minuten wieder.
    assert nachtritt_faellig(timedelta(minutes=7), t, t - timedelta(minutes=5)) is True


def test_select_nur_fuer_speicher_mit_leistung():
    from hems.actuation import richtungs_option

    kw = {"lade_option": EIN, "entlade_option": AUS}
    assert richtungs_option("laden", 500, **kw) == EIN
    assert richtungs_option("entladen", 500, **kw) == AUS
    # 0 W: Select bleibt stehen, auch wenn die Anlage die Richtung wechselt.
    assert richtungs_option("laden", 0, **kw) is None
    assert richtungs_option("entladen", 0.0, **kw) is None
    assert richtungs_option("pausiert", 500, **kw) is None


def test_kaltreserve_bekommt_eigenen_ziel_soc():
    from hems.actuation import geraete_ziel_soc_grundlage

    assert (
        geraete_ziel_soc_grundlage(kaltreserve=True, reserve_ziel_soc=100.0, deckel_soc=60.0)
        == 100.0
    )
    assert (
        geraete_ziel_soc_grundlage(kaltreserve=True, reserve_ziel_soc=90.0, deckel_soc=None)
        == 90.0
    )
    assert (
        geraete_ziel_soc_grundlage(kaltreserve=False, reserve_ziel_soc=100.0, deckel_soc=60.0)
        == 60.0
    )
    assert (
        geraete_ziel_soc_grundlage(kaltreserve=False, reserve_ziel_soc=100.0, deckel_soc=None)
        is None
    )
