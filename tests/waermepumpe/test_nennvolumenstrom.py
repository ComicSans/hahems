"""Ohne Zähler und ohne Nennwert rechnet die Analyse gar nichts.

`durchfluss_effektiv` fällt auf den Nennvolumenstrom des Presets zurück, wenn
kein Zähler verdrahtet ist. Nur: **die sechs generischen Presets führen keinen**
— er hängt an Umwälzpumpe und Hydraulik, nicht am Gerätemodell, und für ein
generisches Profil gibt es ihn nicht.

Damit traf der dokumentierte Rückfall ausgerechnet die Leute nicht, die ihn
brauchen: Wer ein generisches Preset wählt, hat kein passendes Gerätemodell in
der Liste — und hat besonders oft auch keinen Volumenstromzähler. Die Analyse
verwarf dort jede Messung mit `kein_durchfluss`, dauerhaft und ohne dass die
Datenbasis oder ein Hinweis darauf hindeutete.

Deshalb gibt es `durchfluss_nominal_lh` an der Rolle. Diese Tests halten die
Kette zusammen: die Tatsache über die Presets, den Rückfall in der Fachlogik
und die Überschreibung im Auswertelauf.
"""
from __future__ import annotations

import ast
import json

from conftest import PAKET, PRESET_DIR
from hems.models import HeatPumpAnalysis
from hems.waermepumpe.analysis import presets, thermal
from hems.waermepumpe.analysis.types import BETRIEB_HEIZEN, Messwert


def _messwert(**kw) -> Messwert:
    grund = {
        "ts": 0.0,
        "vorlauf_c": 35.0,
        "ruecklauf_c": 30.0,
        "p_el_w": 1400.0,
        "t_aussen_c": 5.0,
        "betrieb": BETRIEB_HEIZEN,
    }
    grund.update(kw)
    return Messwert(**grund)


def test_generische_presets_fuehren_keinen_nennvolumenstrom() -> None:
    """Die Tatsache, aus der alles Weitere folgt.

    Bekommt ein generisches Preset eines Tages doch einen, ist dieser Test die
    Stelle, an der das auffällt — dann gehört die Begründung überprüft, nicht
    der Test angepasst.
    """
    ohne: list[str] = []
    for datei in sorted(PRESET_DIR.glob("*.json")):
        roh = json.loads(datei.read_text(encoding="utf-8"))
        if not roh.get("durchfluss_nominal_lh"):
            ohne.append(roh["schluessel"])
    assert ohne == [
        "generisch-luft-wasser-einfach",
        "generisch-luft-wasser-gut",
        "generisch-luft-wasser-mittel",
        "generisch-sole-wasser-einfach",
        "generisch-sole-wasser-gut",
        "generisch-sole-wasser-mittel",
    ], ohne


def test_ohne_zaehler_und_ohne_nennwert_gibt_es_keinen_volumenstrom() -> None:
    alle = presets.lade_presets(PRESET_DIR)
    generisch = alle["generisch-luft-wasser-mittel"]
    fluss, geschaetzt = thermal.durchfluss_effektiv(_messwert(), generisch)
    assert fluss is None
    # Und `geschaetzt` ist falsch, nicht wahr: geschätzt wurde ja nichts.
    assert geschaetzt is False


def test_mit_nennwert_wird_geschaetzt_statt_verworfen() -> None:
    from dataclasses import replace

    alle = presets.lade_presets(PRESET_DIR)
    mit_nennwert = replace(
        alle["generisch-luft-wasser-mittel"], durchfluss_nominal_lh=900.0
    )
    fluss, geschaetzt = thermal.durchfluss_effektiv(_messwert(), mit_nennwert)
    assert fluss == 900.0
    assert geschaetzt is True


def test_ein_gemessener_wert_schlaegt_den_nennwert() -> None:
    from dataclasses import replace

    alle = presets.lade_presets(PRESET_DIR)
    mit_nennwert = replace(
        alle["generisch-luft-wasser-mittel"], durchfluss_nominal_lh=900.0
    )
    fluss, geschaetzt = thermal.durchfluss_effektiv(
        _messwert(durchfluss_lh=1150.0), mit_nennwert
    )
    assert fluss == 1150.0
    assert geschaetzt is False


def test_die_rolle_kann_den_nennwert_setzen() -> None:
    feld = HeatPumpAnalysis.__dataclass_fields__["durchfluss_nominal_lh"]
    assert feld.default == 0.0, "0 muss heißen: Wert aus dem Preset nehmen"


def test_der_auswertelauf_wendet_den_nennwert_auch_an() -> None:
    """Ein Feld, das niemand liest, ist schlimmer als keines.

    `runner.py` importiert Home Assistant, deshalb über den Syntaxbaum. Geprüft
    wird, dass die Überschreibung an `replace(...)` geht — genau wie beim
    Standby-Sockel daneben.
    """
    baum = ast.parse((PAKET / "runner.py").read_text(encoding="utf-8"))
    ueberschrieben = {
        kw.arg
        for knoten in ast.walk(baum)
        if isinstance(knoten, ast.Call)
        and isinstance(knoten.func, ast.Name)
        and knoten.func.id == "replace"
        for kw in knoten.keywords
    }
    assert "durchfluss_nominal_lh" in ueberschrieben, ueberschrieben
    assert "standby_w" in ueberschrieben, ueberschrieben
