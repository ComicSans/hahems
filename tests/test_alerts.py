"""Tests der reinen Störungs-/Warn-Bewertung (HA-frei)."""
from __future__ import annotations

from hems.const import (
    ALERT_CHANNELS,
    ALERT_ERROR,
    ALERT_FAULT,
    ALERT_UNAVAILABLE,
    FAULT_DEBOUNCE_OFF,
    FAULT_DEBOUNCE_ON,
)
from hems.strategies import alerts as A


def _sig(raw, domain="binary_sensor"):
    return A.FaultSignal(
        role_id="wp1",
        role_name="Therma V",
        entity_id=f"{domain}.wp_fault",
        domain=domain,
        raw=raw,
    )


def _run(seq, domain="binary_sensor"):
    """Signalfolge durch den Latch schicken, Endzustand zurückgeben."""
    latch = A.FaultLatch()
    for raw in seq:
        latch = A.advance_latch(latch, _sig(raw, domain))
    return latch


# --- classify ---------------------------------------------------------------

def test_classify_binary_sensor():
    assert A.classify(_sig("on")) == A.FAULT
    assert A.classify(_sig("off")) == A.CLEAR
    assert A.classify(_sig("unavailable")) == A.UNKNOWN
    assert A.classify(_sig(None)) == A.UNKNOWN


def test_classify_sensor_fehlercode():
    # sensor: „ok"-Wert = kein Fehler, alles andere ist ein Fehlercode.
    assert A.classify(_sig("0", "sensor")) == A.CLEAR
    assert A.classify(_sig("OK", "sensor")) == A.CLEAR
    assert A.classify(_sig("CH07", "sensor")) == A.FAULT
    assert A.classify(_sig("unknown", "sensor")) == A.UNKNOWN


# --- Entprellung ------------------------------------------------------------

def test_latch_erst_nach_n_zyklen_an():
    # Ein einzelner Störimpuls darf nicht auslösen.
    assert _run(["on"]).active is False
    assert _run(["on"] * (FAULT_DEBOUNCE_ON - 1)).active is False
    assert _run(["on"] * FAULT_DEBOUNCE_ON).active is True


def test_latch_erst_nach_m_zyklen_wieder_aus():
    seq = ["on"] * FAULT_DEBOUNCE_ON + ["off"] * (FAULT_DEBOUNCE_OFF - 1)
    assert _run(seq).active is True  # noch nicht genug „off"
    assert _run(seq + ["off"]).active is False


def test_einzelner_aussetzer_kippt_nicht():
    # Dauerhafte Störung, ein „unavailable" dazwischen: bleibt an, kein Reset.
    seq = ["on"] * FAULT_DEBOUNCE_ON + ["unavailable", "on"]
    assert _run(seq).active is True


def test_alternierendes_signal_loest_trotzdem_aus():
    # on/unavailable im Wechsel (wacklige Modbus-Strecke, WP real gestört):
    # Der gehaltene Zähler muss die Schwelle trotz der Aussetzer erreichen —
    # sonst bliebe das System für immer stumm.
    seq = ["on", "unavailable"] * FAULT_DEBOUNCE_ON
    assert _run(seq).active is True


def test_unavailable_haelt_zustand_und_zaehlt():
    seq = ["unavailable"] * FAULT_DEBOUNCE_ON
    latch = _run(seq)
    assert latch.active is False  # unavailable ist keine Störung
    assert latch.unreachable_count >= FAULT_DEBOUNCE_ON


def test_fehlercode_wandert_in_meldung():
    latch = _run(["CH07"] * FAULT_DEBOUNCE_ON, domain="sensor")
    assert latch.active is True
    assert latch.last_code == "CH07"


# --- evaluate / Kandidatenmenge --------------------------------------------

def test_evaluate_liefert_kandidaten_mit_active_flag():
    # Störung noch nicht gelatcht → fault-Alert inaktiv, aber vorhanden.
    res = A.evaluate([_sig("on")], [], {})
    keys = {a.key: a for a in res.alerts}
    assert "wp_fault:wp1" in keys
    assert keys["wp_fault:wp1"].active is False
    assert keys["wp_fault:wp1"].severity == ALERT_FAULT
    assert res.latches["wp1"].on_count == 1


def test_evaluate_gelatchte_stoerung_ist_aktiv():
    # sensor-Domain: der Fehlercode aus dem Rohwert landet in der Meldung.
    latch = A.FaultLatch(active=True, last_code="CH07")
    res = A.evaluate([_sig("CH07", "sensor")], [], {"wp1": latch})
    fault = next(a for a in res.alerts if a.key == "wp_fault:wp1")
    assert fault.active is True
    assert "CH07" in fault.message


def test_unreachable_alert_erst_nach_entprellung():
    sig = _sig(None)
    res = A.evaluate([sig], [], {"wp1": A.FaultLatch(unreachable_count=1)})
    unreach = next(a for a in res.alerts if a.key == "wp_unreachable:wp1")
    assert unreach.active is False
    assert unreach.severity == ALERT_UNAVAILABLE


def test_stoerung_und_unerreichbar_nicht_gleichzeitig():
    # WP gelatcht gestört, dann Gateway weg: nur die Störung meldet, nicht
    # zusätzlich die Nichterreichbarkeit (kein Doppel-Lärm).
    latch = A.FaultLatch(active=True, unreachable_count=FAULT_DEBOUNCE_ON + 2)
    res = A.evaluate([_sig(None)], [], {"wp1": latch})
    fault = next(a for a in res.alerts if a.key == "wp_fault:wp1")
    unreach = next(a for a in res.alerts if a.key == "wp_unreachable:wp1")
    assert fault.active is True
    assert unreach.active is False


def test_config_fehler_aggregiert_zu_einem_alert():
    res = A.evaluate([], ["Fehler A", "Fehler B"], {})
    cfg = next(a for a in res.alerts if a.key == "config_error")
    assert cfg.active is True
    assert cfg.severity == ALERT_ERROR
    assert "Fehler A" in cfg.placeholders["fehler"]
    assert cfg.placeholders["anzahl"] == "2"


def test_config_fehler_leer_ist_inaktiv():
    res = A.evaluate([], [], {})
    cfg = next(a for a in res.alerts if a.key == "config_error")
    assert cfg.active is False


def test_severity_kanal_mapping():
    # Policy „nach Schweregrad": FAULT pusht, ERROR nur Repair, WARNING gar nichts.
    assert "notify" in ALERT_CHANNELS[ALERT_FAULT]
    assert "sensor" in ALERT_CHANNELS[ALERT_FAULT]
    assert ALERT_CHANNELS[ALERT_ERROR] == ("repair",)
    assert "notify" not in ALERT_CHANNELS.get("warning", ())
