"""Speicher-Zwangsladung: laden gegen den Netzsaldo, mit Ende.

Die Saldo-Regelung lädt den Akku ausschließlich gegen Export — steht der Saldo
im Bezug, wird der Sollwert positiv und der Akku entlädt. Kein Optimierungsziel
ändert daran etwas: `vollladen` (im Panel „Laden") hebt nur das Ladeziel auf
100 %, und die Notstromreserve beschleunigt nur das Einsammeln von Überschuss.
Der Schalter `battery_force` ist die einzige Ausnahme — er ist damit auch der
einzige Pfad in HEMS, der Netzstrom in den Speicher schiebt.

Die Tests hier halten beide Hälften fest: dass der Zwang wirklich gegen jeden
Deckel lädt (sonst wäre er wirkungslos), und dass er von selbst endet (sonst
kaufte er ab dem ersten Nachtverbrauch dauerhaft Strom nach).
"""
from __future__ import annotations

from factories import load, plan_input, storage, zuteilung
from hems import planner as P
from hems.strategies import coordination


# --- Laden gegen den Saldo -----------------------------------------------------
def test_zwang_laedt_trotz_netzbezug_mit_voller_leistung():
    # Nachtlage: 1000 W Bezug, halbvolle Speicher. Ohne Zwang entlädt die
    # Regelung (nächster Test); mit Zwang steht der Sollwert fest auf der
    # Summe der Ladegrenzen (3 × 1200 W).
    r = P.compute_plan(
        plan_input(socs=[50, 50, 50], saldo_w=1000, battery_force=True)
    )
    assert r.regelung.modus == "laden"
    assert r.regelung.soll_w == -3600
    assert sum(zuteilung(r).values()) == 3600
    # Der Regelfehler bleibt unangetastet: Der Sensor soll weiter zeigen, was
    # der Regler ohne den Zwang täte — sonst wäre der Eingriff unsichtbar.
    assert r.regelung.fehler_w > 0
    assert r.regelung.zwang_aktiv is True


def test_ohne_zwang_entlaedt_dieselbe_lage():
    r = P.compute_plan(plan_input(socs=[50, 50, 50], saldo_w=1000))
    assert r.regelung.modus == "entladen"
    assert r.regelung.zwang_aktiv is False


def test_zwang_laesst_sich_von_der_wallbox_klausel_nicht_deckeln():
    # „Nicht in den Netzbezug hineinladen" (Asymmetrie-Klausel in
    # `_storage_control`) deckelt den Lade-Sollwert gegen den ECHTEN Saldo,
    # sobald HEMS die Wallbox herunterregelt. Genau dieser Deckel machte einen
    # Zwang wirkungslos — der Override steht deshalb hinter beiden Klauseln.
    r = P.compute_plan(
        plan_input(
            socs=[50, 50, 50],
            saldo_w=4000,
            wallbox_w=3600,
            modulateds=[load(power_w=3600, ist_an=True, nachfrage=True)],
            ev_force=True,
            battery_force=True,
        )
    )
    assert r.regelung.modus == "laden"
    assert sum(zuteilung(r).values()) == 3600


def test_zwang_laedt_nur_meldende_speicher():
    # Ein abgemeldeter Speicher (`stale`) hat keinen SoC mehr, sondern nur
    # einen zuletzt bekannten. Er nimmt an der Zuteilung nicht teil, und der
    # Zwang fordert entsprechend nur die Ladeleistung der übrigen.
    r = P.compute_plan(
        plan_input(
            storage_states=[
                storage("L1", 50),
                storage("L2", 50),
                storage("L3", 50, stale=True),
            ],
            saldo_w=1000,
            battery_force=True,
        )
    )
    z = zuteilung(r)
    assert r.regelung.soll_w == -2400
    assert z["L1"] == z["L2"] == 1200
    assert z.get("L3", 0) == 0


# --- Ladestrategie: Ziel 100 %, sofort, ohne Mittagspause ---------------------
def test_zwang_hebt_ziel_deckel_und_mittagspause_auf():
    # Referenzlage der Factories ist 13:00 lokal — mitten in der Mittags-
    # Ladepause, in der der Akku sonst hinter die Lasten zurücktritt.
    r = P.compute_plan(
        plan_input(socs=[50, 50, 50], saldo_w=1000, battery_force=True)
    )
    assert r.speicher_ziel_soc == 100.0
    assert r.lade_deckel_soc == 100.0
    assert r.lade_start is None  # sofort, nicht just in time
    assert r.lade_pause is False


