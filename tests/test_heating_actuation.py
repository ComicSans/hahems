"""Reine Heizkreis-Aktuierungsentscheidung (actuation.plan_heating_control).

Wie bei Warmwasser ist die Entscheidung — welcher Modus, welcher Vorlauf-Soll,
und ob überhaupt etwas zu stellen ist — HA-frei herausgezogen, damit sie ohne
Home Assistant testbar bleibt. Der Actuator übersetzt das Ergebnis dann je nach
Steuer-Entity (climate: set_hvac_mode/set_temperature; Select: select_option +
Number).
"""
from __future__ import annotations

from hems.actuation import HeatingPlan, plan_heating_control


def _hp(**kw) -> HeatingPlan:
    base = dict(
        modus="heizen",
        vlt_ziel_c=40.0,
        current_mode="heizen",
        current_setpoint=40.0,
    )
    base.update(kw)
    return plan_heating_control(**base)


# --- Unbekannter Modus: nichts anfassen --------------------------------------


def test_unbekannter_modus_leerer_plan():
    assert _hp(modus="unbekannt") == HeatingPlan(None, None)


def test_leerer_modus_leerer_plan():
    assert _hp(modus="") == HeatingPlan(None, None)


# --- Modus stellen -----------------------------------------------------------


def test_modus_wird_gestellt_wenn_abweichend():
    hp = _hp(modus="heizen", current_mode="aus", vlt_ziel_c=40.0, current_setpoint=40.0)
    assert hp.set_mode == "heizen"


def test_modus_passt_nicht_stellen():
    assert _hp(modus="heizen", current_mode="heizen").set_mode is None


def test_unbekannter_ist_modus_wird_gestellt():
    # current_mode None (z. B. climate-Zustand "idle"/"auto") -> Modus setzen.
    assert _hp(current_mode=None).set_mode == "heizen"


def test_kuehlen_wird_gestellt():
    hp = _hp(modus="kuehlen", current_mode="heizen", vlt_ziel_c=20.0, current_setpoint=25.0)
    assert hp.set_mode == "kuehlen"
    assert hp.set_setpoint == 20.0


# --- Vorlauf-Sollwert --------------------------------------------------------


def test_sollwert_wird_gestellt_wenn_abweichend():
    assert _hp(current_setpoint=35.0).set_setpoint == 40.0


def test_sollwert_passt_nicht_stellen():
    assert _hp(current_setpoint=40.0).set_setpoint is None


def test_sollwert_unbekannt_wird_gestellt():
    assert _hp(current_setpoint=None).set_setpoint == 40.0


def test_sollwert_vergleich_ganzzahlig():
    # Vergleich auf ganze °C: 40.9 gilt als 40.
    assert _hp(current_setpoint=40.0, vlt_ziel_c=40.9).set_setpoint is None


def test_kein_sollwert_ohne_vlt_ziel():
    assert _hp(vlt_ziel_c=None, current_setpoint=None).set_setpoint is None


# --- Aus-Modus hat keinen Vorlauf --------------------------------------------


def test_aus_stellt_keinen_sollwert():
    # Auch wenn ein vlt_ziel_c durchgereicht wird: im Aus-Modus kein Vorlauf.
    hp = _hp(modus="aus", current_mode="heizen", vlt_ziel_c=40.0, current_setpoint=10.0)
    assert hp.set_mode == "aus"
    assert hp.set_setpoint is None


def test_aus_passt_leerer_plan():
    hp = _hp(modus="aus", current_mode="aus", vlt_ziel_c=40.0)
    assert hp == HeatingPlan(None, None)


# --- Warmwasserbereitung: Gate der optionalen Rolle ---------------------------
#
# Die Anlage hebt den Vorlauf-Soll waehrend der Speicherladung selbst an. Wuerde
# HEMS nachfuehren, schreiben beide jeden Zyklus gegeneinander. Ohne die
# optionale Rolle ist ww_bereitung False und alles bleibt wie vorher - das
# belegen saemtliche Tests oberhalb, die den Parameter nicht setzen.


def test_ww_bereitung_stellt_weder_modus_noch_sollwert():
    hp = _hp(
        modus="kuehlen",
        current_mode="heizen",
        vlt_ziel_c=21.0,
        current_setpoint=55.0,
        ww_bereitung=True,
    )
    assert hp == HeatingPlan(None, None)


def test_ohne_ww_bereitung_wird_dieselbe_lage_gestellt():
    # Gegenprobe zum Test darueber: identische Eingaben, nur ohne Ladung.
    hp = _hp(
        modus="kuehlen",
        current_mode="heizen",
        vlt_ziel_c=21.0,
        current_setpoint=55.0,
        ww_bereitung=False,
    )
    assert hp == HeatingPlan("kuehlen", 21.0)


def test_ww_bereitung_default_ist_aus():
    # Ohne konfigurierte Rolle wird der Parameter gar nicht uebergeben.
    hp = plan_heating_control(
        modus="heizen", vlt_ziel_c=40.0, current_mode="aus", current_setpoint=30.0
    )
    assert hp == HeatingPlan("heizen", 40.0)


def test_ww_bereitung_bei_unbekanntem_modus_bleibt_leer():
    assert _hp(modus="unbekannt", ww_bereitung=True) == HeatingPlan(None, None)
