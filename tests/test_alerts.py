"""Tests der reinen Warn-Bewertung (HA-frei)."""
from __future__ import annotations

from hems.const import ALERT_CHANNELS, ALERT_ERROR
from hems.strategies import alerts as A


def test_config_fehler_aggregiert_zu_einem_alert():
    res = A.evaluate(["Fehler A", "Fehler B"])
    cfg = next(a for a in res.alerts if a.key == "config_error")
    assert cfg.active is True
    assert cfg.severity == ALERT_ERROR
    assert "Fehler A" in cfg.placeholders["fehler"]
    assert cfg.placeholders["anzahl"] == "2"


def test_config_fehler_leer_ist_inaktiv():
    res = A.evaluate([])
    cfg = next(a for a in res.alerts if a.key == "config_error")
    assert cfg.active is False


def test_severity_kanal_mapping():
    # ERROR wird ein Repair-Issue; WARNING bleibt Sensor + Log, ohne Kanal.
    assert ALERT_CHANNELS[ALERT_ERROR] == ("repair",)
    assert "notify" not in ALERT_CHANNELS.get("warning", ())
