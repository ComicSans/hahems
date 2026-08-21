"""Charakterisierung der Warmwasser-Empfehlung.

WW ist in compute_plan/_priorities verwoben (Sperre, Legionellen, PV-Boost,
Basis/Komfort-Latches, Sollwert). Diese Tests nageln das Verhalten fest, bevor
die Logik nach strategies/water.py extrahiert wird (Schritt 3).
"""
from __future__ import annotations

from datetime import timedelta

from factories import NOON, plan_input
from hems import planner as P
from hems.strategies.types import PlanResult


def _ww(**kw) -> PlanResult:
    return P.compute_plan(plan_input(**kw))


def test_kein_geraet_kein_sollwert():
    r = _ww(thermal_present=False, thermal_temp=None)
    assert r.warmwasser_soll_c is None
    assert r.warmwasser_status == ""


def test_kaltes_wasser_basisladung():
    r = _ww(thermal_present=True, thermal_temp=40.0)
    assert r.warmwasser_soll_c == 48.0
    assert r.warmwasser_status == "basis"
    assert r.flags.warmwasser_basis is True


def test_sperrzeit_schaltet_aus():
    sperr = [(NOON.replace(hour=10), NOON.replace(hour=12))]
    r = _ww(thermal_present=True, thermal_temp=40.0, thermal_block_windows=sperr)
    assert r.warmwasser_gesperrt is True
    assert r.warmwasser_soll_c is None
    assert r.warmwasser_status == "aus"


def test_legionellenschutz_hat_vorrang():
    leg = [(NOON.replace(hour=10), NOON.replace(hour=12))]
    r = _ww(thermal_present=True, thermal_temp=40.0, thermal_legionella_windows=leg)
    assert r.warmwasser_legionelle_aktiv is True
    assert r.warmwasser_soll_c == 60.0
    assert r.warmwasser_status == "legionellenschutz"


def test_pv_boost_auf_komfort():
    # Speicher fast voll (90 %) + kräftige Einspeisung + Temperatur unter Komfort.
    r = _ww(thermal_present=True, thermal_temp=55.0, socs=[90, 90, 90], saldo_w=-3000)
    assert r.flags.warmwasser_boost_soc is True
    assert r.flags.warmwasser_boost_saldo is True
    assert r.warmwasser_soll_c == 60.0
    assert r.warmwasser_status == "pv_boost"


def test_warm_bleibt_basis_ohne_boost():
    # Über Komfort, aber ohne Boost-Bedingungen -> Basis-Sollwert, Flags aus.
    r = _ww(thermal_present=True, thermal_temp=62.0)
    assert r.warmwasser_status == "basis"
    assert r.flags.warmwasser_komfort is False


def _boost_lauf(minute: int, flags, *, saldo_w: float):
    """Ein Planlauf `minute` Minuten nach dem Boost-Start, Flags fortgeschrieben."""
    return _ww(
        now=NOON + timedelta(minutes=minute),
        flags=flags,
        thermal_present=True,
        thermal_temp=55.0,
        socs=[90, 90, 90],
        saldo_w=saldo_w,
    )


def test_boost_haelt_mindestabstand_wenn_die_einspeisung_wegbricht():
    # Der reale Fall vom 04.08.2026: 18:14 Boost an, 18:26 wieder Basis. Der
    # Boiler zieht die Einspeisung selbst weg, die ihn eingeschaltet hat.
    an = _boost_lauf(0, None, saldo_w=-3000)
    assert an.warmwasser_status == "pv_boost"
    assert an.flags.warmwasser_boost_seit == NOON
    assert an.warmwasser_boost_frei_ab == NOON + timedelta(minutes=60)

    # Zwei Minuten später ist die Einspeisung weg — das Saldo-Kriterium kippt,
    # der Boost bleibt trotzdem stehen.
    weg = _boost_lauf(2, an.flags, saldo_w=500)
    assert weg.flags.warmwasser_boost_saldo is False
    assert weg.warmwasser_status == "pv_boost"

    # Nach zwölf Minuten (dem gemeldeten Rückfall) steht er immer noch.
    spaeter = _boost_lauf(12, weg.flags, saldo_w=500)
    assert spaeter.warmwasser_status == "pv_boost"
    assert spaeter.warmwasser_soll_c == 60.0

    # Erst nach Ablauf des Mindestabstands fällt er auf Basis zurück.
    ende = _boost_lauf(61, spaeter.flags, saldo_w=500)
    assert ende.warmwasser_status == "basis"
    assert ende.warmwasser_soll_c == 48.0
    assert ende.flags.warmwasser_boost_seit == NOON + timedelta(minutes=61)

    # Ist die Sperre abgelaufen, steht kein Zeitpunkt mehr in der Anzeige.
    frei = _boost_lauf(130, ende.flags, saldo_w=500)
    assert frei.warmwasser_boost_frei_ab is None


