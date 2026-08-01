"""Reine Warmwasser-Aktuierungsentscheidung (actuation.plan_ww_action).

Die Aktuierung selbst (actuator.py) importiert Home Assistant und ist in der
HA-freien Suite nicht erreichbar. Die Entscheidung, WELCHER Service mit welchem
Wert nötig ist, ist bewusst als pure Funktion herausgezogen und wird hier
festgenagelt — inklusive der beiden Gerätformen (water_heater trägt den Sollwert
selbst, ein Schalter braucht eine separate Number) und der Idempotenz.
"""
from __future__ import annotations

from hems.actuation import WwAction, plan_ww_action


def _plan(**kw):
    base = dict(
        status="basis",
        soll_c=48.0,
        domain="water_heater",
        state="on",
        schaltabstand_erreicht=True,
        current_setpoint=48.0,
        has_setpoint_entity=False,
    )
    base.update(kw)
    return plan_ww_action(**base)


def _act(**kw) -> WwAction | None:
    """Nur die Aktion — die Meldung prüfen die Tests am Ende der Datei."""
    return _plan(**kw).action


# --- Gerät nicht ansprechbar -------------------------------------------------


def test_unavailable_keine_aktion():
    assert _act(state="unavailable") is None


def test_unknown_keine_aktion():
    assert _act(state="unknown") is None


def test_state_none_keine_aktion():
    assert _act(state=None) is None


# --- Ausschalten -------------------------------------------------------------


def test_status_aus_schaltet_ab():
    assert _act(status="aus", state="on") == WwAction("turn_off")


def test_fehlender_sollwert_schaltet_ab():
    # soll_c None wird wie "aus" behandelt, unabhängig vom status.
    assert _act(status="basis", soll_c=None, state="on") == WwAction("turn_off")


def test_aus_respektiert_schaltabstand():
    # Mindestabstand zum letzten Wechsel noch nicht erreicht -> nicht schalten.
    assert _act(status="aus", state="on", schaltabstand_erreicht=False) is None


def test_aus_wenn_bereits_aus_keine_aktion():
    assert _act(status="aus", state="off") is None


# --- Einschalten -------------------------------------------------------------


def test_einschalten_bevor_sollwert():
    # Ausgeschaltet, aber Betrieb erwünscht: erst einschalten, Sollwert folgt
    # im nächsten Zyklus (auch wenn der Sollwert bereits abweicht).
    assert _act(state="off", current_setpoint=30.0) == WwAction("turn_on")


def test_einschalten_respektiert_schaltabstand():
    # Der Kern der Regel: Einschalten war früher ungebremst, ein Gerät konnte
    # also unmittelbar nach dem Abschalten wieder anlaufen. Jetzt gilt der
    # Abstand in beide Richtungen.
    assert _act(state="off", schaltabstand_erreicht=False) is None


def test_einschalten_nach_abgelaufenem_schaltabstand():
    assert _act(state="off", schaltabstand_erreicht=True) == WwAction("turn_on")


def test_sperre_stellt_keinen_sollwert_am_ausgeschalteten_geraet():
    # Vor Ablauf des Abstands passiert am ausgeschalteten Gerät gar nichts —
    # insbesondere fällt die Entscheidung nicht zum Sollwert-Angleichen durch,
    # das erst nach dem Einschalten an der Reihe ist.
    assert _act(state="off", current_setpoint=30.0, schaltabstand_erreicht=False) is None


def test_sollwert_haengt_nicht_am_schaltabstand():
    # Ein laufendes Gerät soll dem Überschuss weiter im Minutentakt folgen: die
    # Sperre bremst den Wechsel an/aus, nicht den Sollwert.
    assert _act(
        state="on", current_setpoint=40.0, schaltabstand_erreicht=False
    ) == WwAction("set_temperature", 48.0)


def test_rueckweg_einschalten_respektiert_schaltabstand():
    # Auch der Wiederhol-Schreibvorgang gegen ein Gerät, das den Befehl verloren
    # hat, ist ein Schaltvorgang — symmetrisch zum Abschalt-Zweig.
    p = _plan(state="on", last_written_on=False, schaltabstand_erreicht=False)
    assert p.action is None


