"""Charakterisierung der Heizkreis-Empfehlung (`_heating_plan`).

Nagelt das Verhalten fest, bevor die Logik nach strategies/heating.py verschoben
wird (Schritt 3, reiner Move).
"""
from __future__ import annotations

from factories import heating, plan_input
from hems import planner as P
from hems.strategies.types import HeatingResult, PlanFlags


def _hz(**kw) -> HeatingResult:
    return P.compute_plan(plan_input(thermal_present=False, **kw)).heizung


def test_heizen_witterungsgefuehrt():
    r = _hz(heating_state=heating(outdoor_temp_c=5.0, demand_pct=50.0))
    assert r.modus == "heizen"
    assert r.vlt_ziel_c == 38.0
    assert r.t_aussen_c == 5.0


def test_aus_bei_milder_temperatur():
    r = _hz(heating_state=heating(outdoor_temp_c=20.0))
    assert r.modus == "aus"
    assert r.vlt_ziel_c is None


def test_kuehlen_ueber_schwelle():
    r = _hz(heating_state=heating(outdoor_temp_c=28.0))
    assert r.modus == "kuehlen"
    assert r.vlt_ziel_c == 18.0


def test_sommersperre_kein_heizen():
    # Oberhalb des Frost-Bands: die Sommersperre hält Heizen sicher aus.
    r = _hz(heating_state=heating(outdoor_temp_c=10.0, heat_locked=True))
    assert r.modus == "aus"
    assert r.sommer_sperre is True


def test_absenkbetrieb_ohne_anforderung():
    r = _hz(heating_state=heating(outdoor_temp_c=5.0, demand_pct=0.0))
    assert r.modus == "heizen"
    assert r.vlt_ziel_c == 28.0  # Vorlauf-Minimum
    assert r.leise_empfohlen is True


def test_unbekannt_ohne_aussentemperatur():
    r = _hz(heating_state=heating(outdoor_temp_c=None))
    assert r.modus == "unbekannt"


def test_frostschutz_uebersteuert_sommersperre():
    # Kernlücke: bei aktiver Sommersperre friert der Heizkreis bei Frost sonst
    # ein — der Frostschutz muss trotzdem Heizen erzwingen.
    r = _hz(heating_state=heating(outdoor_temp_c=1.0, heat_locked=True))
    assert r.modus == "heizen"
    assert r.frostschutz is True
    assert r.sommer_sperre is True
    assert r.vlt_ziel_c == 32.0  # Vorlauf-Minimum bei Kälte, nur Umwälzung


def test_frostschutz_haelt_band_frei_ueber_einschaltschwelle():
    # Oberhalb der Ausschaltschwelle (8 °C) greift der Frostschutz aus dem
    # Ruhezustand nicht — sonst würde die WP um die Schwelle takten.
    r = _hz(heating_state=heating(outdoor_temp_c=9.0, heat_locked=True))
    assert r.modus == "aus"
    assert r.frostschutz is False


def test_frostschutz_hysterese_haelt_im_band():
    # Einmal aktiv, bleibt der Frostschutz im Band (6–8 °C) bei 7 °C aktiv, bis
    # die Ausschaltschwelle (8 °C) überschritten wird.
    r = _hz(
        heating_state=heating(outdoor_temp_c=7.0, heat_locked=True),
        flags=PlanFlags(wp_frost=True),
    )
    assert r.modus == "heizen"
    assert r.frostschutz is True


def test_regulaerer_heizbetrieb_kein_frostschutz_flag():
    # Kalt und entsperrt: die Anlage heizt witterungsgeführt (volle Kurve),
    # der Frostschutz-Zweig übernimmt nicht.
    r = _hz(heating_state=heating(outdoor_temp_c=1.0, demand_pct=50.0))
    assert r.modus == "heizen"
    assert r.frostschutz is False
    assert r.vlt_ziel_c == 42.0  # 40 − 1×0.8 + 50 % × 5 K
