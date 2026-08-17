"""Ein Speicher, der nicht mehr meldet, darf keine Leistung mehr zugeteilt
bekommen.

Anlass ist der 15.08.2026. Einer von drei Zendure Hyper 2000 fiel um 12:54 aus:
Die Integration lieferte weiter Zustände, aber keine neuen — `electric_level`
blieb auf 100 % stehen, `bat_in_out` auf 0 W, `connection_status` fiel auf 0.
Kein Wert wurde `unavailable`, keine Meldung ging ins Log.

Die Folge steckt in der Zuteilung: Entladen bündelt greedy auf den Speicher mit
der meisten verfügbaren Energie, und mit eingefrorenen 100 % gewinnt der
ausgefallene Speicher diese Rangfolge ab sofort in jedem Zyklus. Er bekam die
volle Anforderung, die beiden lebenden bekamen 0 W — fünfeinhalb Stunden lang,
bei 1,1 kW Netzbezug und 95,7 % gemeldetem Gesamt-SoC.

Zwei Nähte, beide hier abgesichert: die Erkennung (Coordinator, prüft
`last_reported` — HA-nah, deshalb über den Syntaxbaum gelesen) und die
Konsequenz (`_storage_control`, HA-frei und direkt testbar).
"""
from __future__ import annotations

import ast
from pathlib import Path

from factories import plan_input, storage, zuteilung
from hems import planner as P
from hems.const import STORAGE_STALE_MIN

BASIS = Path(__file__).resolve().parents[1] / "custom_components" / "hems"


def _funktion(datei: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    baum = ast.parse((BASIS / datei).read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if (
            isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef))
            and knoten.name == name
        ):
            return knoten
    raise AssertionError(f"{name} nicht in {datei} gefunden")


