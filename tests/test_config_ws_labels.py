"""Beschriftungen des Panel-Config-Editors.

Das Panel bekommt Labels und Hilfetexte aus den Übersetzungsdateien, gefunden
über den Schritt-Namen der Rolle. Rollen-Slug und Schritt-Name sind nicht
dasselbe (`heating_circuit` → `edit_heating`), und genau daran ist es schon
einmal auseinandergelaufen: das Panel zeigte rohe Schlüssel wie
``antitakt_starts`` ohne jede Erklärung.

Der Test kommt ohne Home Assistant aus — `config_ws` und `config_flow`
importieren es, deshalb wird das Mapping aus dem Quelltext gelesen.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

BASIS = Path(__file__).resolve().parents[1] / "custom_components" / "hems"
SPRACHEN = ("de", "en")


def _edit_steps() -> dict[str, str]:
    """``EDIT_STEPS`` aus config_flow.py lesen, ohne das Modul zu importieren."""
    baum = ast.parse((BASIS / "config_flow.py").read_text(encoding="utf-8"))
    for knoten in baum.body:
        if isinstance(knoten, ast.Assign) and any(
            isinstance(z, ast.Name) and z.id == "EDIT_STEPS" for z in knoten.targets
        ):
            werte = knoten.value
            assert isinstance(werte, ast.Dict)
            return {
                schluessel.id: ast.literal_eval(wert)
                for schluessel, wert in zip(werte.keys, werte.values)
                if isinstance(schluessel, ast.Name)
            }
    raise AssertionError("EDIT_STEPS nicht in config_flow.py gefunden")


def _schema_felder() -> dict[str, set[str]]:
    """``ROLE_SCHEMAS`` aus config_flow.py: je Rolle die Feldnamen.

    Ohne das prüft der Test nur, was schon beschriftet ist — ein neu
    hinzugefügtes Feld fällt lautlos durch, und im Panel steht dann sein roher
    Schlüssel. Genau so ist `curve_from_analysis` beim Bauen durchgerutscht.
    """
    quelle = (BASIS / "config_flow.py").read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    schemas: dict[str, ast.AST] = {}
    for knoten in baum.body:
        if isinstance(knoten, ast.Assign) and isinstance(knoten.targets[0], ast.Name):
            schemas[knoten.targets[0].id] = knoten.value

    zuordnung = schemas.get("ROLE_SCHEMAS")
    assert isinstance(zuordnung, ast.Dict), "ROLE_SCHEMAS nicht gefunden"

    felder: dict[str, set[str]] = {}
    for rolle, verweis in zip(zuordnung.keys, zuordnung.values):
        if not isinstance(rolle, ast.Name) or not isinstance(verweis, ast.Name):
            continue
        schema = schemas.get(verweis.id)
        if schema is None:
            continue
        namen: set[str] = set()
        for knoten in ast.walk(schema):
            # vol.Required("name") / vol.Optional(CONF_…): nur die Zeichen-
            # ketten; benannte Konstanten stehen in keinem Rollen-Schema.
            if isinstance(knoten, ast.Call) and knoten.args:
                erstes = knoten.args[0]
                if isinstance(erstes, ast.Constant) and isinstance(erstes.value, str):
                    namen.add(erstes.value)
        felder[rolle.id] = namen
    return felder


@pytest.mark.parametrize("sprache", SPRACHEN)
def test_jedes_formularfeld_hat_ein_label(sprache: str) -> None:
    uebersetzung = json.loads(
        (BASIS / "translations" / f"{sprache}.json").read_text(encoding="utf-8")
    )
    schritte = uebersetzung["options"]["step"]
    edit_steps = _edit_steps()
    felder = _schema_felder()
    assert felder, "keine Rollen-Schemas gefunden"
    for rolle, namen in felder.items():
        assert rolle in edit_steps, f"{rolle} fehlt in EDIT_STEPS"
        for schritt in _schritte(edit_steps[rolle]):
            ohne = namen - set(schritte[schritt]["data"])
            assert not ohne, f"{sprache}: {schritt} ohne Label: {sorted(ohne)}"


def _schritte(rolle_step: str) -> tuple[str, str]:
    """Dieselbe Ableitung wie ``config_ws._role_steps``."""
    return f"add_{rolle_step.removeprefix('edit_')}", rolle_step


@pytest.mark.parametrize("sprache", SPRACHEN)
def test_jede_rolle_hat_beschriftete_schritte(sprache: str) -> None:
    uebersetzung = json.loads(
        (BASIS / "translations" / f"{sprache}.json").read_text(encoding="utf-8")
    )
    schritte = uebersetzung["options"]["step"]
    for rolle, edit_step in _edit_steps().items():
        for name in _schritte(edit_step):
            assert name in schritte, f"{sprache}: Schritt {name} fehlt ({rolle})"
            block = schritte[name]
            assert block.get("data"), f"{sprache}: {name} ohne Labels"
            assert block.get(
                "data_description"
            ), f"{sprache}: {name} ohne Hilfetexte"


@pytest.mark.parametrize("sprache", SPRACHEN)
def test_grundeinstellungen_haben_labels_und_hilfetexte(sprache: str) -> None:
    """Die Grundeinstellungen hängen an keiner Rolle und fielen sonst durch."""
    uebersetzung = json.loads(
        (BASIS / "translations" / f"{sprache}.json").read_text(encoding="utf-8")
    )
    block = uebersetzung["options"]["step"].get("general", {})
    assert block.get("data"), f"{sprache}: Grundeinstellungen ohne Labels"
    ohne_hilfe = set(block["data"]) - set(block.get("data_description", {}))
    assert not ohne_hilfe, f"{sprache}: Grundeinstellungen ohne Hilfetext: {ohne_hilfe}"


@pytest.mark.parametrize("sprache", SPRACHEN)
def test_labels_und_hilfetexte_decken_dieselben_felder(sprache: str) -> None:
    """Ein Feld mit Label, aber ohne Hilfetext, fällt im Panel nicht auf —
    darum hier."""
    uebersetzung = json.loads(
        (BASIS / "translations" / f"{sprache}.json").read_text(encoding="utf-8")
    )
    schritte = uebersetzung["options"]["step"]
    for edit_step in _edit_steps().values():
        for name in _schritte(edit_step):
            block = schritte[name]
            ohne_hilfe = set(block["data"]) - set(block["data_description"])
            assert not ohne_hilfe, f"{sprache}: {name} ohne Hilfetext: {ohne_hilfe}"
