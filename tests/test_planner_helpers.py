"""Tests für die Ein-/Ausgabe-Aufbereitung in planner.py (aus coordinator.py
verschoben, um sie ohne laufendes Home Assistant testbar zu machen).
"""
from __future__ import annotations

from hems import planner as P


def test_parse_weekday_gueltig():
    assert P.parse_weekday("0") == 0
    assert P.parse_weekday(6) == 6
    assert P.parse_weekday("3") == 3


def test_parse_weekday_ungueltig():
    assert P.parse_weekday(None) is None
    assert P.parse_weekday("") is None
    assert P.parse_weekday("none") is None
    assert P.parse_weekday("7") is None  # außerhalb 0–6
    assert P.parse_weekday("abc") is None


def test_profile_rows_leer_ohne_profil():
    assert P.profile_rows(None) == []
    assert P.profile_rows({}) == []


def test_profile_rows_uebernimmt_die_lokale_stunde_unveraendert():
    # Das Profil ist seit 22.09.2026 nach Ortszeit gelernt — keine Umrechnung
    # mehr in der Anzeige.
    profile = {(0, 22): 300.0, (1, 22): 250.0}
    assert P.profile_rows(profile) == [
        {"stunde": 22, "werktag_w": 300.0, "wochenende_w": 250.0}
    ]


def test_profile_rows_sortiert_nach_stunde():
    profile = {(0, 23): 100.0, (0, 0): 50.0}
    assert [r["stunde"] for r in P.profile_rows(profile)] == [0, 23]