def test_zwang_gibt_dem_akku_vorrang_vor_den_lasten():
    # Wer den Akku zwingt, will ihn vor dem Auto — die eingestellte Priorität
    # („E-Auto zuerst") tritt zurück, wie bei der Notstromreserve.
    inp = plan_input(priority_mode="ev_first", battery_force=True)
    assert coordination.akku_hat_vorrang(inp) is True
    assert coordination.akku_hat_vorrang(plan_input(priority_mode="ev_first")) is False


# --- Sichtbarkeit -------------------------------------------------------------
def test_empfehlung_benennt_den_zwang_auch_ohne_ueberschuss():
    # Ohne Überschuss steigt die Empfehlung sonst früh mit „kein Überschuss"
    # aus. Wer gerade Strom kauft, soll das aber in der Empfehlung lesen.
    r = P.compute_plan(
        plan_input(
            socs=[50, 50, 50],
            saldo_w=1000,
            pv_remaining_kwh=0.0,
            battery_force=True,
        )
    )
    assert any("Zwang, notfalls aus dem Netz" in p for p in r.prioritaeten)
    assert not any("kein Überschuss" in p for p in r.prioritaeten)


# --- Ende der Zwangsladung ----------------------------------------------------
def test_zwang_endet_am_physischen_ladeende_nicht_erst_bei_100_prozent():
    # Der wichtigste Test der Datei: Ein Zendure Hyper 2000 meldet 100 %
    # faktisch nie — er steht bei 99 % im CV-Taper und nimmt nichts mehr
    # (`SPEICHER_VOLL_SOC`, Befund 07.09.2026). Ein Ende-Kriterium „SoC < 100"
    # bliebe hier für immer offen und kommandierte drei satten BMS dauerhaft
    # die volle Ladeleistung — auf Netzkosten und mit Lade-Quittungen, die
    # niemand einlösen kann.
    r = P.compute_plan(
        plan_input(socs=[99, 99, 99], saldo_w=1000, battery_force=True)
    )
    assert r.speicher_zwang_fertig is True
    assert r.regelung.zwang_aktiv is False
    # Ab hier regelt wieder der Saldo: ein voller Akku, der den Hausverbrauch
    # deckt, ist genau das Ergebnis, das der Zwang erreichen wollte.
    assert r.regelung.modus == "entladen"


def test_zwang_endet_bei_vollen_speichern():
    r = P.compute_plan(
        plan_input(socs=[100, 100, 100], saldo_w=1000, battery_force=True)
    )
    assert r.speicher_zwang_fertig is True


def test_zwang_uebergeht_volle_speicher_und_laedt_den_rest():
    # Gemischter Stand: Zwei Speicher stehen am Ladeschluss, einer hat echten
    # Platz. Nur der bekommt Leistung — die beiden anderen bekämen sonst einen
    # Sollwert auf ihre rechnerisch freie Kapazität (99 → 100 %), die im Taper
    # nicht mehr abrufbar ist.
    r = P.compute_plan(
        plan_input(socs=[99, 99, 60], saldo_w=1000, battery_force=True)
    )
    assert r.speicher_zwang_fertig is False
    assert r.regelung.modus == "laden"
    z = zuteilung(r)
    assert z["L3"] == 1200
    assert z["L1"] == z["L2"] == 0
    assert r.regelung.soll_w == -1200


def test_zwang_endet_nicht_solange_ein_speicher_platz_hat():
    r = P.compute_plan(
        plan_input(socs=[100, 100, 60], saldo_w=1000, battery_force=True)
    )
    assert r.speicher_zwang_fertig is False
    assert r.regelung.modus == "laden"
    assert zuteilung(r)["L3"] > 0


def test_abgemeldete_speicher_beenden_den_zwang_nicht():
    # Alle Speicher stumm: Die Regelung fällt in ihre ausdrücklich passive
    # Empfehlung (0 W an alle). Das ist ein Ausfall, kein erfülltes Ladeziel —
    # ein Zwang, den ein Broker-Neustart beendet, wäre keiner.
    r = P.compute_plan(
        plan_input(
            storage_states=[
                storage("L1", 50, stale=True),
                storage("L2", 50, stale=True),
            ],
            saldo_w=1000,
            battery_force=True,
        )
    )
    assert r.regelung.modus == "pausiert"
    assert r.speicher_zwang_fertig is False


def test_ohne_zwang_meldet_ein_voller_speicher_kein_ende():
    # Gegenprobe zur Bedingung: Eine 0-W-Zuteilung im Lade-Zweig ist ohne
    # Zwang der Normalfall (Deckel erreicht) und bedeutet gerade kein Ende.
    r = P.compute_plan(plan_input(socs=[100, 100, 100], saldo_w=-3000))
    assert r.speicher_zwang_fertig is False
