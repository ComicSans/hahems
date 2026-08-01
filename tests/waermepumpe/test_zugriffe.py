"""Die Zugriffe in `entities.py` gegen die echten Analysetypen.

Jede Entity liest ihren Wert über ein `wert=lambda a: a.…`. Diese Lambdas
laufen erst zur Laufzeit in Home Assistant — ein Tippfehler oder ein Feld, das
in `analysis/types.py` umbenannt wurde, fällt hier in der Testsuite nirgends
auf und in Home Assistant als leere Entity ohne Fehlermeldung.

Das ist der teure Fall: `analysis/` ist die Fachlogik und wird umbenannt, wenn
eine Rechnung genauer wird; `entities.py` importiert Home Assistant und kann
deshalb nicht mitgetestet werden. Genau zwischen diesen beiden Dateien läuft
sonst nichts zusammen.

Deshalb: die Lambdas aus dem Syntaxbaum lesen, die Attributketten gegen eine
echte `Analyse` mit Vorgabewerten laufen lassen. Ein fehlendes Feld wirft.
"""
from __future__ import annotations

import ast

import pytest
from conftest import PAKET
from hems.waermepumpe.analysis.types import Analyse

WURZEL = "a"


def _ketten() -> list[tuple[str, list[str]]]:
    """Je Rolle die Attributketten ihres `wert`-Lambdas.

    `round(a.takt.laufzeit_s / 3600.0, 3)` liefert `("laufzeit_summe",
    ["takt", "laufzeit_s"])` — es wird der ganze Ausdruck durchsucht, nicht
    nur ein direkter Attributzugriff.
    """
    baum = ast.parse((PAKET / "entities.py").read_text(encoding="utf-8"))
    gefunden: list[tuple[str, list[str]]] = []
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        argumente = {kw.arg: kw.value for kw in knoten.keywords if kw.arg}
        schluessel, wert = argumente.get("key"), argumente.get("wert")
        if not isinstance(schluessel, ast.Constant) or wert is None:
            continue
        for kette in _attributketten(wert):
            gefunden.append((schluessel.value, kette))
    return gefunden


def _attributketten(ausdruck: ast.AST) -> list[list[str]]:
    """Alle Ketten der Form `a.x.y` als `["x", "y"]`.

    Nur vollständige Ketten: bei `a.takt.starts` liefert `ast.walk` auch das
    Teilstück `a.takt`, das hier nicht noch einmal gebraucht wird.
    """
    ketten: list[list[str]] = []
    for knoten in ast.walk(ausdruck):
        if not isinstance(knoten, ast.Attribute):
            continue
        teile: list[str] = []
        laufend: ast.AST = knoten
        while isinstance(laufend, ast.Attribute):
            teile.insert(0, laufend.attr)
            laufend = laufend.value
        if isinstance(laufend, ast.Name) and laufend.id == WURZEL:
            ketten.append(teile)
    # Teilketten entfernen: ["takt"] ist in ["takt", "starts"] enthalten.
    return [k for k in ketten if not any(a[: len(k)] == k and a != k for a in ketten)]


@pytest.mark.parametrize("rolle,kette", _ketten(), ids=lambda w: str(w))
def test_zugriff_trifft_ein_feld(rolle: str, kette: list[str]) -> None:
    objekt = Analyse()
    pfad = WURZEL
    for teil in kette:
        pfad = f"{pfad}.{teil}"
        assert hasattr(objekt, teil), f"{rolle}: {pfad} gibt es nicht"
        objekt = getattr(objekt, teil)


def test_es_gibt_ueberhaupt_zugriffe_zu_pruefen() -> None:
    """Wächter gegen den stillen Ausfall dieses Tests.

    Ändert sich der Aufbau von `entities.py` so, dass `_ketten()` nichts mehr
    findet, liefe die Parametrisierung leer durch und die Suite bliebe grün —
    ohne dass irgendetwas geprüft wäre.
    """
    ketten = _ketten()
    assert len(ketten) >= 25
    assert {r for r, _ in ketten} >= {"cop_momentan", "takte", "empfehlung_fusspunkt"}
