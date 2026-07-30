"""Hinweise: zwei Schwellen, kein Flattern."""
from __future__ import annotations

from analysis import hints
from analysis.types import (
    DATENBASIS_BELASTBAR,
    DATENBASIS_VORLAEUFIG,
    HinweisZustand,
)


def test_niedrige_spreizung_schaltet_ein_und_bleibt_im_zwischenbereich():
    z = hints.bewerte(HinweisZustand(), hints.Tagesbild(spreizung_mittel_k=2.5))
    assert z.spreizung_niedrig

    # Zwischen den Schwellen bleibt der Hinweis stehen — genau das verhindert
    # das Hin und Her um einen einzelnen Grenzwert.
    z = hints.bewerte(z, hints.Tagesbild(spreizung_mittel_k=3.5))
    assert z.spreizung_niedrig

    z = hints.bewerte(z, hints.Tagesbild(spreizung_mittel_k=4.5))
    assert not z.spreizung_niedrig


def test_hohe_spreizung_ist_der_gegenfall():
    z = hints.bewerte(HinweisZustand(), hints.Tagesbild(spreizung_mittel_k=9.0))
    assert z.spreizung_hoch
    assert not z.spreizung_niedrig

    z = hints.bewerte(z, hints.Tagesbild(spreizung_mittel_k=6.5))
    assert not z.spreizung_hoch


def test_taktung_schaltet_erst_oberhalb_der_oberen_schwelle():
    z = hints.bewerte(HinweisZustand(), hints.Tagesbild(takte_pro_tag=16.0))
    assert not z.taktung_hoch

    z = hints.bewerte(z, hints.Tagesbild(takte_pro_tag=25.0))
    assert z.taktung_hoch

    z = hints.bewerte(z, hints.Tagesbild(takte_pro_tag=16.0))
    assert z.taktung_hoch


def test_fehlende_kennzahl_loescht_den_hinweis_nicht():
    # Ein Messausfall ist kein Beleg dafuer, dass das Problem weg ist.
    z = hints.bewerte(HinweisZustand(), hints.Tagesbild(takte_pro_tag=30.0))
    assert z.taktung_hoch
    assert hints.bewerte(z, hints.Tagesbild()).taktung_hoch


def test_datenblattvergleich_verlangt_belastbare_datenbasis():
    schwach = hints.bewerte(
        HinweisZustand(),
        hints.Tagesbild(cop_abweichung_prozent=-40.0, datenbasis=DATENBASIS_VORLAEUFIG),
    )
    assert not schwach.effizienz_unter_erwartung

    stark = hints.bewerte(
        HinweisZustand(),
        hints.Tagesbild(cop_abweichung_prozent=-40.0, datenbasis=DATENBASIS_BELASTBAR),
    )
    assert stark.effizienz_unter_erwartung


def test_leichte_abweichung_loest_nichts_aus():
    # Die Schwelle liegt jenseits des Modellfehlers von rund 17 Prozent.
    z = hints.bewerte(
        HinweisZustand(),
        hints.Tagesbild(cop_abweichung_prozent=-10.0, datenbasis=DATENBASIS_BELASTBAR),
    )
    assert not z.effizienz_unter_erwartung
