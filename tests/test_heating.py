"""Charakterisierung der Heizkreis-Empfehlung (`_heating_plan`).

Nagelt das Verhalten fest, bevor die Logik nach strategies/heating.py verschoben
wird (Schritt 3, reiner Move).
"""
from __future__ import annotations

from datetime import timedelta

from factories import NOON, heating, plan_input
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
        flags=PlanFlags(waermepumpe_frost=True),
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


# --- Warmwasserbereitung: die Rolle wird nur durchgereicht --------------------


def test_ww_bereitung_wird_in_die_empfehlung_gereicht():
    # Die Empfehlung selbst bleibt unveraendert - das Aussetzen entscheidet
    # spaeter plan_heating_control, nicht die Strategie.
    r = _hz(heating_state=heating(outdoor_temp_c=28.0, dhw_active=True))
    assert r.modus == "kuehlen"
    assert r.vlt_ziel_c == 18.0
    assert r.ww_bereitung is True


def test_ohne_rolle_bleibt_ww_bereitung_aus():
    r = _hz(heating_state=heating(outdoor_temp_c=28.0))
    assert r.ww_bereitung is False


def test_ww_bereitung_auch_ohne_aussentemperatur():
    # Ohne Aussentemperatur endet die Strategie frueh (Modus "unbekannt").
    # Die Rueckmeldung muss trotzdem am Ergebnis haengen, sonst faellt das Gate
    # genau dann aus, wenn der Temperaturfuehler ausfaellt.
    r = _hz(heating_state=heating(outdoor_temp_c=None, dhw_active=True))
    assert r.modus == "unbekannt"
    assert r.ww_bereitung is True


# --- Taktschutz: Zwangspause gegen zu viele Verdichterstarts ------------------


def _lauf(flags, minute: int, *, an: bool, **kw):
    """Ein Planlauf zur Minute `minute` nach NOON. Gibt (Empfehlung, Flags)."""
    kw.setdefault("outdoor_temp_c", 28.0)
    inp = plan_input(
        now=NOON + timedelta(minutes=minute),
        thermal_present=False,
        flags=flags,
        heating_state=heating(compressor_on=an, **kw),
    )
    res = P.compute_plan(inp)
    return res.heizung, res.flags


def _takte(flags, *, ab: int, anzahl: int, abstand: int = 8, **kw):
    """`anzahl` Verdichterstarts im Abstand von `abstand` Minuten."""
    r = None
    for i in range(anzahl):
        r, flags = _lauf(flags, ab + i * abstand - 1, an=False, **kw)
        r, flags = _lauf(flags, ab + i * abstand, an=True, **kw)
    return r, flags


def test_taktschutz_pausiert_nach_zu_vielen_starts():
    r, flags = _takte(PlanFlags(), ab=0, anzahl=4)
    assert r.verdichterstarts == 4
    assert r.taktschutz is True
    assert r.modus == "aus"
    assert r.vlt_ziel_c is None
    assert r.taktschutz_bis == NOON + timedelta(minutes=24 + 30)


def test_taktschutz_haelt_bis_zum_ende_der_pause():
    _, flags = _takte(PlanFlags(), ab=0, anzahl=4)
    r, _ = _lauf(flags, 53, an=False)
    assert r.taktschutz is True
    assert r.modus == "aus"


def test_taktschutz_gibt_nach_der_pause_wieder_frei():
    _, flags = _takte(PlanFlags(), ab=0, anzahl=4)
    r, flags = _lauf(flags, 55, an=False)
    assert r.taktschutz is False
    assert r.modus == "kuehlen"
    assert r.vlt_ziel_c == 18.0
    # Frisches Fenster: die alten Starts loesen nicht sofort erneut aus.
    assert r.verdichterstarts == 0


def test_taktschutz_loest_nach_der_pause_nicht_sofort_erneut_aus():
    # Nach der Freigabe laeuft der Heizkreis eine Mindestzeit, auch wenn es
    # sofort wieder taktet - sonst sperrt HEMS den Kuehlbetrieb dauerhaft aus.
    _, flags = _takte(PlanFlags(), ab=0, anzahl=4)
    _, flags = _lauf(flags, 55, an=False)
    r, flags = _takte(flags, ab=56, anzahl=4, abstand=2)
    assert r.verdichterstarts == 4
    assert r.taktschutz is False
    assert r.modus == "kuehlen"


def test_taktschutz_zaehlt_die_starts_der_warmwasserladung_nicht():
    # Die Starts gehoeren dem Speicher, nicht dem Heizkreis.
    r, _ = _takte(PlanFlags(), ab=0, anzahl=4, dhw_active=True)
    assert r.verdichterstarts == 0
    assert r.taktschutz is False


def test_taktschutz_ruht_ohne_rolle():
    flags = PlanFlags()
    r = None
    for i in range(8):
        inp = plan_input(
            now=NOON + timedelta(minutes=i * 4),
            thermal_present=False,
            flags=flags,
            heating_state=heating(outdoor_temp_c=28.0),  # compressor_on=None
        )
        res = P.compute_plan(inp)
        r, flags = res.heizung, res.flags
    assert r.taktschutz is False
    assert r.modus == "kuehlen"


def test_taktschutz_nur_im_kuehlbetrieb():
    # Fuer den Heizbetrieb fehlt die Messung, ob er ueberhaupt taktet.
    r, _ = _takte(PlanFlags(), ab=0, anzahl=4, outdoor_temp_c=5.0)
    assert r.verdichterstarts == 4
    assert r.taktschutz is False
    assert r.modus == "heizen"


def test_taktschutz_zaehlt_nur_im_fenster():
    # Vier Starts, aber ueber mehr als eine Stunde verteilt.
    r, _ = _takte(PlanFlags(), ab=0, anzahl=4, abstand=25)
    assert r.taktschutz is False
    assert r.modus == "kuehlen"


def test_taktschutz_laesst_den_kuehl_latch_stehen():
    # Die Pause uebersteuert nur die Ausgabe. Setzte sie den Latch zurueck,
    # finge die Aussentemperatur-Hysterese nach jeder Pause von vorn an.
    r, flags = _takte(PlanFlags(), ab=0, anzahl=4)
    assert r.taktschutz is True
    assert flags.waermepumpe_kuehlen is True


def test_taktschutz_veraendert_die_eingabe_flags_nicht():
    flags = PlanFlags()
    _takte(flags, ab=0, anzahl=4)
    assert flags.takt_starts == 0
    assert flags.takt_pause_bis is None


def test_taktschutz_erfindet_bei_signalluecke_keine_flanke():
    # Verdichter laeuft, Rolle faellt aus, Rolle kommt zurueck: kein Start.
    flags = PlanFlags()
    _, flags = _lauf(flags, 0, an=False)
    _, flags = _lauf(flags, 1, an=True)
    inp = plan_input(
        now=NOON + timedelta(minutes=2),
        thermal_present=False,
        flags=flags,
        heating_state=heating(outdoor_temp_c=28.0),  # Signal weg
    )
    flags = P.compute_plan(inp).flags
    r, _ = _lauf(flags, 3, an=True)
    assert r.verdichterstarts == 1
