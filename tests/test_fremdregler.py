"""„Zendure-Manager deaktiviert?" — der zweite Regler auf denselben Geräten.

Die Überlappungsprüfung des Config-Checks kannte bis zum 10.09.2026 nur einen
Zweiten: eine aktive Automation, die auf eine HEMS-Steuer-Entität schreibt. Der
häufigere Zweite ist aber die Geräte-Integration selbst. Der Zendure-Manager
verteilt in jedem Modus außer `off` die Leistung eigenständig — er ruft
`device.charge()` bzw. `device.discharge()` und überschreibt damit genau die
Wege (`ac_mode`, `input_limit`, `output_limit`), über die HEMS stellt.

Von außen ist das nicht von einem defekten Speicher zu unterscheiden: Der
Auto-Modus schreibt, der Manager schreibt dagegen, und im Sensor steht ein
Gerät, das seine Zuteilung „ignoriert". In der Nacht vom 09. auf den 10.09.2026
kostete genau diese Unterscheidung zwanzig Minuten Suche (Aufgabe „Der Speicher
stand zwanzig Minuten still").

`fremdregler_aktiv` ist HA-frei und direkt testbar. Die Naht zum Config-Check
(Einordnung als Überlappung, Verzicht auf die Prüfung ohne gestellten Speicher)
steht in `config_check.py`, die Home Assistant importiert; sie wird deshalb über
den Syntaxbaum gelesen — dieselbe Bauart wie `test_config_check_start.py`.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hems.actuation import FREMDREGLER, fremdregler_aktiv

QUELLE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hems"
    / "config_check.py"
)

MANAGER = "select.zendure_manager_operation"


def _funktion(name: str) -> ast.FunctionDef:
    baum = ast.parse(QUELLE.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.FunctionDef) and knoten.name == name:
            return knoten
    raise AssertionError(f"{name} nicht in config_check.py gefunden")


# --- Die Erkennung ----------------------------------------------------------


def test_abgeschalteter_manager_ist_kein_befund():
    assert fremdregler_aktiv([(MANAGER, "off")]) == []


def test_jeder_andere_modus_ist_ein_befund():
    # Alle Modi der Integration außer `off` schreiben auf die Geräte —
    # `manual` genauso wie die drei `smart`-Varianten und `store_solar`.
    for modus in ("manual", "smart", "smart_discharging", "smart_charging",
                  "store_solar"):
        treffer = fremdregler_aktiv([(MANAGER, modus)])
        assert treffer == [("Zendure-Manager", MANAGER, modus, "off")], modus


def test_unbekannter_zustand_ist_kein_befund():
    # Dieselbe Zurückhaltung wie bei der Start-Wache: Ein Regler, dessen
    # Zustand nicht feststeht, ist kein Nachweis für einen aktiven Regler.
    for zustand in ("unknown", "unavailable", ""):
        assert fremdregler_aktiv([(MANAGER, zustand)]) == [], zustand


def test_fremde_selects_bleiben_unbehelligt():
    # Der Richtungs-Select eines Speichers steht auf „input" und darf nie als
    # Fremdregler gelten — sonst meldete der Config-Check den Normalbetrieb.
    # Die Liste ist der reale Bestand vom 10.09.2026, einmal mit dem
    # Gerätenamen „zendure" davor: Der Name des Geräts steht im Entity-Namen
    # und ist frei wählbar, deshalb darf „zendure" allein nicht reichen.
    andere = [
        ("select.hyper_2000_l2_ac_mode", "input"),
        ("select.hyper_2000_l2_fuse_group", "owncircuit"),
        ("select.hyper_2000_l2_connection", "local"),
        ("select.hyper_2000_l2_ble_adapter", "auto"),
        ("select.hyper_2000_l2_grid_reverse", "forbidden"),
        ("select.hyper_2000_l2_grid_off_mode", "normal"),
        ("select.hyper_2000_l2_auto_heat", "off"),
        ("select.zendure_hyper_2000_ac_mode", "input"),
        ("select.hems_modus", "auto"),
        ("select.waschmaschine_programm", "eco"),
    ]
    assert fremdregler_aktiv(andere) == []


def test_erkennung_ist_unabhaengig_von_der_gross_kleinschreibung():
    assert fremdregler_aktiv([("select.Zendure_Manager_Operation", "manual")])


def test_mehrere_regler_werden_alle_gemeldet():
    treffer = fremdregler_aktiv(
        [
            (MANAGER, "manual"),
            ("select.zendure_manager_2_operation", "smart"),
            ("select.hyper_2000_l3_ac_mode", "output"),
        ]
    )
    assert [t[1] for t in treffer] == [
        MANAGER,
        "select.zendure_manager_2_operation",
    ]


def test_jeder_eintrag_traegt_seinen_aus_zustand():
    # Die Meldung nennt den erwarteten Zustand; er kommt aus der Tabelle, nicht
    # aus einem fest verdrahteten „off" in der Textbaustelle.
    for teile, aus, name in FREMDREGLER:
        assert aus and name and teile


# --- Die Naht im Config-Check ----------------------------------------------


def test_scan_haengt_hinter_der_start_wache():
    # Der Fremdregler-Scan fragt `hass.states` — vor dem Ende des Starts wäre
    # jede Antwort wertlos, genau wie beim Rest der Prüfung.
    check = _funktion("check_config")
    quelle = ast.unparse(check)
    wache = quelle.index("pruefung_moeglich")
    scan = quelle.index("_scan_fremdregler")
    assert wache < scan


def test_ohne_gestellten_speicher_wird_nicht_gemeldet():
    # Beobachtet HEMS nur, darf der Manager regeln, so viel er will — dann ist
    # er kein Zweiter, sondern der Einzige.
    quelle = ast.unparse(_funktion("_scan_fremdregler"))
    assert "stellt_speicher" in quelle
    assert "if not stellt_speicher" in quelle
    for feld in ("charge_setpoint_entity", "discharge_setpoint_entity",
                 "mode_entity"):
        assert feld in quelle


def test_befund_ist_ueberlappung_und_kein_fehler():
    # `errors` würde den Auto-Modus hart blockieren. Die Erkennung ist eine
    # Namensheuristik — sie gehört in dieselbe Klasse wie die konkurrierende
    # Automation: nur im Auto-Modus ein Problem.
    quelle = ast.unparse(_funktion("_scan_fremdregler"))
    assert "c.overlaps.append" in quelle
    assert "c.errors" not in quelle
    assert "c.warnings.append" in quelle


def test_scan_reisst_den_sensor_nicht():
    # Wie der Automations-Scan: lieber „nicht geprüft" melden als den
    # Diagnose-Sensor mit einer Ausnahme aus dem Zyklus werfen.
    quelle = ast.unparse(_funktion("_scan_fremdregler"))
    assert "except Exception" in quelle
    assert "c.scan_ok = False" in quelle
