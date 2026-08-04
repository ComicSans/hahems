"""Heizung (strategies/heating.py): Frostschutz, Sommersperre, Heizkurve.

Der Frostschutz ist der Grund, warum es diese Rolle gibt. Eine Heizung, die
HEMS aus Sparsamkeit abschaltet, darf das Haus nicht auskühlen lassen — und
zwar auch dann nicht, wenn außenrum etwas anderes ausfällt. Die Tests hier
sichern genau diese Kanten ab: kein Netzsaldo, kein Temperatursensor, laufende
Sommersperre, greifende Mindestpause.
"""
from __future__ import annotations

from factories import heating, plan_input, switchable
from hems import planner as P
from hems.strategies.types import PlanFlags


def _plan(**kw):
    return P.compute_plan(plan_input(**kw))


def _anlage(res, name="Wärmepumpe"):
    return next(a for a in res.heizung.anlagen if a.name == name)


def _an(res, name="Wärmepumpe"):
    return next(l.an for l in res.schaltbare.lasten if l.name == name)


# --- Frostschutz --------------------------------------------------------------
def test_frost_erzwingt_einschalten_ohne_ueberschuss():
    r = _plan(
        switchables=[switchable("Wärmepumpe", erwartet_w=1500)],
        heatings=[heating(outdoor_temp_c=1.0)],
        saldo_w=2000,  # Netzbezug, also kein Überschuss
    )
    assert _anlage(r).zwang_an is True
    assert _an(r) is True


def test_frost_bricht_die_mindestpause():
    """Sonst bliebe die Anlage bis zu min_off aus, während es friert."""
    sw = switchable("Wärmepumpe", erwartet_w=1500, aus_seit_s=60, min_off_min=60)
    r = _plan(switchables=[sw], heatings=[heating(outdoor_temp_c=-2.0)], saldo_w=2000)
    assert _an(r) is True


def test_frost_ueberstimmt_die_sommersperre():
    """Spätfrost im Sperrmonat: die Sperre darf den Kreis nicht einfrieren
    lassen."""
    r = _plan(
        switchables=[switchable("Wärmepumpe")],
        heatings=[heating(outdoor_temp_c=1.0, month=7)],
        saldo_w=2000,
    )
    assert _anlage(r).status == "frostschutz"
    assert _an(r) is True


def test_frost_entsteht_auch_ohne_netzsaldo():
    """Der Zähler ist unerreichbar — es gibt keine Überschuss-Empfehlung mehr.

    Genau dann darf der Frostschutz nicht mitverschwinden: Der Actuator stellt
    ihn aus `plan.heizung`, und die muss dafür auch ohne Saldo entstehen.
    """
    r = _plan(
        switchables=[switchable("Wärmepumpe")],
        heatings=[heating(outdoor_temp_c=-5.0)],
        saldo_w=None,
    )
    assert r.schaltbare is None
    assert _anlage(r).zwang_an is True


def test_frost_hat_hysterese():
    """Zwischen Ein- und Aus-Schwelle bleibt der vorige Zustand stehen."""
    warm = _plan(
        switchables=[switchable("Wärmepumpe")],
        heatings=[heating(outdoor_temp_c=4.0)],
        flags=PlanFlags(frost={"sw1": True}),
    )
    assert _anlage(warm).zwang_an is True
    kalt_genug_vorbei = _plan(
        switchables=[switchable("Wärmepumpe")],
        heatings=[heating(outdoor_temp_c=6.0)],
        flags=PlanFlags(frost={"sw1": True}),
    )
    assert _anlage(kalt_genug_vorbei).zwang_an is False


def test_frost_steht_in_der_empfehlung():
    """Ohne diese Zeile meldete HEMS "kein Überschuss", während es Strom kauft."""
    r = _plan(
        switchables=[switchable("Wärmepumpe")],
        heatings=[heating(outdoor_temp_c=-5.0)],
        saldo_w=2000,
        pv_remaining_kwh=0.0,
    )
    assert any("Frostschutz" in zeile for zeile in r.prioritaeten)


