"""Charakterisierung der Saldo-Speicherregelung (`_storage_control`).

Laden verteilt parallel (proportional zur freien Kapazität), Entladen greedy
mit Auswahl-Hysterese (ein Akku zur Zeit, gegen Verschleiß).
"""
from __future__ import annotations

from factories import plan_input, storage, storages, zuteilung
from hems import planner as P

from hems.const import CONTROL_MIN_SETPOINT_W


# --- Laden: parallel auf mehrere Akkus ----------------------------------------
def test_laden_verteilt_parallel_gleichmaessig():
    # -3000 W, Gain 0.5 => Soll ~ -1488 W. Gleiche SoCs => gleichmäßig auf alle
    # drei (statt einen voll, dann den nächsten).
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-3000))
    assert r.regelung.modus == "laden"
    z = zuteilung(r)
    assert z["L1"] == z["L2"] == z["L3"] == 496
    assert sum(z.values()) == 1488


def test_laden_moderat_immer_noch_parallel():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-1000))
    z = zuteilung(r)
    assert z["L1"] == z["L2"] == z["L3"] == 162


def test_laden_proportional_zur_freien_kapazitaet():
    # Leerster Akku bekommt am meisten (SoC-Ausgleich).
    r = P.compute_plan(plan_input(socs=[40, 60, 70], saldo_w=-2000))
    z = zuteilung(r)
    assert z["L1"] > z["L2"] > z["L3"] > 0


def test_laden_zu_klein_faellt_auf_einen_akku_zurueck():
    # Sehr kleiner Überschuss: 3-fach-Split läge unter dem Mindest-Setpoint und
    # würde auf 0 runden (Überschuss liefe ins Netz). Rückfall auf einen Akku.
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-200))
    z = zuteilung(r)
    gestellt = [w for w in z.values() if w > 0]
    assert len(gestellt) == 1
    assert gestellt[0] >= CONTROL_MIN_SETPOINT_W


def test_laden_grosser_ueberschuss_alle_unter_max():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-6000))
    z = zuteilung(r)
    assert all(0 < w <= 1200 for w in z.values())
    assert len({z["L1"], z["L2"], z["L3"]}) == 1  # gleichmäßig


# --- Entladen (greedy + Auswahl-Hysterese, bewusst so) ------------------------
def test_entladen_konzentriert_auf_einen_akku():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=1500))
    assert r.regelung.modus == "entladen"
    z = zuteilung(r)
    assert z["L1"] == 991
    assert z["L2"] == 0
    assert z["L3"] == 0


def test_entladen_arbeitender_akku_behaelt_fuehrung():
    # L1 entlädt bereits (power_w>0) und behält trotz minimal niedrigerem SoC
    # die Führung (Hysterese-Bonus); Überlauf geht auf den nächsten.
    ss = [
        storage("L1", 60, power_w=800.0),
        storage("L2", 61, power_w=0.0),
        storage("L3", 61, power_w=0.0),
    ]
    r = P.compute_plan(plan_input(storage_states=ss, saldo_w=1500))
    z = zuteilung(r)
    assert z["L1"] == 1200
    assert z["L2"] == 591
    assert z["L3"] == 0


# --- Kaltreserve --------------------------------------------------------------
def test_kaltreserve_aktiv_wenn_primaer_leer():
    ss = [
        storage("L1", 30),
        storage("L2", 30),
        storage("R", 90, cold_reserve=True),
    ]
    r = P.compute_plan(plan_input(storage_states=ss, saldo_w=1500))
    assert r.regelung.reserve_aktiv is True
    assert r.regelung.reserve_namen == ["R"]
    # Primärspeicher an der Reserve-Grenze (30 - 10 = 20% verfügbar) tragen
    # wenig bei; die Reserve deckt den Löwenanteil.
    assert zuteilung(r)["R"] > 0


def test_kaltreserve_inaktiv_wenn_primaer_voll():
    ss = [
        storage("L1", 70),
        storage("L2", 70),
        storage("R", 90, cold_reserve=True),
    ]
    r = P.compute_plan(plan_input(storage_states=ss, saldo_w=1500))
    assert r.regelung.reserve_aktiv is False
    assert zuteilung(r)["R"] == 0


# --- Totband ------------------------------------------------------------------
def test_kleiner_saldo_pausiert():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-20))
    assert r.regelung.modus == "pausiert"
    assert sum(zuteilung(r).values()) == 0


# --- Fehlende Eingaben --------------------------------------------------------
def test_ohne_saldo_keine_regelung():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=None))
    assert r.regelung is None
