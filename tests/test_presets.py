"""Ausgelieferte Presets und die Erwartungskennlinie."""
from __future__ import annotations

import json

import pytest
from analysis import presets
from conftest import PRESET_DIR


def test_alle_ausgelieferten_presets_laden():
    geladen = presets.lade_presets(PRESET_DIR)
    # Vier LG-Varianten plus sechs generische Typen.
    assert len(geladen) == 10
    assert "lg-therma-v-r32-split-5-7-9" in geladen


def test_lg_ist_modellscharf_nicht_markenscharf():
    # Die Therma-V-Reihe hat vier verschiedene Kennlinien. Ein Preset je
    # Marke waere fuer drei davon schlicht falsch.
    geladen = presets.lade_presets(PRESET_DIR)
    lg = [s for s in geladen if s.startswith("lg-therma-v-")]
    assert len(lg) == 4
    polynome = {
        (geladen[s].p1, geladen[s].p2, geladen[s].p3, geladen[s].p4) for s in lg
    }
    assert len(polynome) == 4


def test_erwarteter_cop_faellt_mit_steigendem_vorlauf():
    p = presets.lade_presets(PRESET_DIR)["lg-therma-v-r32-split-5-7-9"]
    warm = presets.erwarteter_cop(p, 7.0, 30.0)
    heiss = presets.erwarteter_cop(p, 7.0, 50.0)
    assert warm > heiss


def test_erwarteter_cop_steigt_mit_der_aussentemperatur():
    p = presets.lade_presets(PRESET_DIR)["lg-therma-v-r32-split-5-7-9"]
    assert presets.erwarteter_cop(p, 12.0, 35.0) > presets.erwarteter_cop(p, -7.0, 35.0)


def test_erwartung_bleibt_im_plausiblen_bereich():
    # Weit ausserhalb des Kennfelds extrapoliert das Polynom ins Absurde.
    p = presets.lade_presets(PRESET_DIR)["lg-therma-v-r32-split-5-7-9"]
    for t_aussen, t_vorlauf in ((-40.0, 70.0), (40.0, 20.0)):
        wert = presets.erwarteter_cop(p, t_aussen, t_vorlauf)
        assert presets.COP_UNTERGRENZE <= wert <= presets.COP_OBERGRENZE


def test_ausserhalb_kennfeld_wird_erkannt():
    p = presets.lade_presets(PRESET_DIR)["lg-therma-v-r32-split-5-7-9"]
    assert presets.ausserhalb_kennfeld(p, -35.0)
    assert not presets.ausserhalb_kennfeld(p, 5.0)
    assert presets.ausserhalb_kennfeld(p, None)


def test_generische_presets_sind_als_solche_markiert():
    geladen = presets.lade_presets(PRESET_DIR)
    generisch = [p for p in geladen.values() if p.generisch]
    assert len(generisch) == 6
    # Ein generisches Profil darf keine Datenblattgenauigkeit vortaeuschen.
    assert all(p.modellfehler_prozent >= 20.0 for p in generisch)


def test_unvollstaendiges_preset_wird_abgelehnt():
    with pytest.raises(ValueError):
        presets.aus_dict({"schluessel": "x", "anzeigename": "X", "quelle": "test"})


def test_defektes_preset_reisst_die_uebrigen_nicht_mit(tmp_path):
    (tmp_path / "kaputt.json").write_text("{ kein json", encoding="utf-8")
    (tmp_path / "gut.json").write_text(
        json.dumps(
            {
                "schluessel": "gut",
                "anzeigename": "Gut",
                "quelle": "test",
                "cop_polynom": {"p1": 0.0, "p2": -0.1, "p3": 8.0, "p4": 0.19},
            }
        ),
        encoding="utf-8",
    )
    geladen = presets.lade_presets(tmp_path)
    assert list(geladen) == ["gut"]
