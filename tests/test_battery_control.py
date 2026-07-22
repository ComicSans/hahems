"""Charakterisierung der Saldo-Speicherregelung (`_storage_control`).

Nagelt das heutige Verhalten fest, BEVOR paralleles Laden (Schritt 2) und der
Domänen-Refactor (Schritt 3) kommen. Die Ladeverteilung ist heute greedy
(ein Akku voll, dann der nächste) — die betreffenden Erwartungen werden in
Schritt 2 bewusst auf paralleles Laden umgestellt; alles andere bleibt gleich.
"""
from __future__ import annotations

from factories import plan_input, storage, storages, zuteilung
from hems import planner as P


# --- Laden (heutiges greedy-Verhalten) ----------------------------------------
def test_laden_greedy_fuellt_einen_akku_zuerst():
    # -3000 W Überschuss, Gain 0.5 => Soll ~ -1487 W. Greedy: L1 an sein Max
    # (1200), Rest (~288) auf L2, L3 leer.
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-3000))
    assert r.regelung.modus == "laden"
    z = zuteilung(r)
    assert z["L1"] == 1200
    assert z["L2"] == 288
    assert z["L3"] == 0


def test_laden_moderat_nur_ein_akku():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-1000))
    z = zuteilung(r)
    assert z["L1"] == 488
    assert z["L2"] == 0
    assert z["L3"] == 0


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
