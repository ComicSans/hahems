"""Freischwimm-Probe im Entlade-Zweig — ein verriegelter Speicher liefert sich
seinen eigenen Entriegelungs-Beweis, ohne dem arbeitenden Speicher etwas
wegzunehmen.

Anlass ist der 07.09.2026 (siehe tasks/speicher-selbstsperre-ladepfad.md):
L1 und L3 standen bei 99 %, verriegelt (Entscheidung Frage 1), L2 hing an
seiner Ausgangsgrenze bei 1200 W. Der Regler wollte 2116 W ausregeln, bekam
aber nur 1200 W zugeteilt — 916 W kamen aus dem Netz, obwohl L1 und L3 gesund
und fast voll waren.

Die Probe (Entscheidung Frage 2) verteilt NUR den Rest, den die bekannten
Speicher (`known`) nicht decken können — nie Leistung, die ihnen zusteht:

    soll_wunsch = Reglerforderung nach den Wallbox-Klauseln, aber VOR der
                  Kappung auf max_ent
    rest        = soll_wunsch − Σ Zuteilung(known)
    Probe nur, wenn rest ≥ CONTROL_MIN_SETPOINT_W

Die Bedingung ist der ungedeckte Rest, NICHT „known ist am Deckel" — ein
bekannter Speicher an der Reserve hat `anteil ≤ 0`, bekommt 0 W, zählt aber
weiter in `max_ent`. Ein Deckel-Kriterium (Σ Zuteilung ≥ max_ent) verfehlt
diesen Fall (0 ≥ 1200 ist falsch, keine Probe); das Rest-Kriterium erwischt
ihn. Genau das prüft `test_probe_greift_bei_reserve_nicht_bei_deckel`.

Folgt die probierte Einheit, tickt ihr SoC, der push-Sensor meldet, und die
BESTEHENDE Entriegelung über eine frische Meldung (`HemsCoordinator._stumm`)
greift von selbst — kein zweiter Entriegelungs-Eingang nötig.
"""
from __future__ import annotations

from pathlib import Path

from factories import plan_input, storage
from factories import zuteilung as _zuteilung
from hems import planner as P

BASIS = Path(__file__).resolve().parents[1] / "custom_components" / "hems"


# --- Rot vor dem Umbau: die Szene, für die die Probe gebaut wurde ----------


def test_szene_070926_verriegelte_probe_deckt_den_rest():
    # bat_ist = 1200 (L2), fehler = saldo + offset = 1409 + 25 = 1434,
    # gain = 0.65 -> soll_wunsch = 1200 + 1434*0.65 = 2132.1. max_ent = 1200
    # (nur L2 ist known), also zuteilung[L2] = 1200. Rest = 932.1 W. L1 und
    # L3 stehen bei 99 % (gleicher Anteil), L1 gewinnt die Rangfolge über die
    # Reihenfolge in storage_states (kein Leistungs-Bonus, beide power_w=0) —
    # greedy gibt ihm den ganzen Rest, L3 bleibt bei 0.
    r = P.compute_plan(
        plan_input(
            saldo_w=1409,
            storage_states=[
                storage("L2", 88.0, power_w=1200.0),
                storage("L1", 99.0, stale=True),
                storage("L3", 99.0, stale=True),
            ],
        )
    )
    z = _zuteilung(r)
    assert z["L2"] == 1200
    assert z["L1"] >= 60
    assert z.get("L3", 0) == 0
    assert r.regelung.probe_namen == ["L1"]


