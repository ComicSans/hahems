"""Die Drossel in `_call` verwirft nur echte Wiederholungen.

Bis 22.09.2026 merkte sie sich jeden jemals geschriebenen Wert einzeln — ein
Wert, der nach einem anderen ZURÜCKKEHRTE, galt als Wiederholung. Gemessen mit
einem nachgestellten Actuator: Sollwert 1200 → 900 → 1200 im Minutentakt blieb
auf 900, und `release_battery` nach laden → entladen → laden ließ den Speicher
mit 800 W weiterladen. Die Entscheidung liegt HA-frei in `drossel_verwirft`;
die Naht zum Actuator wird über den Syntaxbaum gelesen.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hems.actuation import drossel_verwirft

BASIS = Path(__file__).resolve().parents[1] / "custom_components" / "hems"
T0 = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
FRIST = timedelta(minutes=5)


def _funktion(name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    baum = ast.parse((BASIS / "actuator.py").read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            knoten.name == name
        ):
            return knoten
    raise AssertionError(name)


def test_erster_aufruf_geht_durch():
    assert drossel_verwirft(None, (("value", 1200),), T0, FRIST) is False


def test_identische_wiederholung_in_der_frist_wird_verworfen():
    vorher = ((("value", 1200),), T0)
    assert drossel_verwirft(vorher, (("value", 1200),), T0 + timedelta(minutes=2), FRIST)


def test_identische_wiederholung_nach_der_frist_geht_durch():
    vorher = ((("value", 1200),), T0)
    assert not drossel_verwirft(
        vorher, (("value", 1200),), T0 + timedelta(minutes=5), FRIST
    )


def test_rueckkehr_auf_einen_frueheren_wert_ist_ein_neuer_befehl():
    # 1200 → 900 → 1200: Verglichen wird nur mit dem unmittelbar vorigen (900).
    vorher = ((("value", 900),), T0 + timedelta(minutes=1))
    assert not drossel_verwirft(
        vorher, (("value", 1200),), T0 + timedelta(minutes=2), FRIST
    )


def test_actuator_merkt_sich_je_ziel_nur_den_letzten_aufruf():
    quelle = ast.unparse(_funktion("_call"))
    assert "key = (domain, service, entity)" in quelle
    assert "drossel_verwirft(" in quelle
    assert "self._last_call[key] = (daten, now)" in quelle


def test_release_battery_umgeht_die_drossel():
    # Die Freigabe läuft genau einmal — ein verworfener Aufruf bekäme keinen
    # zweiten Versuch.
    quelle = ast.unparse(_funktion("release_battery"))
    assert quelle.count("ohne_drossel=True") == 2


def test_servicefehler_werden_sichtbar_und_geben_die_drossel_frei():
    # `blocking=False` verschluckte jeden Ausführungsfehler; jetzt wartet ein
    # Hintergrund-Task auf das Ergebnis und meldet ihn.
    quelle = ast.unparse(_funktion("_ausfuehren"))
    assert "blocking=True" in quelle
    assert "warning" in quelle
    assert "self._last_call.pop(key" in quelle
    assert "blocking=False" not in ast.unparse(_funktion("_call"))
