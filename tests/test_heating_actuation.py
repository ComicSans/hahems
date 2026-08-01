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


# --- Taktschutz: die Pause kommt als Modus "aus" an ---------------------------


def test_taktschutz_pause_wird_als_aus_gestellt():
    # Die Strategie liefert waehrend der Zwangspause "aus" ohne Vorlauf-Soll.
    hp = _hp(modus="aus", vlt_ziel_c=None, current_mode="kuehlen", current_setpoint=21.0)
    assert hp == HeatingPlan("aus", None)


def test_taktschutz_pause_waehrend_der_warmwasserladung_stellt_nichts():
    # Laedt die Anlage gerade Warmwasser, wird nichts gestellt - die Pause
    # greift erst danach. Sie laeuft in der Zwischenzeit weiter (Zeitfenster).
    hp = _hp(
        modus="aus",
        vlt_ziel_c=None,
        current_mode="kuehlen",
        current_setpoint=21.0,
        ww_bereitung=True,
    )
    assert hp == HeatingPlan(None, None)


def test_nach_der_pause_wird_der_kuehl_sollwert_neu_geschrieben():
    # Beim Moduswechsel belegt die Anlage ihren Sollwert selbst (HR24 folgt dem
    # Modus). Beim Wiederanlauf muss HEMS ihn deshalb erneut stellen.
    hp = _hp(
        modus="kuehlen",
        vlt_ziel_c=21.0,
        current_mode="aus",
        current_setpoint=55.0,
    )
    assert hp == HeatingPlan("kuehlen", 21.0)


# --- Anlagen, die den geschriebenen Modus nicht zeigen ------------------------
#
# `last_written_mode` ist der Modus, den HEMS zuletzt selbst geschrieben hat —
# vom Actuator nur dann durchgereicht, wenn seither ein frischer Ist-Wert
# vorliegt. Ohne Steuer-Entity, nach einem Neustart und bei jeder Anlage, die
# ihren Modus sauber meldet, ist er None; dass dann alles bleibt wie vorher,
# belegen saemtliche Tests oberhalb, die den Parameter nicht setzen.


def test_ohne_quittung_verhaelt_sich_alles_wie_bisher():
    # Ausdrueckliche Gegenprobe zum Default: last_written_mode=None aendert nichts.
    hp = _hp(modus="heizen", current_mode="heizen", last_written_mode=None)
    assert hp == HeatingPlan(None, None, False)


def test_rueckweg_wird_geschrieben_obwohl_der_ist_modus_passt():
    # Der Fall, um den es geht: HEMS hatte "aus" geschrieben, die Anlage meldet
    # aber weiter "kuehlen". Beim Wiedereinschalten stimmen Ziel und Ist damit
    # ueberein — ohne den Vergleich mit dem Geschriebenen kaeme der Befehl nie an.
    hp = _hp(
        modus="kuehlen",
        vlt_ziel_c=21.0,
        current_mode="kuehlen",
        current_setpoint=21.0,
        last_written_mode="aus",
    )
    assert hp.set_mode == "kuehlen"


def test_rueckweg_ist_einmalig():
    # Nach dem Schreiben stimmt der gebuchte Modus mit dem Ziel ueberein: ab
    # da wieder Idempotenz, kein Schreiben je Zyklus.
    hp = _hp(
        modus="kuehlen",
        vlt_ziel_c=21.0,
        current_mode="kuehlen",
        current_setpoint=21.0,
        last_written_mode="kuehlen",
    )
    assert hp == HeatingPlan(None, None, False)


def test_nicht_uebernommener_modus_wird_gemeldet_und_erneut_geschrieben():
    hp = _hp(
        modus="aus",
        vlt_ziel_c=None,
        current_mode="kuehlen",
        current_setpoint=21.0,
        last_written_mode="aus",
    )
    assert hp.modus_nicht_uebernommen is True
    assert hp.set_mode == "aus"


def test_keine_meldung_solange_nichts_geschrieben_wurde():
    # Gleiche Lage, nur ohne vorherigen Schreibvorgang: das ist ein normaler
    # Moduswechsel und keine verweigerte Uebernahme.
    hp = _hp(modus="aus", vlt_ziel_c=None, current_mode="kuehlen", current_setpoint=21.0)
    assert hp.modus_nicht_uebernommen is False
    assert hp.set_mode == "aus"


def test_keine_meldung_waehrend_der_warmwasserladung():
    # In diesem Fenster stellt HEMS nichts — also gibt es auch nichts zu
    # beurteilen, selbst wenn der Ist-Modus vom Ziel abweicht.
    hp = _hp(
        modus="aus",
        vlt_ziel_c=None,
        current_mode="kuehlen",
        current_setpoint=21.0,
        ww_bereitung=True,
        last_written_mode="aus",
    )
    assert hp == HeatingPlan(None, None, False)


def test_taupunkt_anhebung_wird_als_kuehl_sollwert_geschrieben():
    # Ende der Kette: Die Strategie hebt den Kuehl-Sollwert von 12 auf die
    # Taupunkt-Untergrenze 16, und genau die muss auch geschrieben werden -
    # sonst rechnet HEMS eine Grenze aus, die nie ein Register erreicht.
    hp = _hp(
        modus="kuehlen",
        vlt_ziel_c=16.0,
        current_mode="kuehlen",
        current_setpoint=12.0,
    )
    assert hp == HeatingPlan(None, 16.0)
