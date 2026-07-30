"""Taktzaehlung mit Hysterese."""
from __future__ import annotations

import pytest
from analysis import cycling
from analysis.types import Messwert, TaktZustand


def _lauf(werte: list[tuple[float, float]], feld: str = "verdichter_hz") -> TaktZustand:
    z = TaktZustand()
    for ts, wert in werte:
        z = cycling.fortschreiben(z, Messwert(ts=ts, **{feld: wert}))
    return z


def test_ein_start_wird_einmal_gezaehlt():
    z = _lauf([(0, 0), (60, 40), (120, 40), (180, 40)])
    assert z.starts == 1
    assert z.laeuft


def test_rauschen_um_die_schwelle_erzeugt_keine_starts():
    # Genau der Grund fuer zwei Schwellen: mit einer einzigen bei 15 Hz
    # zaehlte diese Folge sechs Starts statt einen.
    z = _lauf([(0, 0), (60, 20), (120, 12), (180, 20), (240, 12), (300, 20)])
    assert z.starts == 1


def test_echter_takt_wird_erkannt():
    z = _lauf([(0, 0), (60, 40), (120, 2), (180, 40), (240, 2), (300, 40)])
    assert z.starts == 3


def test_laufzeit_zaehlt_nur_bei_laufendem_verdichter():
    z = _lauf([(0, 0), (60, 0), (120, 40), (180, 40)])
    # Erst ab dem Abtastpunkt bei 120 s laeuft der Verdichter, die Laufzeit
    # bis 180 s betraegt damit 60 s.
    assert z.laufzeit_s == pytest.approx(60.0)


def test_lange_luecke_wird_nicht_als_laufzeit_gutgeschrieben():
    # Nach einem Neustart darf keine erfundene Dauerlaufphase entstehen.
    z = _lauf([(0, 40), (10_000, 40)])
    assert z.laufzeit_s == 0.0


def test_leistung_ersetzt_die_frequenz_wenn_sie_fehlt():
    z = _lauf([(0, 100), (60, 800), (120, 800)], feld="p_el_w")
    assert z.starts == 1
    assert z.laeuft


def test_anlaufsockel_gilt_nicht_als_laufender_verdichter():
    # 204 W sind Regelung und Umwaelzpumpe, nicht der Verdichter.
    z = _lauf([(0, 0), (60, 204), (120, 204)], feld="p_el_w")
    assert z.starts == 0


def test_mittlere_laufzeit_ohne_starts_ist_none():
    assert cycling.mittlere_laufzeit_min(0, 0.0) is None
    assert cycling.mittlere_laufzeit_min(4, 4800.0) == pytest.approx(20.0)
