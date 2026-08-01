"""Der Config-Check darf während des Starts nichts behaupten.

Am 01.08.2026 nach einem Neustart: einundzwanzig Meldungen „existiert nicht",
über Zendure-Speicher, Wärmepumpe und eine Steckdose verteilt. Keine davon
stimmte — die Integrationen hatten ihre Entitäten nur noch nicht registriert,
und HEMS' Prüfung war schneller. Wenige Sekunden später war der Sensor wieder
grün.

Das ist die teuerste Sorte Fehlalarm: Er sieht nach einer kaputten
Konfiguration aus, verschwindet von selbst, und wer ihn zweimal gesehen hat,
liest den Diagnose-Sensor nicht mehr.

`config_check.py` importiert Home Assistant (`CoreState`), deshalb wird die
Reihenfolge über den Syntaxbaum geprüft. Genau die ist der Schutz: Die Wache
muss vor allem anderen stehen. Eine Prüfung, die jemand später davorschiebt,
holt das Rennen zurück.
"""
from __future__ import annotations

import ast
from pathlib import Path

QUELLE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hems"
    / "config_check.py"
)


def _funktion(name: str) -> ast.FunctionDef:
    baum = ast.parse(QUELLE.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.FunctionDef) and knoten.name == name:
            return knoten
    raise AssertionError(f"{name} nicht in config_check.py gefunden")


def test_die_wache_steht_vor_jeder_pruefung() -> None:
    """Erste Anweisung in `check_config`, nicht irgendeine."""
    erste = _funktion("check_config").body[0]
    quelle = ast.unparse(erste)
    assert isinstance(erste, ast.If), f"erste Anweisung ist kein if: {quelle}"
    assert "pruefung_moeglich" in quelle, quelle
    assert "hass.state" in quelle, quelle
    assert "check_beim_start" in quelle, quelle


def test_vor_der_wache_wird_hass_nicht_gefragt() -> None:
    """Kein `hass.states`-Zugriff oberhalb der Wache.

    Formuliert als Zeilenvergleich, weil ein Zugriff darüber genau das Rennen
    zurückbrächte, das die Wache verhindert.
    """
    funktion = _funktion("check_config")
    wache = funktion.body[0].lineno
    zugriffe = [
        knoten.lineno
        for knoten in ast.walk(funktion)
        if isinstance(knoten, ast.Attribute) and knoten.attr == "states"
    ]
    assert not [z for z in zugriffe if z < wache], (
        f"hass.states vor der Wache in Zeile {wache}: {zugriffe}"
    )


def test_das_startergebnis_behauptet_nichts() -> None:
    """Keine Fehler, keine Warnungen, aber auch kein „geprüft"."""
    quelle = ast.unparse(_funktion("check_beim_start"))
    assert "geprueft=False" in quelle, quelle
    assert "scan_ok=False" in quelle, quelle
    # Weder errors noch warnings werden gesetzt: ein Startlauf soll weder
    # Alarm schlagen noch Entwarnung geben.
    assert "errors=" not in quelle, quelle
    assert "warnings=" not in quelle, quelle
    assert "info=" in quelle, quelle


def test_bereit_fuer_auto_haengt_an_geprueft() -> None:
    """Sonst läse sich ein ungeprüfter Zustand wie ein fehlerfreier.

    `errors` ist beim Start leer, weil nichts geprüft wurde — nicht, weil
    nichts zu finden war.
    """
    sensor = (QUELLE.parent / "binary_sensor.py").read_text(encoding="utf-8")
    baum = ast.parse(sensor)
    gefunden = [
        ast.unparse(wert)
        for knoten in ast.walk(baum)
        if isinstance(knoten, ast.Dict)
        for schluessel, wert in zip(knoten.keys, knoten.values)
        if isinstance(schluessel, ast.Constant) and schluessel.value == "bereit_fuer_auto"
    ]
    assert gefunden, "Attribut bereit_fuer_auto nicht gefunden"
    for ausdruck in gefunden:
        assert "geprueft" in ausdruck, ausdruck