def test_einschalten_switch_variante():
    assert _act(domain="switch", state="off", has_setpoint_entity=True) == WwAction(
        "turn_on"
    )


# --- Sollwert: water_heater --------------------------------------------------


def test_water_heater_sollwert_wird_gestellt():
    assert _act(current_setpoint=40.0) == WwAction("set_temperature", 48.0)


def test_water_heater_sollwert_passt_keine_aktion():
    assert _act(current_setpoint=48.0) is None


def test_water_heater_sollwert_unbekannt_wird_gestellt():
    assert _act(current_setpoint=None) == WwAction("set_temperature", 48.0)


def test_water_heater_vergleich_ganzzahlig():
    # Vergleich auf ganze °C (wie die abgelöste Automation): 48.9 gilt als 48.
    assert _act(current_setpoint=48.9, soll_c=48.0) is None


# --- Sollwert: Schalter + Number ---------------------------------------------


def test_switch_sollwert_ueber_number():
    assert _act(
        domain="switch", current_setpoint=40.0, has_setpoint_entity=True
    ) == WwAction("set_number", 48.0)


def test_switch_sollwert_passt_keine_aktion():
    assert (
        _act(domain="switch", current_setpoint=48.0, has_setpoint_entity=True) is None
    )


def test_switch_ohne_number_nur_schalten():
    # Kein Sollwert-Ziel konfiguriert: eingeschaltet lassen, nichts stellen.
    assert (
        _act(domain="switch", current_setpoint=None, has_setpoint_entity=False) is None
    )


def test_input_boolean_wie_switch():
    assert _act(
        domain="input_boolean", current_setpoint=40.0, has_setpoint_entity=True
    ) == WwAction("set_number", 48.0)


def test_turn_off_hat_keinen_wert():
    assert _act(status="aus", state="on").value is None


# --- Buchführung: hat das Gerät die Freigabe übernommen? ---------------------
#
# Gemessen am 01.08.2026 an einer LG Therma V über Modbus: HEMS schrieb die
# Warmwasser-Freigabe sechsmal ein (jeder on-Wechsel trägt im HA-Logbook den
# Kontext call_service switch.turn_on), und die Anlage fiel jedes Mal nach 4
# bis 30 Sekunden auf "aus" zurück. Kein einziger Aus-Befehl kam von HEMS.


def test_ohne_buchung_keine_meldung():
    # Nichts geschrieben (oder Frist läuft noch): verhält sich wie vorher.
    assert _plan(state="on", last_written_on=None).nicht_uebernommen is False


def test_geraet_zeigt_geschriebene_freigabe_nicht():
    p = _plan(state="off", last_written_on=True)
    assert p.nicht_uebernommen is True
    # Und HEMS schreibt weiter dagegen, statt aufzugeben.
    assert p.action == WwAction("turn_on")


def test_uebernommene_freigabe_meldet_nichts():
    assert _plan(state="on", last_written_on=True).nicht_uebernommen is False


def test_geraet_zeigt_geschriebenes_aus_nicht():
    p = _plan(status="aus", soll_c=None, state="on", last_written_on=False)
    assert p.nicht_uebernommen is True
    assert p.action == WwAction("turn_off")


def test_rueckweg_schreibt_obwohl_ist_schon_passt():
    # HEMS hat zuletzt "aus" geschrieben, das Gerät meldet weiter "on". Ziel ist
    # jetzt wieder "ein": Ist und Ziel sehen gleich aus, gestellt würde ohne den
    # Rückweg nie wieder.
    assert _plan(state="on", last_written_on=False).action == WwAction("turn_on")


def test_rueckweg_respektiert_schaltabstand():
    # Schalten bleibt an den Mindestabstand gebunden — er schützt die Hardware
    # und wiegt schwerer als die Buchführung.
    p = _plan(
        status="aus",
        soll_c=None,
        state="off",
        last_written_on=True,
        schaltabstand_erreicht=False,
    )
    assert p.action is None


def test_unavailable_meldet_nichts():
    # Ohne lesbaren Ist-Zustand ist keine Aussage möglich.
    assert _plan(state="unavailable", last_written_on=True).nicht_uebernommen is False
