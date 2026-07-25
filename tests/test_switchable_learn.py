"""Gelernte Leistungsaufnahme schaltbarer Lasten (`lern_leistung`).

Hintergrund: Der gelernte Wert steuert, ab welchem Überschuss eine Last
eingeschaltet wird. Ein zu niedrig gelernter Wert schaltet sie zu früh ein, sie
zieht real mehr, und der nächste Zyklus schaltet sie wieder ab — Takten. Genau
das entstand, als ein Anlaufwert einer Wärmepumpe (~200 W) als Erwartung
übernommen und persistiert wurde.
"""
from __future__ import annotations

from hems.const import (
    SWITCH_LEARN_FLOOR_HEAT_W,
    SWITCH_LEARN_FLOOR_W,
    SWITCH_LEARN_WARMUP_S,
)
from hems.strategies.switchable import lern_leistung

WARM = SWITCH_LEARN_WARMUP_S + 1  # Karenz sicher überschritten


# --- Anlaufkarenz -------------------------------------------------------------
def test_anlauf_lernt_nicht():
    # Frisch eingeschaltet: der Messwert ist noch nicht repräsentativ.
    assert lern_leistung(None, 1800.0, 30.0, floor_w=SWITCH_LEARN_FLOOR_W) is None


def test_nach_karenz_lernt():
    assert lern_leistung(None, 1800.0, WARM, floor_w=SWITCH_LEARN_FLOOR_W) == 1800.0


def test_ohne_laufzeit_lernt_nicht():
    # Kein Schalterzustand (Entity fehlt) -> keine Aussage über die Laufzeit.
    assert lern_leistung(None, 1800.0, None, floor_w=SWITCH_LEARN_FLOOR_W) is None


# --- Boden --------------------------------------------------------------------
def test_standby_unter_boden_lernt_nicht():
    assert lern_leistung(None, 5.0, WARM, floor_w=SWITCH_LEARN_FLOOR_W) is None


def test_waermepumpe_anlaufsockel_unter_heizboden_lernt_nicht():
    # Der reale Fehlerfall: 204 W Regelung/Umwälzpumpe, Kompressor noch aus.
    assert (
        lern_leistung(None, 203.71, WARM, floor_w=SWITCH_LEARN_FLOOR_HEAT_W) is None
    )


def test_kleine_last_lernt_ueber_allgemeinem_boden():
    # Ein 290-W-Luftentfeuchter ist keine Wärmepumpe: allgemeiner Boden gilt.
    assert lern_leistung(None, 291.0, WARM, floor_w=SWITCH_LEARN_FLOOR_W) == 291.0


# --- Asymmetrie ---------------------------------------------------------------
def test_nach_oben_sofort():
    # Unterschätzte Last provoziert Netzbezug -> sofort korrigieren.
    assert lern_leistung(204.0, 1800.0, WARM, floor_w=SWITCH_LEARN_FLOOR_HEAT_W) == 1800.0


def test_nach_unten_gedaempft():
    # Teillast zieht den Wert nur anteilig herunter (Standard-Dämpfung 25 %).
    neu = lern_leistung(2000.0, 1000.0, WARM, floor_w=SWITCH_LEARN_FLOOR_HEAT_W)
    assert 1000.0 < neu < 2000.0
    assert neu == 1750.0


def test_nach_unten_konvergiert_gegen_die_messung():
    wert = 2000.0
    for _ in range(30):
        wert = lern_leistung(wert, 1000.0, WARM, floor_w=SWITCH_LEARN_FLOOR_HEAT_W)
    # Nähert sich der Messung an, unterschreitet sie aber nie.
    assert 1000.0 <= wert < 1005.0
