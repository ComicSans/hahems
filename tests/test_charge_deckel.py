"""Ladestrategie über den Tag: zwei Ladefenster, Mittagspause, voll zur Nacht.

Charakterisiert `_lade_deckel_soc` / `_ist_ladepause` (custom_components/hems/
strategies/battery.py), deren Ergebnis in `compute_plan.lade_deckel_soc` /
`.lade_pause` landet, und ihre Wirkung auf Ladezuteilung und Vorrang.

Alle Zeiten im Test sind UTC; `lokal()` rechnet die gemeinte Uhrzeit um.
"""
from __future__ import annotations

from datetime import timedelta

from factories import NOON, SUNSET, load, lokal, plan_input, storages, zuteilung
from hems import planner as P
from hems.const import (
    GOAL_FULL_CHARGE,
    PRIORITY_BATTERY_FIRST,
    STORAGE_DAY_TARGET_SOC,
)
from hems.strategies import coordination


def _deckel(**kw) -> float:
    return P.compute_plan(plan_input(**kw)).lade_deckel_soc


# --- Deckelkurve über den Tag ---------------------------------------------


def test_vormittags_deckel_ist_tagesziel():
    assert _deckel(now=lokal(9), socs=[60, 60, 60], saldo_w=-1500) == (
        STORAGE_DAY_TARGET_SOC
    )


def test_mittags_deckel_ist_tagesziel():
    assert _deckel(socs=[60, 60, 60], saldo_w=-1500) == STORAGE_DAY_TARGET_SOC


def test_nachmittags_rampe_auf_voll():
    # 15:00 lokal liegt mittig in der Rampe 14:00 → 16:00.
    deckel = _deckel(now=lokal(15), socs=[80, 82, 85], saldo_w=-1500)
    erwartet = STORAGE_DAY_TARGET_SOC + 0.5 * (100 - STORAGE_DAY_TARGET_SOC)
    assert abs(deckel - erwartet) < 0.05


def test_ab_sechzehn_uhr_voll_fuer_die_nacht():
    assert _deckel(now=lokal(16), socs=[80, 82, 85], saldo_w=-1500) == 100.0
    assert _deckel(now=lokal(18), socs=[80, 82, 85], saldo_w=-1500) == 100.0


def test_unter_tagesziel_laedt():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-1500))
    assert r.regelung.modus == "laden"
    assert sum(zuteilung(r).values()) > 0


def test_ueber_tagesziel_ohne_andere_abnehmer_laedt_trotzdem():
    # "Bevor eingespeist wird, immer Akkus laden": Alle Speicher stehen über
    # dem Tagesziel, es gibt aber keine Last, die den Überschuss nimmt — dann
    # geht er in den Akku statt ins Netz, und der wirksame Deckel zieht mit.
    r = P.compute_plan(plan_input(socs=[96, 96, 96], saldo_w=-1500))
    assert r.regelung.modus == "laden"
    assert r.regelung.laden_statt_einspeisen
    assert sum(zuteilung(r).values()) > 0
    assert r.lade_deckel_soc == 100.0


def test_gemischte_staende_nutzen_auch_den_gedeckelten_speicher():
    # L1 steht über dem Tagesziel, L2 darunter — aber L2 allein kann den
    # Überschuss nicht aufnehmen (Ladegrenze 1200 W je Speicher). Der Rest
    # ginge ins Netz, also lädt auch L1 mit.
    r = P.compute_plan(
        plan_input(storage_states=storages([96, 50]), saldo_w=-6000)
    )
    z = zuteilung(r)
    assert r.regelung.laden_statt_einspeisen
    assert z["L1"] > 0 and z["L2"] > 0


def test_volle_speicher_laden_nicht_weiter():
    r = P.compute_plan(plan_input(socs=[100, 100, 100], saldo_w=-1500))
    assert not r.regelung.laden_statt_einspeisen
    assert sum(zuteilung(r).values()) == 0


def test_ziel_vollladen_hebt_deckel_auf():
    assert _deckel(socs=[80, 82, 85], saldo_w=-1500, goal=GOAL_FULL_CHARGE) == 100.0