def test_boost_startet_erst_nach_dem_mindestabstand_neu():
    # Der Abstand gilt in beide Richtungen: Nach dem Ende wartet auch der
    # nächste Start, sonst verschöbe die Sperre das Takten nur um eine
    # halbe Periode.
    an = _boost_lauf(0, None, saldo_w=-3000)
    aus = _boost_lauf(61, an.flags, saldo_w=500)
    assert aus.warmwasser_status == "basis"

    frueh = _boost_lauf(91, aus.flags, saldo_w=-3000)
    assert frueh.flags.warmwasser_boost_saldo is True
    assert frueh.warmwasser_status == "basis"

    spaet = _boost_lauf(122, frueh.flags, saldo_w=-3000)
    assert spaet.warmwasser_status == "pv_boost"


def test_boost_startet_nach_neustart_sofort():
    # Nach einem Neustart sind die Flags leer (kein Zeitstempel) — der Boost
    # darf sofort greifen, statt eine Stunde stumm zu bleiben.
    r = _ww(thermal_present=True, thermal_temp=55.0, socs=[90, 90, 90], saldo_w=-3000)
    assert r.warmwasser_status == "pv_boost"


def test_sperrzeit_ueberstimmt_den_gehaltenen_boost():
    an = _boost_lauf(0, None, saldo_w=-3000)
    sperr = [(NOON, NOON + timedelta(hours=2))]
    r = _ww(
        now=NOON + timedelta(minutes=5),
        flags=an.flags,
        thermal_present=True,
        thermal_temp=55.0,
        socs=[90, 90, 90],
        saldo_w=-3000,
        thermal_block_windows=sperr,
    )
    assert r.warmwasser_status == "aus"
    assert r.warmwasser_soll_c is None


def test_ev_zwang_beendet_pv_boost_sofort():
    # Boost läuft (Speicher voll, kräftige Einspeisung) und ist per
    # Mindestabstand gehalten — die Zwangsladung setzt sich trotzdem sofort
    # durch: der Zwang kauft notfalls Netzstrom, der Boiler bekäme ihn ab.
    r = _ww(
        thermal_present=True,
        thermal_temp=55.0,
        socs=[90, 90, 90],
        saldo_w=-3000,
        ev_force=True,
        wallbox_w=4000.0,
    )
    assert r.warmwasser_soll_c == 48.0
    assert r.warmwasser_status == "ev_zwang"
    # Der gehaltene Boost-Zustand bleibt unangetastet — nur der Status weicht,
    # wie bei Sperrzeit und Legionellenschutz.
    assert r.flags.warmwasser_boost is True
    # Und die Empfehlung darf den Boost nicht weiter ausweisen.
    assert not any("PV-Boost" in p for p in r.prioritaeten)


def test_ev_zwang_ohne_ladendes_auto_laesst_den_boost_laufen():
    # Schalter an, aber die Wallbox zieht nichts: es gibt nichts zu sparen.
    r = _ww(
        thermal_present=True,
        thermal_temp=55.0,
        socs=[90, 90, 90],
        saldo_w=-3000,
        ev_force=True,
        wallbox_w=0.0,
    )
    assert r.warmwasser_status == "pv_boost"
    assert r.warmwasser_soll_c == 60.0
