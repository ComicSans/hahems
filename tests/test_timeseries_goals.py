"""Zeitverlauf-Tests: Optimierungsziele über einen realen (anonymisierten)
Tagesverlauf auf korrekte Erfüllung prüfen.

Datenbasis: tests/data/day_profile.json — ein Sonnentag aus einer echten
HA-Instanz, anonymisiert (Namen entfernt, Datum → relative Tageszeit, Werte
skaliert+gerundet). Der Simulator (tests/simulate.py) treibt compute_plan im
15-Min-Takt durch den Tag, integriert den SoC und leitet den Netzsaldo aus PV,
Hauslast und Speicherleistung ab.

Erwartete Semantik der Ziele:
- eigenverbrauch: Der Tagesdeckel (~95 %) ordnet den Vorrang, verschenkt aber
  nichts: Läuft — wie in diesem Profil — kein anderer Abnehmer mit, lädt der
  Akku auch über den Deckel hinaus, statt einzuspeisen. Der Deckel wird erst
  dort sichtbar, wo eine Last um denselben Überschuss konkurriert
  (tests/test_coordination.py, tests/test_charge_deckel.py).
- nulleinspeisung: der Akku saugt den Überschuss auf, solange er Platz hat;
  Einspeisung nur bei vollem Akku (Deckel aufgehoben).
- vollladen: Akku wird voll geladen (Deckel aufgehoben), sonst wie
  eigenverbrauch.
"""
from __future__ import annotations

import pytest

from simulate import (
    RESERVE_SOC,
    export_kwh,
    export_kwh_below,
    import_kwh,
    load_profile,
    simulate,
)

NOON = 48   # index 12:00 bei 15-Min-Raster
ABEND = 84  # index 21:00

BASELINE_IMPORT_KWH = 3.45  # ohne Akku (Summe der Defizite)
BASELINE_EXPORT_KWH = 17.85  # ohne Akku (Summe der Überschüsse)


@pytest.fixture(scope="module")
def sims():
    return {g: simulate(g) for g in ("eigenverbrauch", "nulleinspeisung", "vollladen")}


# --- Invarianten über alle Ziele ---------------------------------------------
@pytest.mark.parametrize("goal", ["eigenverbrauch", "nulleinspeisung", "vollladen"])
def test_soc_bleibt_in_grenzen(sims, goal):
    st = sims[goal]
    assert all(RESERVE_SOC - 0.5 <= s.soc <= 100.5 for s in st), (
        f"{goal}: SoC verlässt [Reserve..100]"
    )


@pytest.mark.parametrize("goal", ["eigenverbrauch", "nulleinspeisung", "vollladen"])
def test_akku_senkt_netzabhaengigkeit(sims, goal):
    # Der Akku deckt Defizite (weniger Bezug) und puffert Überschuss (weniger
    # Einspeisung) gegenüber dem batterielosen Referenztag.
    st = sims[goal]
    assert import_kwh(st) < BASELINE_IMPORT_KWH
    assert export_kwh(st) < BASELINE_EXPORT_KWH


@pytest.mark.parametrize("goal", ["eigenverbrauch", "nulleinspeisung", "vollladen"])
def test_akku_entlaedt_bei_defizit(sims, goal):
    st = sims[goal]
    assert any(s.modus == "entladen" and s.bat_w > 0 for s in st)


# --- eigenverbrauch: laden statt einspeisen -----------------------------------
def test_eigenverbrauch_laedt_lieber_als_einzuspeisen(sims):
    st = sims["eigenverbrauch"]
    # Kein konkurrierender Abnehmer im Profil: Der Deckel hält den Akku nicht
    # zurück, wenn der Überschuss sonst ins Netz ginge.
    assert st[NOON].soc > 90, f"Midday-SoC {st[NOON].soc} zu niedrig (speist ein)"
    assert export_kwh_below(st, 99) < 4.0
    # Und die Nacht beginnt voll.
    assert st[ABEND].soc > 90


# --- nulleinspeisung: Überschuss aufsaugen -----------------------------------
def test_nulleinspeisung_minimiert_einspeisung_bei_platz(sims):
    st = sims["nulleinspeisung"]
    # Einspeisung, WÄHREND der Akku noch Platz hat, ist klein — er lädt zuerst.
    assert export_kwh_below(st, 99) < 4.0
    # Der Akku wird dabei voll.
    assert max(s.soc for s in st) >= 99


# --- vollladen: Deckel aufgehoben --------------------------------------------
def test_vollladen_erreicht_100(sims):
    st = sims["vollladen"]
    assert max(s.soc for s in st) >= 99
    # Anders als eigenverbrauch lädt es schon mittags durch (kein Tagesdeckel).
    assert st[NOON].soc > 88


# --- Ziele klar unterscheidbar (robuste Vergleiche) ---------------------------
def test_ziele_unterscheiden_sich(sims):
    eig, null, voll = sims["eigenverbrauch"], sims["nulleinspeisung"], sims["vollladen"]
    # Nulleinspeisung und Vollladen heben den Deckel ganz auf und laden deshalb
    # etwas früher durch als der eigenverbrauchsgeführte Tag, der ihn nur
    # überfährt, soweit sonst eingespeist würde.
    assert eig[NOON].soc <= null[NOON].soc
    assert eig[NOON].soc <= voll[NOON].soc
    # Und speisen bei freiem Akku nicht mehr ein als dieser.
    assert export_kwh_below(null, 99) <= export_kwh_below(eig, 99)


# --- Fixture-Integrität -------------------------------------------------------
def test_profil_ist_ein_ueberschusstag():
    prof = load_profile()
    step_h = prof["raster_min"] / 60.0
    pv = sum(prof["pv_w"]) * step_h / 1000
    last = sum(prof["last_w"]) * step_h / 1000
    assert pv > last  # sonst testet der Tag keine Lade-/Schonungslogik
    assert len(prof["pv_w"]) == len(prof["last_w"]) == 96
