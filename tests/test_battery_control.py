"""Charakterisierung der Saldo-Speicherregelung (`_storage_control`).

Laden verteilt parallel (proportional zur freien Kapazität), Entladen greedy
mit Auswahl-Hysterese (ein Akku zur Zeit, gegen Verschleiß).
"""
from __future__ import annotations

from factories import plan_input, storage, switchable, zuteilung
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
    # Gleiche SoCs, gleiche freie Kapazität: jeder Speicher bekommt denselben
    # Anteil am gedämpften Regelziel (487,5 W, auf ganze Watt gerundet).
    assert z["L1"] == z["L2"] == z["L3"]
    assert abs(sum(z.values()) - 487.5) <= 1.5


def test_laden_proportional_zur_freien_kapazitaet():
    # Leerster Akku bekommt am meisten (SoC-Ausgleich). Der Überschuss ist
    # bewusst größer als die Ladegrenze eines einzelnen Speichers (1200 W):
    # sonst nähme der leerste ihn allein auf und die Verteilung bliebe ungeprüft.
    r = P.compute_plan(plan_input(socs=[40, 60, 70], saldo_w=-6000))
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


# --- Schaltlast-Feedforward (Wärmepumpe & Co.) ---------------------------------
def test_schaltlast_zuschaltung_wirkt_sofort_auf_speicherregelung():
    # WP schaltet mit 500 W zu (Überschuss deckt sie), bevor das real im Saldo
    # ankommt (Aktuierungs-Totzeit der Last selbst). Die Speicherregelung soll
    # das schon in diesem Zyklus dämpfend einrechnen — exakt so, als läge der
    # Saldo bereits um die 500 W höher (Bezug/weniger Einspeisung).
    r_feedforward = P.compute_plan(
        plan_input(
            socs=[60, 60, 60],
            saldo_w=-899,
            switchables=[switchable("WP", erwartet_w=500.0, ist_an=False)],
        )
    )
    assert r_feedforward.schaltbare.lasten[0].an is True
    assert r_feedforward.schaltbare.delta_w == 500
    r_aequivalent = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-399))
    assert r_feedforward.regelung.soll_w == r_aequivalent.regelung.soll_w == -187.0
    assert zuteilung(r_feedforward) == zuteilung(r_aequivalent)


def test_schaltlast_abschaltung_bremst_ladung_aber_echter_saldo_bleibt_geschuetzt():
    # WP schaltet ab (500 W weniger Last), aber der Netz-Bezug ist real noch
    # hoch (+300 W) — die Last hat real noch nicht nachgelassen. Der
    # ECHTE-Saldo-Schutz (battery.py) verhindert, dass die Feedforward-
    # Korrektur allein daraufhin schon in den Bezug hineinlädt: die Regelung
    # pausiert, statt zu laden.
    r = P.compute_plan(
        plan_input(
            socs=[60, 60, 60],
            saldo_w=300,
            switchables=[
                switchable("WP", erwartet_w=500.0, ist_an=True, power_w=500.0)
            ],
        )
    )
    assert r.schaltbare.lasten[0].an is False
    assert r.schaltbare.delta_w == -500
    assert r.regelung.modus == "pausiert"
    assert r.regelung.soll_w == 0.0


def test_standby_einer_ausgeschalteten_last_verschiebt_den_sollpunkt_nicht():
    # Am 05.08.2026 gemessen: Die Heizung lag unter Sommersperre (aus, bleibt
    # aus), zog aber weiter 181 W Standby. Die alte Bestandsbilanz
    # (soll_w − mess_sw) machte daraus eine dauerhafte Feedforward-Korrektur von
    # −181 W; der Regler sah bei 145 W realem Bezug −36 W (Einspeisung), blieb
    # im Totband und ließ den Bezug laufen, während drei volle Akkus danebenstanden.
    r = P.compute_plan(
        plan_input(
            socs=[93, 93, 94],
            saldo_w=145,
            switchables=[
                switchable(
                    "Heizung", erwartet_w=826.0, ist_an=False, power_w=181.0,
                    aus_seit_s=3600,
                )
            ],
        )
    )
    assert r.schaltbare.lasten[0].an is False
    assert r.schaltbare.delta_w == 0
    assert r.regelung.fehler_w == 170.0
    assert r.regelung.modus == "entladen"
    assert sum(zuteilung(r).values()) > 0


# --- Totband ------------------------------------------------------------------
def test_kleiner_saldo_pausiert():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=-20))
    assert r.regelung.modus == "pausiert"
    assert sum(zuteilung(r).values()) == 0


# --- Fehlende Eingaben --------------------------------------------------------
def test_ohne_saldo_keine_regelung():
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=None))
    assert r.regelung is None
