"""Die Sommersperre darf keine Halbkugel unterstellen.

Die Vorgaben Mai bis September sind die Nordhalbkugel. Auf der Südhalbkugel
sind das genau die Heizmonate: HEMS empföhle dort im Winter nie Heizen, und
zwar ohne jede Fehlermeldung — der Heizkreis stünde einfach auf „aus", und die
Sommersperre in der Anzeige sähe nach einer korrekten Begründung aus.

`config_flow.py` importiert Home Assistant, deshalb hier über den Syntaxbaum
und über die reine Rechenfunktion.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hems.const import DEFAULT_HEAT_LOCK_FROM, DEFAULT_HEAT_LOCK_TO

QUELLE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hems"
    / "config_flow.py"
)


def _halbjahr_versetzt():
    """Die Funktion aus config_flow.py laden, ohne das Modul zu importieren."""
    baum = ast.parse(QUELLE.read_text(encoding="utf-8"))
    for knoten in baum.body:
        if isinstance(knoten, ast.FunctionDef) and knoten.name == "_halbjahr_versetzt":
            raum: dict = {}
            exec(compile(ast.Module([knoten], []), "<kurve>", "exec"), raum)
            return raum["_halbjahr_versetzt"]
    raise AssertionError("_halbjahr_versetzt nicht in config_flow.py gefunden")


def test_der_versatz_trifft_die_gegenueberliegende_jahreszeit():
    versetzt = _halbjahr_versetzt()
    # Mai bis September wird November bis März: dieselbe Jahreszeit, andere
    # Halbkugel. Der Umlauf über den Jahreswechsel ist dabei der Normalfall
    # und kein Sonderfall.
    assert versetzt(DEFAULT_HEAT_LOCK_FROM) == 11
    assert versetzt(DEFAULT_HEAT_LOCK_TO) == 3


def test_jeder_monat_bleibt_ein_monat():
    versetzt = _halbjahr_versetzt()
    ergebnisse = [versetzt(m) for m in range(1, 13)]
    assert sorted(ergebnisse) == list(range(1, 13))


def test_zweimal_versetzt_ist_wieder_derselbe_monat():
    versetzt = _halbjahr_versetzt()
    assert [versetzt(versetzt(m)) for m in range(1, 13)] == list(range(1, 13))


def test_die_sperre_selbst_kann_den_jahreswechsel_ueberspannen():
    """Die Vorbelegung wäre wertlos, wenn die Regel sie nicht trüge.

    Dieselbe Rechnung wie in `coordinator.py`: liegt der Startmonat hinter dem
    Endmonat, läuft das Fenster über den Jahreswechsel. Ohne diesen Zweig
    ergäbe eine Sperre von November bis März nie ein wahres Ergebnis.
    """

    def gesperrt(monat: int, lo: int, hi: int) -> bool:
        return lo <= monat <= hi if lo <= hi else (monat >= lo or monat <= hi)

    sued = [m for m in range(1, 13) if gesperrt(m, 11, 3)]
    assert sued == [1, 2, 3, 11, 12]
    nord = [m for m in range(1, 13) if gesperrt(m, 5, 9)]
    assert nord == [5, 6, 7, 8, 9]
    # Beide sperren fünf Monate, um ein halbes Jahr versetzt.
    versetzt = _halbjahr_versetzt()
    assert sorted(versetzt(m) for m in nord) == sued