def test_probe_greift_bei_reserve_nicht_bei_deckel():
    # L1 (known) steht an der Reserve (soc == reserve_soc): anteil = 0, greedy
    # gibt ihm 0 W, er zählt aber weiter in max_ent (1200 W). Mit dem
    # Deckel-Kriterium "Σ Zuteilung >= max_ent" hieße das 0 >= 1200 — keine
    # Probe, obwohl L2 daneben gesund und voll verriegelt ist. Das
    # Rest-Kriterium sieht rest = soll_wunsch - 0 > 0 und lässt die Probe zu.
    r = P.compute_plan(
        plan_input(
            saldo_w=1000,
            storage_states=[
                storage("L1", 10.0, reserve_soc=10.0, power_w=0.0),
                storage("L2", 90.0, stale=True),
            ],
        )
    )
    z = _zuteilung(r)
    assert z.get("L1", 0) == 0
    assert z["L2"] > 0
    assert r.regelung.probe_namen == ["L2"]


# --- Die Invariante: known bleibt unberührt ---------------------------------


def test_zuteilung_bekannter_unveraendert_ueber_saldo_raster():
    # Kern der Entscheidung: die Probe darf `zuteilung[known]` und `soll_w`
    # in keinem Zyklus verändern. Verglichen wird L2 allein gegen dieselbe
    # Lage mit zwei zusätzlichen verriegelten Nachbarn, über ein Raster von
    # Saldo-Werten (darunter der Fall vom 07.09.). known bleibt in beiden
    # Fällen exakt {L2} — L1/L3 sind stale, nehmen an `known` nicht teil.
    for saldo in (100, 500, 1100, 1409, 2500, 4000):
        ohne = P.compute_plan(
            plan_input(
                saldo_w=saldo,
                storage_states=[storage("L2", 88.0, power_w=1200.0)],
            )
        )
        mit = P.compute_plan(
            plan_input(
                saldo_w=saldo,
                storage_states=[
                    storage("L2", 88.0, power_w=1200.0),
                    storage("L1", 99.0, stale=True),
                    storage("L3", 99.0, stale=True),
                ],
            )
        )
        assert _zuteilung(mit).get("L2") == _zuteilung(ohne).get("L2"), saldo
        assert mit.regelung.soll_w == ohne.regelung.soll_w, saldo


# --- Wallbox-Klausel gilt auch für soll_wunsch ------------------------------


def test_probe_beachtet_die_wallbox_klausel_in_soll_wunsch():
    # Review Subtask B, Auflage 1 (tasks/speicher-selbstsperre-ladepfad.md):
    # "kein Akkustrom ins Auto" (battery.py:302-335) deckelt sowohl `soll` als
    # auch `soll_wunsch` — sonst bekäme die Probe über den Rest genau die
    # Leistung, die der Regler dem bekannten Speicher wegen der Wallbox
    # ausdrücklich verweigert. Kein Test bisher setzt `wallbox_w`.
    #
    # Rechnung (saldo_w=1409, offset=25, gain=0.65, bat_ist=1200 von L2,
    # max_ent=1200, wallbox_w=1000, battery_to_ev=False):
    #   soll_wunsch (ungedeckelt)      = 1200 + (1409+25)*0.65   = 2132,1
    #   fehler_ohne_ev                 = 1409 - 1000 + 25        =  434
    #   soll_wunsch (wallbox-gedeckelt)= min(2132,1;
    #                                        1200 + 434*0,65)    = 1482,1
    #   soll (max_ent-gekappt)         = 1200                     -> zuteilung[L2]
    #   rest MIT Klausel   = soll_wunsch − 1200 = 1482,1 − 1200 =  282,1 W
    #   rest OHNE Klausel  =            2132,1 − 1200          =  932,1 W
    # L1 (99 %, stale) gewinnt die Rangfolge und bekommt greedy den ganzen
    # Rest: mit Klausel 282 W, ohne Klausel 932 W (siehe
    # test_szene_070926_verriegelte_probe_deckt_den_rest, gleiche Szene ohne
    # Wallbox). Führte die Probe die Wallbox-Klausel nicht mit, bekäme der
    # verriegelte L1 also die 650 W, die der Regler L2 wegen der Wallbox
    # ausdrücklich verweigert hätte.
    r = P.compute_plan(
        plan_input(
            saldo_w=1409,
            wallbox_w=1000.0,
            battery_to_ev=False,
            storage_states=[
                storage("L2", 88.0, power_w=1200.0),
                storage("L1", 99.0, stale=True),
            ],
        )
    )
    z = _zuteilung(r)
    assert z["L2"] == 1200
    assert z["L1"] == 282
    assert r.regelung.probe_namen == ["L1"]