def test_heute_knapp_hebt_deckel_auf():
    # Wenig PV-Rest, hohe Grundlast => Restertrag reicht nicht zum Nachladen.
    deckel = _deckel(
        socs=[60, 60, 60], saldo_w=-1500, pv_remaining_kwh=1.0, baseline_load_w=1500.0
    )
    assert deckel == 100.0


def test_heute_knapp_bemisst_sich_am_tatsaechlichen_stand():
    # SoC ungewöhnlich tief unter dem Tagesziel: der reale Nachlade-Bedarf bis
    # 100 % (4,8 kWh) ist größer als eine feste Prozent-Annahme unterstellt
    # hätte. Der verfügbare Resttag-Überschuss (3,0 kWh) reicht dafür nicht —
    # der Deckel muss sofort aufheben, sonst droht die Nacht ungedeckt.
    deckel = _deckel(
        socs=[20, 20, 20], saldo_w=-1500, pv_remaining_kwh=6.2, baseline_load_w=400.0
    )
    assert deckel == 100.0


def test_nacht_deckel_ist_100():
    day = NOON.replace(hour=1)  # 01:00, vor Sonnenaufgang
    inp = plan_input(
        now=day,
        socs=[60, 60, 60],
        saldo_w=-1500,
        next_sunrise=day.replace(hour=5),
        sunset=day.replace(hour=19),
    )
    assert P.compute_plan(inp).lade_deckel_soc == 100.0


def test_prognose_tagsueber_unter_tagesziel():
    r = P.compute_plan(plan_input(now=lokal(9), socs=[30, 30, 30], saldo_w=-3000))
    fenster = [
        pt.soc
        for pt in r.soc_prognose
        if lokal(9) <= pt.zeit <= lokal(9) + timedelta(hours=2)
    ]
    assert fenster and all(s <= STORAGE_DAY_TARGET_SOC + 0.6 for s in fenster)


# --- Mittags-Ladepause ------------------------------------------------------


def test_ladepause_nur_zwischen_elf_und_vierzehn():
    def pause(stunde: int) -> bool:
        return P.compute_plan(
            plan_input(now=lokal(stunde), socs=[60, 60, 60], saldo_w=-1500)
        ).lade_pause

    assert not pause(10)
    assert pause(11)
    assert pause(13)
    assert not pause(14)
    assert not pause(15)


def test_ladepause_reserviert_keinen_ueberschuss_fuer_den_akku():
    inp = plan_input(
        socs=[60, 60, 60],
        saldo_w=-1500,
        modulateds=[load()],
        priority_mode=PRIORITY_BATTERY_FIRST,
    )
    res = P.compute_plan(inp)
    assert res.lade_pause
    assert coordination.akku_ladereservierung(inp, res) == 0.0


def test_ausserhalb_der_pause_reserviert_der_akku_wieder():
    inp = plan_input(
        now=lokal(15),
        socs=[60, 60, 60],
        saldo_w=-1500,
        modulateds=[load()],
        priority_mode=PRIORITY_BATTERY_FIRST,
    )
    res = P.compute_plan(inp)
    assert not res.lade_pause
    assert coordination.akku_ladereservierung(inp, res) > 0


def test_ladepause_faellt_weg_wenn_voll_geladen_werden_muss():
    res = P.compute_plan(
        plan_input(socs=[60, 60, 60], saldo_w=-1500, goal=GOAL_FULL_CHARGE)
    )
    assert not res.lade_pause


def test_ladepause_laedt_weiter_wenn_sonst_eingespeist_wuerde():
    # In der Pause tritt der Akku nur im VORRANG zurück. Bleibt Überschuss
    # übrig, lädt er ihn trotzdem — die Zuteilung steht.
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-1500))
    assert r.lade_pause
    assert r.regelung.modus == "laden"
    assert sum(zuteilung(r).values()) > 0


def test_sonnenuntergang_verschiebt_den_deckel_nicht():
    # Früherer Sonnenuntergang (Winter) ändert an der Uhrzeit-Regel nichts,
    # solange die Nachtdeckung nicht knapp wird.
    frueh = SUNSET - timedelta(hours=3)
    assert _deckel(now=lokal(12), socs=[60, 60, 60], saldo_w=-1500, sunset=frueh) == (
        STORAGE_DAY_TARGET_SOC
    )
