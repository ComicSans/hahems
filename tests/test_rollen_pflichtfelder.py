"""Pflicht im Formular und Pflicht im Modell müssen dasselbe heißen.

Ein Feld, das die Fachlogik entbehren kann, im Dialog aber als Pflicht steht,
sperrt jemanden aus, der es nicht liefern kann — und zwar ohne
Fehlermeldung, weil das Formular sich einfach nicht abschicken lässt. Genau so
ist einmal ein Messeingang zur Pflicht geworden, für den die Fachlogik längst
einen Rückfallwert hatte.

Umgekehrt genauso: ein Feld ohne Vorgabewert im Modell, das im Dialog optional
ist, lässt `parse_devices` beim Anlegen mit einem `TypeError` scheitern.

`config_flow.py` importiert Home Assistant, deshalb über den Syntaxbaum.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from hems import models

BASIS = Path(__file__).resolve().parents[1] / "custom_components" / "hems"

# Felder, die `parse_devices` selbst setzt und die deshalb in keinem Formular
# vorkommen.
INTERN = {"id"}


def _schema_felder() -> dict[str, dict[str, bool]]:
    """Je Rolle: Feldname → muss der Mensch wirklich etwas eintragen?

    `vol.Required(name, default=…)` ist vorbelegt und wird immer mitgeschickt —
    das ist die verbreitete Form im Repository und keine Hürde. Erzwungen ist
    nur `vol.Required(name)` **ohne** Vorgabe: da kommt niemand am Formular
    vorbei, der den Wert nicht liefern kann.
    """
    baum = ast.parse((BASIS / "config_flow.py").read_text(encoding="utf-8"))
    schemas = {
        knoten.targets[0].id: knoten.value
        for knoten in baum.body
        if isinstance(knoten, ast.Assign) and isinstance(knoten.targets[0], ast.Name)
    }
    zuordnung = schemas.get("ROLE_SCHEMAS")
    assert isinstance(zuordnung, ast.Dict), "ROLE_SCHEMAS nicht gefunden"

    felder: dict[str, dict[str, bool]] = {}
    for rolle, verweis in zip(zuordnung.keys, zuordnung.values):
        if not isinstance(rolle, ast.Name) or not isinstance(verweis, ast.Name):
            continue
        schema = schemas.get(verweis.id)
        if schema is None:
            continue
        eintraege: dict[str, bool] = {}
        for knoten in ast.walk(schema):
            if not (isinstance(knoten, ast.Call) and knoten.args):
                continue
            markierung = ast.unparse(knoten.func)
            if markierung not in ("vol.Required", "vol.Optional"):
                continue
            name = knoten.args[0]
            hat_vorgabe = any(kw.arg == "default" for kw in knoten.keywords)
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                eintraege[name.value] = (
                    markierung == "vol.Required" and not hat_vorgabe
                )
        felder[rolle.id] = eintraege
    return felder


def _rollen_klassen() -> dict[str, type]:
    """Name der Rollen-Konstante → Datenklasse.

    Über den Namen und nicht den Wert, weil der Syntaxbaum von `config_flow.py`
    nur Namen sieht.
    """
    from hems import const

    nach_wert = {
        wert: name
        for name, wert in vars(const).items()
        if name.startswith("ROLE_") and isinstance(wert, str)
    }
    return {
        nach_wert[rolle]: klasse
        for rolle, (klasse, _attr) in models._ROLE_CLASSES.items()
        if rolle in nach_wert
    }


FELDER = _schema_felder()
KLASSEN = _rollen_klassen()


@pytest.mark.parametrize("rolle", sorted(FELDER))
def test_kein_feld_wird_erzwungen_das_die_logik_entbehren_kann(rolle: str) -> None:
    """Was das Formular erzwingt, muss die Fachlogik auch wirklich brauchen."""
    klasse = KLASSEN[rolle]
    aus_modell = {
        name: _ohne_vorgabe(feld)
        for name, feld in klasse.__dataclass_fields__.items()
        if name not in INTERN
    }
    for name, erzwungen in FELDER[rolle].items():
        assert name in aus_modell, f"{rolle}: {name} gibt es im Modell nicht"
        if erzwungen and not aus_modell[name]:
            raise AssertionError(
                f"{rolle}.{name}: das Formular erzwingt eine Eingabe, obwohl "
                "das Modell einen Vorgabewert hat. Wer den Wert nicht liefern "
                "kann, kommt am Dialog nicht vorbei."
            )


@pytest.mark.parametrize("rolle", sorted(FELDER))
def test_jedes_pflichtfeld_des_modells_steht_im_formular(rolle: str) -> None:
    """Sonst scheitert `parse_devices` beim Anlegen mit einem TypeError."""
    klasse = KLASSEN[rolle]
    pflicht = {
        name
        for name, feld in klasse.__dataclass_fields__.items()
        if name not in INTERN and _ohne_vorgabe(feld)
    }
    assert not pflicht - set(FELDER[rolle]), (
        f"{rolle}: ohne Vorgabewert und nicht im Formular: "
        f"{sorted(pflicht - set(FELDER[rolle]))}"
    )


def _ohne_vorgabe(feld) -> bool:
    """Pflichtfeld im Sinne der Datenklasse: weder Default noch Factory."""
    import dataclasses

    return (
        feld.default is dataclasses.MISSING
        and feld.default_factory is dataclasses.MISSING
    )