def test_vertauschte_frostschwellen_kehren_die_wirkung_nicht_um():
    """„Ein bei 5 °C, aus bei 3 °C" ist eine naheliegende Lesart der Felder.

    Ungeprüft läse `_latch` daraus die Gegenrichtung: Frostschutz oberhalb von
    5 °C, Freigabe unterhalb von 3 °C — genau verkehrt herum, ohne Meldung, an
    der Funktion, die vor dem Einfrieren schützen soll.
    """
    verdreht = heating(outdoor_temp_c=-5.0, frost_on_c=5.0, frost_off_c=3.0)
    assert _anlage(_plan(heatings=[verdreht])).zwang_an is True
    warm = heating(outdoor_temp_c=18.0, frost_on_c=5.0, frost_off_c=3.0)
    assert _anlage(_plan(heatings=[warm])).zwang_an is False


def test_gleiche_frostschwellen_behalten_eine_hysterese():
    """Ohne Abstand hätte `_latch` keinen Haltebereich mehr."""
    a = heating(outdoor_temp_c=3.5, frost_on_c=3.0, frost_off_c=3.0)
    r = _plan(heatings=[a], flags=PlanFlags(frost={"sw1": True}))
    assert _anlage(r).zwang_an is True


# --- Fehlende Außentemperatur -------------------------------------------------
def test_ohne_temperatur_wird_nicht_abgeschaltet():
    """Wer nicht messen kann, soll nicht regeln — schon gar nicht abschalten."""
    sw = switchable("Wärmepumpe", erwartet_w=1500, ist_an=True, an_seit_s=99999)
    r = _plan(
        switchables=[sw],
        heatings=[heating(outdoor_temp_c=None)],
        saldo_w=2000,
    )
    assert _anlage(r).status == "unbekannt"
    assert _an(r) is True


def test_ohne_temperatur_wird_eine_aus_anlage_nicht_gestartet():
    sw = switchable("Wärmepumpe", erwartet_w=1500, ist_an=False, aus_seit_s=600)
    r = _plan(
        switchables=[sw], heatings=[heating(outdoor_temp_c=None)], saldo_w=2000
    )
    assert _an(r) is False


def test_frost_ueberlebt_den_ausfall_des_sensors():
    """Fällt der Sensor mitten im Frost aus, wäre sein Wegfall die
    gefährlichste Auslegung des fehlenden Messwerts."""
    r = _plan(
        switchables=[switchable("Wärmepumpe")],
        heatings=[heating(outdoor_temp_c=None)],
        saldo_w=2000,
        flags=PlanFlags(frost={"sw1": True}),
    )
    assert _anlage(r).zwang_an is True
    assert _an(r) is True


# --- Sommersperre und Heizgrenze ---------------------------------------------
def test_sommersperre_sperrt():
    r = _plan(
        switchables=[switchable("Wärmepumpe", erwartet_w=1500)],
        heatings=[heating(outdoor_temp_c=14.0, month=7)],
        saldo_w=-4000,  # reichlich Überschuss
    )
    assert _anlage(r).status == "sommersperre"
    assert _an(r) is False


def test_sperre_wuergt_keinen_laufenden_kompressor_ab():
    """Der Monatswechsel um Mitternacht (oder das Überschreiten der Heizgrenze)
    darf eine laufende Anlage nicht mitten aus dem Takt reißen — dafür gibt es
    die Mindestlaufzeit. Zwang zum Einschalten ist Sicherheit, Zwang zum
    Ausschalten nur Sparsamkeit."""
    sw = switchable("Wärmepumpe", ist_an=True, an_seit_s=60, min_on_min=20)
    r = _plan(
        switchables=[sw],
        heatings=[heating(outdoor_temp_c=14.0, month=7)],
        saldo_w=2000,
    )
    assert _anlage(r).sperre is True
    assert _an(r) is True


def test_sperre_greift_nach_der_mindestlaufzeit():
    sw = switchable("Wärmepumpe", ist_an=True, an_seit_s=99999, min_on_min=20)
    r = _plan(
        switchables=[sw],
        heatings=[heating(outdoor_temp_c=14.0, month=7)],
        saldo_w=2000,
    )
    assert _an(r) is False


def test_sperre_ueber_den_jahreswechsel():
    r = _plan(
        heatings=[heating(outdoor_temp_c=10.0, month=1, heat_lock_from_month=11,
                          heat_lock_to_month=2)],
    )
    assert _anlage(r).status == "sommersperre"