def _feldtabelle() -> set[str]:
    """Die Schlüssel von `_DECISION_FIELDS` — die einzige Liste, über die
    `diff_snapshots` läuft."""
    baum = ast.parse((BASIS / "changelog.py").read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        ziel = getattr(knoten, "target", None)
        if isinstance(knoten, ast.AnnAssign) and getattr(ziel, "id", "") == (
            "_DECISION_FIELDS"
        ):
            return {k.value for k in knoten.value.keys}
    raise AssertionError("_DECISION_FIELDS nicht gefunden")


# --- Die Konsequenz in der Regelung -----------------------------------------


def test_abgemeldeter_speicher_bekommt_keine_entladeleistung():
    # Die Lage vom 15.08.2026, auf drei Speicher eingedampft: L1 steht auf
    # eingefrorenen 100 %, L2 und L3 leben und stehen darunter.
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
    assert r.regelung.modus == "entladen"
    z = zuteilung(r)
    # Ohne die Prüfung gewänne L1 die Rangfolge und nähme alles.
    assert "L1" not in z
    assert sum(z.values()) > 0


def test_der_abgemeldete_speicher_steht_im_ergebnis():
    # Eine stillschweigend halbierte Anlage ist von einer vollständigen sonst
    # nicht zu unterscheiden — der Name gehört sichtbar gemacht.
    r = P.compute_plan(
        plan_input(
            saldo_w=1100,
            storage_states=[
                storage("L1", 100.0, stale=True),
                storage("L2", 88.0),
            ],
        )
    )
    assert r.regelung.abgemeldet_namen == ["L1"]


def test_abgemeldeter_speicher_bekommt_auch_keine_ladeleistung():
    # Dieselbe Begründung in der Gegenrichtung: Wer nicht meldet, führt auch
    # keinen Ladebefehl aus — der Überschuss ginge ins Netz.
    r = P.compute_plan(
        plan_input(
            saldo_w=-3000,
            storage_states=[
                storage("L1", 20.0, stale=True),  # viel freie Kapazität
                storage("L2", 60.0),
                storage("L3", 60.0),
            ],
        )
    )
    assert r.regelung.modus == "laden"
    assert "L1" not in zuteilung(r)


def test_eingefrorene_leistung_geht_nicht_in_den_regler():
    # `bat_ist` ist die gemessene Basis, auf die der Regler seinen Schritt
    # addiert. Der stehengebliebene Wert eines abgemeldeten Speichers ist kein
    # Messwert mehr; ginge er ein, rechnete der Regler mit einer Leistung, die
    # niemand liefert.
    gemeinsam = dict(saldo_w=1100)
    mit_geist = P.compute_plan(
        plan_input(
            storage_states=[
                storage("L1", 100.0, power_w=800.0, stale=True),
                storage("L2", 88.0, power_w=0.0),
            ],
            **gemeinsam,
        )
    )
    ohne_geist = P.compute_plan(
        plan_input(
            storage_states=[storage("L2", 88.0, power_w=0.0)],
            **gemeinsam,
        )
    )
    assert mit_geist.regelung.soll_w == ohne_geist.regelung.soll_w


def test_alle_speicher_abgemeldet_heisst_passiv_statt_stumm():
    # Bei drei Geräten an einem MQTT-Pfad fallen alle gemeinsam aus. „Keine
    # Empfehlung" wäre hier das Schlechteste: Der Actuator schriebe dann gar
    # nichts, und der zuletzt kommandierte Sollwert liefe blind weiter.
    r = P.compute_plan(
        plan_input(
            saldo_w=1100,
            storage_states=[
                storage("L1", 100.0, stale=True),
                storage("L2", 88.0, stale=True),
            ],
        )
    )
    assert r.regelung is not None
    assert r.regelung.modus == "pausiert"
    assert set(zuteilung(r).values()) == {0}
    # Und der Grund bleibt sichtbar — Sensor und Log lesen beide aus `regelung`.
    assert r.regelung.abgemeldet_namen == ["L1", "L2"]


def test_ohne_jeden_soc_bleibt_es_bei_keiner_empfehlung():
    # Der Pfad daneben, unverändert: Wer nie einen SoC geliefert hat, hat auch
    # keinen Sollwert stehen, der abgeschaltet gehörte.
    r = P.compute_plan(
        plan_input(
            saldo_w=1100,
            storage_states=[storage("L1", None), storage("L2", None)],
        )
    )
    assert r.regelung is None


def test_meldende_speicher_bleiben_unberuehrt():
    # Der Normalfall darf sich nicht verschieben: ohne abgemeldeten Speicher
    # dieselbe Zuteilung wie vor der Prüfung.
    r = P.compute_plan(plan_input(socs=[100, 88, 99], saldo_w=1100))
    z = zuteilung(r)
    assert r.regelung.abgemeldet_namen == []
    assert z["L1"] > 0  # höchster SoC führt greedy, wie bisher


# --- Die Erkennung im Coordinator -------------------------------------------


def test_frist_liegt_ueber_dem_meldetakt_und_unter_dem_schaden():
    # Zu kurz wirft bei jeder Netzlücke einen gesunden Speicher aus der
    # Regelung, zu lang lässt den Ausfall Energie kosten.
    assert 5.0 <= STORAGE_STALE_MIN <= 30.0


def test_erkennung_liest_last_reported():
    # `last_changed`/`last_updated` bewegen sich nur bei einem NEUEN Wert. Ein
    # stehender SoC ändert sich nicht, ein lebendes Gerät meldet ihn trotzdem —
    # nur `last_reported` trennt „steht still" von „ist stumm".
    quelle = ast.unparse(_funktion("coordinator.py", "_abgemeldet"))
    assert "last_reported" in quelle


def test_fehlende_entitaet_gilt_nicht_als_abgemeldet():
    # Das ist ein Konfigurationsbefund (`config_check`), kein Ausfall — und
    # ohne Entität gibt es ohnehin keinen SoC, der die Rangfolge gewinnen kann.
    quelle = ast.unparse(_funktion("coordinator.py", "_abgemeldet"))
    assert "return False" in quelle


def test_coordinator_reicht_die_erkennung_in_den_planner():
    # Ohne diese Naht ist die ganze Prüfung wirkungslos: `stale` bliebe auf
    # seinem Default, und die Regelung sähe den Ausfall nie. Die Erkennung
    # selbst sitzt seit dem 17.08.2026 in `_stumm` — `_abgemeldet` ist nur
    # noch deren eine Hälfte (siehe test_speicher_selbstsperre.py).
    quelle = (BASIS / "coordinator.py").read_text(encoding="utf-8")
    assert "stale=self._stumm(s, offen)" in quelle
    assert "STORAGE_STALE_MIN" in ast.unparse(_funktion("coordinator.py", "_stumm"))


def test_sensor_und_log_zeigen_den_ausfall():
    assert "abgemeldet" in (BASIS / "sensor.py").read_text(encoding="utf-8")
    quelle = (BASIS / "changelog.py").read_text(encoding="utf-8")
    assert "akku_abgemeldet" in quelle
    # Der Snapshot allein genügt nicht — `diff_snapshots` läuft ausschließlich
    # über die Feldtabelle. Genau daran ging `akku_quittung` verloren: gebaut,
    # aber nie ausgegeben.
    assert {"akku_abgemeldet", "akku_quittung"} <= _feldtabelle()
