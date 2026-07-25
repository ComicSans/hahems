"""Tests für die Ein-/Ausgabe-Aufbereitung in planner.py (aus coordinator.py
verschoben, um sie ohne laufendes Home Assistant testbar zu machen — siehe
docs/architektur-review.md).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hems import planner as P

UTC = timezone.utc
# UTC+2 (z. B. deutsche Sommerzeit), fixer Offset genügt für den Stundenversatz.
TZ_PLUS2 = timezone(timedelta(hours=2))


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
    assert P.profile_rows(None, datetime(2026, 7, 22, 12, tzinfo=UTC), UTC) == []
    assert P.profile_rows({}, datetime(2026, 7, 22, 12, tzinfo=UTC), UTC) == []


def test_profile_rows_rechnet_utc_stunde_in_lokale_stunde_um():
    profile = {(0, 22): 300.0, (1, 22): 250.0}
    rows = P.profile_rows(
        profile, datetime(2026, 7, 22, 12, tzinfo=UTC), TZ_PLUS2
    )
    assert rows == [{"stunde": 0, "werktag_w": 300.0, "wochenende_w": 250.0}]


def test_profile_rows_sortiert_nach_lokaler_stunde():
    profile = {(0, 23): 100.0, (0, 0): 50.0}
    rows = P.profile_rows(profile, datetime(2026, 7, 22, 12, tzinfo=UTC), UTC)
    assert [r["stunde"] for r in rows] == [0, 23]