# --- Wann keine Probe stattfindet -------------------------------------------


def test_keine_probe_wenn_known_die_forderung_deckt():
    # Dieselbe Lage wie test_abgemeldeter_speicher_bekommt_keine_entladeleistung
    # (tests/test_speicher_abgemeldet.py): bat_ist = 0, fehler = 1125,
    # gain = 0.65 -> soll = 731.25. L3 (Anteil 1,78 kWh) gewinnt die Rangfolge
    # vor L2 (1,56 kWh) und deckt greedy allein -> Rest 0, keine Probe.
    r = P.compute_plan(
        plan_input(
            saldo_w=1100,
            storage_states=[
                storage("L1", 100.0, stale=True),
                storage("L2", 88.0),
                storage("L3", 99.0),
            ],
        )
    )
    assert r.regelung.probe_namen == []
    assert "L1" not in _zuteilung(r)


def test_keine_probe_im_lademodus():
    # Spiegelbild von test_abgemeldeter_speicher_bekommt_auch_keine_ladeleistung
    # (tests/test_speicher_abgemeldet.py) — die Probe steht nur im
    # Entlade-Zweig, der Lade-Zweig bleibt unverändert.
    r = P.compute_plan(
        plan_input(
            saldo_w=-3000,
            storage_states=[
                storage("L1", 20.0, stale=True),
                storage("L2", 60.0),
                storage("L3", 60.0),
            ],
        )
    )
    assert r.regelung.modus == "laden"
    assert r.regelung.probe_namen == []
    assert "L1" not in _zuteilung(r)


def test_keine_probe_unter_der_reserve():
    # L1 ist verriegelt, aber mit 5 % unter seiner Reserve (10 %): anteil = 0,
    # dieselbe Teilnahme-Schranke wie bei `known` gilt auch für die Probe.
    r = P.compute_plan(
        plan_input(
            saldo_w=1409,
            storage_states=[
                storage("L2", 88.0, power_w=1200.0),
                storage("L1", 5.0, reserve_soc=10.0, stale=True),
            ],
        )
    )
    assert "L1" not in _zuteilung(r)
    assert r.regelung.probe_namen == []


def test_keine_probe_unter_dem_mindest_setpoint():
    # bat_ist = 1200 (L2), fehler = 25 + 25 = 50, gain = 0.65 ->
    # soll_wunsch = 1200 + 50*0.65 = 1232.5. max_ent = 1200, zuteilung[L2]
    # deckt greedy die vollen 1200 W. Rest = 32.5 W, unter
    # CONTROL_MIN_SETPOINT_W (60) — keine Probe, obwohl L1 verriegelt und
    # gesund (Anteil > 0) daneben steht.
    r = P.compute_plan(
        plan_input(
            saldo_w=25,
            storage_states=[
                storage("L2", 88.0, power_w=1200.0),
                storage("L1", 99.0, stale=True),
            ],
        )
    )
    assert r.regelung.probe_namen == []
    assert "L1" not in _zuteilung(r)


# --- Sichtbarkeit ------------------------------------------------------------


def test_probe_sichtbar_im_sensor():
    # Eine probierte Einheit steht zugleich in `zuteilung` und in
    # `abgemeldet` — ohne eigenes Attribut widerspräche sich der Sensor
    # selbst (Entscheidung Frage 2, „Sichtbarkeit").
    quelle = (BASIS / "sensor.py").read_text(encoding="utf-8")
    assert '"probe"' in quelle
