"""EV↔Akku-Ladevorrang (strategies/coordination.py).

Vor dem Fix ignorierte die Aktuierung den priority_mode: die Wallbox bediente
sich immer zuerst am Überschuss, der Akku bekam nur den Rest ("das E-Auto
gewann die Ladehoheit immer"). Diese Tests belegen, dass der Vorrang jetzt
echt greift — und dass ein laufendes Auto nie abgeregelt wird.
"""
from __future__ import annotations

from factories import load, plan_input, storage, zuteilung
from hems import planner as P
from hems.strategies.types import PlanFlags


def _run(mode, *, wb_on, socs=(60, 60, 60), saldo=-6000.0, knapp=True):
    wb_w = 4200.0 if wb_on else 0.0
    wb = load("WB", power_w=wb_w, ist_an=wb_on, an_seit_s=3600, nachfrage=wb_on)
    flags = PlanFlags()
    flags.knapp = knapp
    r = P.compute_plan(
        plan_input(
            socs=list(socs),
            saldo_w=saldo,
            modulateds=[wb],
            wallbox_w=wb_w,
            priority_mode=mode,
            flags=flags,
        )
    )
    return r


def test_battery_first_gibt_akku_vorrang():
    # Gleicher Überschuss: battery_first lädt den Akku deutlich stärker und die
    # Wallbox schwächer als ev_first.
    bf = _run("battery_first", wb_on=True)
    ef = _run("ev_first", wb_on=True)
    assert sum(zuteilung(bf).values()) > sum(zuteilung(ef).values())
    assert bf.ev_regelung.soll_summe_w < ef.ev_regelung.soll_summe_w


def test_battery_first_reisst_laufendes_auto_nicht_ab():
    # Ein bereits laufendes Auto behält mindestens sein Minimum (6 A × 3 × 230).
    bf = _run("battery_first", wb_on=True)
    ev_min = 6.0 * 3 * 230.0
    assert bf.ev_regelung.soll_summe_w >= ev_min
    assert bf.ev_regelung.lasten[0].laden is True


def test_battery_first_haelt_ausgeschaltetes_auto_zurueck():
    # Akku-Vorrang: ein noch nicht laufendes Auto startet nicht, solange der
    # Akku den Überschuss braucht; der Akku lädt.
    bf = _run("battery_first", wb_on=False)
    assert bf.ev_regelung.soll_summe_w == 0
    assert bf.regelung.modus == "laden"
    assert sum(zuteilung(bf).values()) > 0


def test_am_ladedeckel_bekommt_auto_alles():
    # Akku am Tagesdeckel (78 %) reserviert nichts mehr — die Wallbox bekommt den
    # vollen Überschuss, auch bei battery_first.
    bf = _run("battery_first", wb_on=True, socs=(78, 78, 78))
    ef = _run("ev_first", wb_on=True, socs=(78, 78, 78))
    assert bf.ev_regelung.soll_summe_w == ef.ev_regelung.soll_summe_w
    assert sum(zuteilung(bf).values()) == 0


def test_ev_first_unveraendert():
    # ev_first reserviert nie — der Akku bekommt nur das Residuum.
    ef = _run("ev_first", wb_on=True)
    assert ef.ev_regelung.ueberschuss_w == 10200
    assert ef.ev_regelung.soll_summe_w == 9660


def test_auto_folgt_knapp_latch():
    # auto = battery_first bei knappem Tag, sonst ev_first.
    knapp = _run("auto", wb_on=True, knapp=True)
    reichlich = _run("auto", wb_on=True, knapp=False)
    assert knapp.ev_regelung.soll_summe_w < reichlich.ev_regelung.soll_summe_w
    assert sum(zuteilung(knapp).values()) > sum(zuteilung(reichlich).values())


def test_koordination_konvergiert_ueber_zyklen():
    """Mehrzyklus-Rückkopplung: Messleistung folgt dem Soll des Vorzyklus. Der
    gekoppelte EV-/Akku-Regelkreis muss in einen festen Punkt einlaufen, statt
    um die Ladehoheit zu pendeln (kein Dauer-Gerangel)."""
    pv, house, lag = 9000.0, 400.0, 0.6
    wallbox, bat = 4140.0, 0.0
    flags = None
    tail = []
    for i in range(20):
        saldo = house + wallbox - pv - bat
        ss = [storage(f"L{k+1}", 60.0, power_w=bat / 3) for k in range(3)]
        on = wallbox > 100
        wb = load("WB", power_w=wallbox, ist_an=on, an_seit_s=3600, nachfrage=on)
        r = P.compute_plan(
            plan_input(
                storage_states=ss,
                saldo_w=saldo,
                modulateds=[wb],
                wallbox_w=wallbox,
                priority_mode="battery_first",
                flags=flags,
                gain_level="max",
            )
        )
        flags = r.flags
        ev = r.ev_regelung.soll_summe_w
        bs = r.regelung.soll_w
        wallbox += lag * (ev - wallbox)
        bat += lag * (bs - bat)
        if i >= 15:
            tail.append((round(ev), round(bs)))
    # Im Schwanz kaum noch Bewegung -> konvergiert (kein Pendeln).
    ev_span = max(t[0] for t in tail) - min(t[0] for t in tail)
    bat_span = max(t[1] for t in tail) - min(t[1] for t in tail)
    assert ev_span < 50, f"EV pendelt: {tail}"
    assert bat_span < 50, f"Akku pendelt: {tail}"
