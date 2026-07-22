"""Schaltbare Lasten (strategies/switchable.py) — überschussgesteuert an/aus.

Kernanforderung: bei knappem Überschuss drosseln modulierbare Lasten herunter
(geben Überschuss frei), bevor eine schaltbare Last abgeschaltet wird.
"""
from __future__ import annotations

from factories import load, plan_input, switchable, zuteilung
from hems import planner as P


def _plan(switchables, **kw):
    return P.compute_plan(plan_input(switchables=switchables, **kw))


def _an(res, name):
    return next(l.an for l in res.schaltbare.lasten if l.name == name)


# --- Grundverhalten -----------------------------------------------------------
def test_keine_lasten_keine_empfehlung():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-4000))
    assert r.schaltbare is None


def test_einschalten_bei_ausreichendem_ueberschuss():
    r = _plan([switchable("Pumpe", erwartet_w=1500)], socs=[60, 60, 60], saldo_w=-4000)
    assert _an(r, "Pumpe") is True
    assert r.schaltbare.soll_w == 1500


def test_aus_bei_zu_kleinem_ueberschuss():
    r = _plan([switchable("Pumpe", erwartet_w=1500)], socs=[60, 60, 60], saldo_w=-500)
    assert _an(r, "Pumpe") is False
    assert r.schaltbare.soll_w == 0


# --- Priorität ----------------------------------------------------------------
def test_wichtigere_last_zuerst():
    # Überschuss ~2000 reicht nur für eine 1500-W-Last -> die wichtigere (prio 1).
    lasten = [
        switchable("Wichtig", id="a", priority=1, erwartet_w=1500),
        switchable("Unwichtig", id="b", priority=2, erwartet_w=1500),
    ]
    r = _plan(lasten, socs=[60, 60, 60], saldo_w=-2000)
    assert _an(r, "Wichtig") is True
    assert _an(r, "Unwichtig") is False


# --- Anti-Takt ----------------------------------------------------------------
def test_min_on_haelt_an():
    # Kein Überschuss, aber innerhalb der Mindestlaufzeit -> bleibt an.
    sw = switchable("Pumpe", erwartet_w=1500, ist_an=True, an_seit_s=60, min_on_min=20)
    r = _plan([sw], socs=[60, 60, 60], saldo_w=2000)
    assert _an(r, "Pumpe") is True
    assert "min_on" in next(l.grund for l in r.schaltbare.lasten if l.name == "Pumpe")


def test_min_off_haelt_aus():
    # Überschuss da, aber innerhalb der Mindestpause -> bleibt aus.
    sw = switchable("Pumpe", erwartet_w=1500, ist_an=False, aus_seit_s=60, min_off_min=10)
    r = _plan([sw], socs=[60, 60, 60], saldo_w=-4000)
    assert _an(r, "Pumpe") is False


def test_max_block_erzwingt_an():
    # Zu lange ausgehalten -> an, auch ohne Überschuss.
    sw = switchable("Pumpe", erwartet_w=1500, ist_an=False, aus_seit_s=8000, max_block_min=120)
    r = _plan([sw], socs=[60, 60, 60], saldo_w=2000)
    assert _an(r, "Pumpe") is True


# --- Hysterese ----------------------------------------------------------------
def test_hysterese_laufende_last_bleibt_laenger_an():
    # Überschuss knapp unter erwartet: eine laufende Last (an) bleibt an
    # (Schwelle erwartet-Marge = 1300).
    an = switchable("An", id="a", erwartet_w=1500, ist_an=True, an_seit_s=99999, power_w=1400)
    r = _plan([an], socs=[60, 60, 60], saldo_w=-1400)
    assert _an(r, "An") is True


def test_hysterese_wartende_last_bleibt_aus():
    # Gleicher Überschuss (1600), aber eine wartende (aus) Last startet erst ab
    # erwartet+Marge = 1700 — bleibt also aus. Belegt die andere Hysterese-Seite.
    aus = switchable("Aus", id="b", erwartet_w=1500, ist_an=False, aus_seit_s=None)
    r = _plan([aus], socs=[60, 60, 60], saldo_w=-1600)
    assert _an(r, "Aus") is False


# --- Kernanforderung: modulierbare Last drosselt vor Abschaltung --------------
def test_modulierbare_last_drosselt_fuer_schaltlast():
    wb = load("WB", power_w=6000.0, ist_an=True, an_seit_s=3600, nachfrage=True)
    ohne = P.compute_plan(plan_input(
        socs=[60, 60, 60], saldo_w=-2000, modulateds=[wb], wallbox_w=6000.0,
        priority_mode="ev_first"))
    mit = P.compute_plan(plan_input(
        socs=[60, 60, 60], saldo_w=-2000, modulateds=[wb], wallbox_w=6000.0,
        priority_mode="ev_first",
        switchables=[switchable("Pumpe", erwartet_w=2000, ist_an=False)]))
    # Die Wallbox drosselt herunter, die Schaltlast läuft.
    assert mit.ev_regelung.soll_summe_w < ohne.ev_regelung.soll_summe_w
    assert mit.schaltbare.soll_w == 2000
    assert _an(mit, "Pumpe") is True


# --- delta_w ------------------------------------------------------------------
def test_delta_neue_last_reserviert_bereits_laufende_nicht():
    # Neue Last: delta = volle erwartete Leistung.
    neu = _plan([switchable("Neu", erwartet_w=1500, ist_an=False)],
                socs=[60, 60, 60], saldo_w=-4000)
    assert neu.schaltbare.delta_w == 1500
    # Bereits laufende Last (gemessen ~erwartet): delta ~ 0.
    laufend = _plan(
        [switchable("Alt", erwartet_w=1500, ist_an=True, an_seit_s=99999, power_w=1500)],
        socs=[60, 60, 60], saldo_w=-4000)
    assert abs(laufend.schaltbare.delta_w) < 100
