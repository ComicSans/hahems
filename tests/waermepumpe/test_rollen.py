"""Rollennamen im Code gegen die Schnittstellenbeschreibung.

Genau hier ist es schon einmal auseinandergelaufen. Solange die Analyse ein
eigenes Repository war, verband ein versioniertes Dokument sie mit HEMS — und
niemand prüfte es: Produzent und Konsument lieferten beide `takte`, das
Dokument forderte `takte_periode`, drei Rollen standen im Code und in keinem
Dokument, eine im Dokument und in keinem Code. Beide Seiten meldeten
Kontraktversion 1.

Die Rollennamen bilden die Kennung in der Entity-Registry. Eine umbenannte
Rolle verwaist die alte Entity und legt eine neue an; Automationen und
Dashboards zeigen danach ins Leere, ohne Fehlermeldung. Ein Dokument allein
verhindert das nicht, dieser Test schon.

`entities.py` importiert Home Assistant, deshalb wird es wie in
`test_config_ws_labels.py` über den Syntaxbaum gelesen statt importiert.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from conftest import PAKET
from hems.models import HeatPumpAnalysis

DOKUMENT = Path(__file__).resolve().parents[2] / "docs" / "waermepumpen-analyse.md"


def _beschreibungen() -> dict[str, dict[str, str]]:
    """Je Rollenname die gesetzten Schlüsselwortargumente als Text.

    Über den Syntaxbaum und nicht per Textsuche: sonst schlägt schon eine
    Erwähnung im Docstring an.
    """
    baum = ast.parse((PAKET / "entities.py").read_text(encoding="utf-8"))
    gefunden: dict[str, dict[str, str]] = {}
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        argumente = {
            kw.arg: kw.value for kw in knoten.keywords if kw.arg is not None
        }
        schluessel = argumente.get("key")
        if not isinstance(schluessel, ast.Constant):
            continue
        gefunden[schluessel.value] = {
            name: ast.unparse(wert) for name, wert in argumente.items()
        }
    return gefunden


def _energie_rolle() -> str:
    baum = ast.parse((PAKET / "entities.py").read_text(encoding="utf-8"))
    for knoten in baum.body:
        if isinstance(knoten, ast.Assign) and any(
            isinstance(z, ast.Name) and z.id == "ENERGIE_KEY" for z in knoten.targets
        ):
            return ast.literal_eval(knoten.value)
    raise AssertionError("ENERGIE_KEY nicht in entities.py gefunden")


def _veroeffentlichte_rollen() -> set[str]:
    return set(_beschreibungen()) | {_energie_rolle()}


def _dokumentierte_rollen() -> set[str]:
    """Rollennamen aus der Beschreibung: Tabellenzeilen und Aufzählungen."""
    text = DOKUMENT.read_text(encoding="utf-8")
    namen = set(re.findall(r"^\|\s*`(\w+)`\s*\|", text, re.M))
    namen |= set(re.findall(r"^-\s+`(\w+)`(\s+—|$)", text, re.M))
    return {n if isinstance(n, str) else n[0] for n in namen}


def test_jede_veroeffentlichte_rolle_ist_beschrieben() -> None:
    fehlend = _veroeffentlichte_rollen() - _dokumentierte_rollen()
    assert not fehlend, f"veröffentlicht, aber nicht beschrieben: {sorted(fehlend)}"


def test_keine_beschriebene_ausgaberolle_fehlt_im_code() -> None:
    # Die Messeingänge stehen in derselben Beschreibung, sind aber
    # Konfigurationsfelder und keine Ausgaberollen — sie werden abgezogen.
    eingaenge = set(HeatPumpAnalysis.__dataclass_fields__)
    fehlend = _dokumentierte_rollen() - _veroeffentlichte_rollen() - eingaenge
    assert not fehlend, f"beschrieben, aber nicht veröffentlicht: {sorted(fehlend)}"


def test_zaehler_sind_total_increasing() -> None:
    """Das Stundenmittel einer Startzahl ist bedeutungslos.

    Aussagen über einen Zeitraum entstehen aus der Differenz zweier
    Zählerstände. Stünde hier `measurement`, wäre der Langzeitverlauf hinter
    `hinweis_taktung_hoch` still falsch — ohne dass irgendetwas auffiele.
    """
    beschreibungen = _beschreibungen()
    for rolle in ("takte", "laufzeit_summe"):
        klasse = beschreibungen[rolle].get("state_class")
        assert klasse == "SensorStateClass.TOTAL_INCREASING", f"{rolle}: {klasse}"


def test_jede_rolle_kommt_genau_einmal_vor() -> None:
    """Sensor und Binärsensor bilden dieselbe Kennung.

    Ein doppelter Name gäbe zwei Entities mit derselben `unique_id`; Home
    Assistant legte die zweite nicht an, und welche fehlt, hinge an der
    Reihenfolge.
    """
    quelle = (PAKET / "entities.py").read_text(encoding="utf-8")
    namen = re.findall(r'^\s+key="(\w+)",$', quelle, re.M)
    doppelt = {n for n in namen if namen.count(n) > 1}
    assert not doppelt, f"mehrfach vergeben: {sorted(doppelt)}"
    assert _energie_rolle() not in namen