def test_sperre_aus_wenn_monat_null():
    r = _plan(
        heatings=[heating(outdoor_temp_c=10.0, month=7, heat_lock_from_month=0,
                          heat_lock_to_month=0)],
    )
    assert _anlage(r).status == "heizen"


def test_ueber_der_heizgrenze_wird_nicht_geheizt():
    r = _plan(
        switchables=[switchable("Wärmepumpe", erwartet_w=1500)],
        heatings=[heating(outdoor_temp_c=20.0, month=1)],
        saldo_w=-4000,
    )
    assert _anlage(r).status == "heizgrenze"
    assert _an(r) is False


# --- Heizkurve ----------------------------------------------------------------
def _kurve(t: float, **kw):
    """Heizung mit abgesenkter Frostschwelle: Der Frostschutz faehrt bewusst nur
    das Vorlauf-Minimum und wuerde die Kurve sonst ueberdecken."""
    return heating(outdoor_temp_c=t, frost_on_c=-40.0, frost_off_c=-38.0, **kw)


def test_vorlauf_folgt_der_aussentemperatur():
    kalt = _anlage(_plan(heatings=[_kurve(-10.0)]))
    mild = _anlage(_plan(heatings=[_kurve(5.0)]))
    # 32 − (−10) × 0.6 = 38 ; 32 − 5 × 0.6 = 29
    assert kalt.vorlauf_c == 38.0
    assert mild.vorlauf_c == 29.0


def test_vorlauf_bleibt_in_den_grenzen():
    sehr_kalt = _anlage(_plan(heatings=[_kurve(-30.0)]))
    assert sehr_kalt.vorlauf_c == 45.0  # vlt_max


def test_frostschutz_faehrt_nur_das_minimum():
    """Ziel ist Umwälzung, nicht Komfort — sonst heizt der Frostschutz das
    Haus auf Kurve, obwohl niemand da ist."""
    a = _anlage(_plan(heatings=[heating(outdoor_temp_c=1.0)]))
    assert a.vorlauf_c == 25.0  # vlt_min


def test_ohne_vorlauf_entity_kein_sollwert():
    a = _anlage(_plan(heatings=[_kurve(0.0, hat_vorlauf_entity=False)]))
    assert a.vorlauf_c is None


# --- Zusammenspiel mit den Schaltlasten ---------------------------------------
def test_heizung_teilt_sich_das_budget_mit_den_schaltlasten():
    """Beide zusammen passen nicht in den Überschuss; die wichtigere gewinnt."""
    lasten = [
        switchable("Wärmepumpe", id="sw1", priority=1, erwartet_w=1500),
        switchable("Pool", id="b", priority=2, erwartet_w=1500),
    ]
    r = _plan(
        switchables=lasten,
        heatings=[heating(id="sw1", outdoor_temp_c=8.0)],
        saldo_w=-2000,
    )
    assert _an(r, "Wärmepumpe") is True
    assert _an(r, "Pool") is False


def test_schaltlast_ohne_heizungsrolle_bleibt_unberuehrt():
    """Die Vorgaben dürfen nur auf die Anlage wirken, zu der sie gehören."""
    r = _plan(
        switchables=[
            switchable("Wärmepumpe", id="sw1", erwartet_w=1500),
            switchable("Pool", id="b", erwartet_w=1500),
        ],
        heatings=[heating(id="sw1", outdoor_temp_c=20.0, month=7)],
        saldo_w=-6000,
    )
    assert _an(r, "Wärmepumpe") is False  # Sommersperre
    assert _an(r, "Pool") is True


def test_flags_des_aufrufers_bleiben_unveraendert():
    """`compute_plan` ist eine reine Funktion — die Dicts werden kopiert, nicht
    geteilt. Sonst schriebe der Planlauf in den Zustand des Coordinators."""
    flags = PlanFlags()
    P.compute_plan(
        plan_input(heatings=[heating(outdoor_temp_c=-5.0)], flags=flags)
    )
    assert flags.frost == {}


def test_keine_heizung_keine_empfehlung():
    assert _plan(switchables=[switchable("Pumpe")]).heizung is None
