"""Thermische Leistung, COP und die Gueltigkeitspruefung der Messwerte.

Die Pruefung ist der wichtigste Teil dieses Moduls. Sie laeuft im Abfragetakt
und *vor* jeder Mittelung — ein Stundenmittel, in das ungueltige Momentanwerte
eingehen, ist unbrauchbar und laesst sich nachtraeglich nicht mehr retten.
"""
from __future__ import annotations

from .types import (
    BETRIEB_ABTAUEN,
    BETRIEB_AUS,
    BETRIEB_WARMWASSER,
    GRUND_ABTAUEN,
    GRUND_KEIN_DURCHFLUSS,
    GRUND_KEINE_LEISTUNG,
    GRUND_OK,
    GRUND_SPREIZUNG_ZU_KLEIN,
    GRUND_UNPLAUSIBEL,
    GRUND_WARMWASSER,
    Guete,
    Messwert,
    Preset,
)

# Unterhalb dieser elektrischen Leistung laeuft kein Verdichter, sondern nur
# Regelung und Umwaelzpumpe. Ein COP daraus waere Unsinn.
STANDBY_W = 150.0

# Obergrenze fuer einen plausiblen Momentan-COP. Alles darueber deutet auf
# einen Mess- oder Einheitenfehler hin, nicht auf eine gute Waermepumpe.
COP_MAX_PLAUSIBEL = 12.0


def spreizung_k(vorlauf_c: float | None, ruecklauf_c: float | None) -> float | None:
    """Temperaturdifferenz ueber dem Waermetauscher."""
    if vorlauf_c is None or ruecklauf_c is None:
        return None
    return vorlauf_c - ruecklauf_c


def waermeleistung_w(
    durchfluss_lh: float | None,
    spreizung: float | None,
    faktor: float = 1.163,
) -> float | None:
    """Thermische Leistung in Watt.

    Durchfluss in Litern pro Stunde mal Spreizung in Kelvin mal dem
    Waermetraegerfaktor in Wh/(l*K) ergibt Watt — nicht Kilowatt. Kommt der
    Durchfluss in Litern pro Minute, fehlt ein Faktor 60; die Umrechnung
    passiert beim Einlesen anhand der Einheit der Entity, nie durch Raten.
    """
    if durchfluss_lh is None or spreizung is None:
        return None
    return durchfluss_lh * spreizung * faktor


def cop(p_th_w: float | None, p_el_w: float | None) -> float | None:
    """Momentane Leistungszahl."""
    if p_th_w is None or p_el_w is None or p_el_w <= 0:
        return None
    return p_th_w / p_el_w


def bewerte(m: Messwert, preset: Preset) -> Guete:
    """Taugt dieser Abtastpunkt fuer eine Effizienzaussage?

    Warmwasserbereitung und Abtauung sind regulaerer Betrieb, aber keine
    Heizleistung — sie gehoeren nicht in dieselbe Kennzahl. Eine zu kleine
    Spreizung ist der haeufigste Fall: dort dominiert das Messrauschen der
    Temperaturfuehler, und der COP springt wild.
    """
    if m.betrieb == BETRIEB_ABTAUEN:
        return Guete(False, GRUND_ABTAUEN)
    if m.betrieb == BETRIEB_WARMWASSER:
        return Guete(False, GRUND_WARMWASSER)
    if m.betrieb == BETRIEB_AUS:
        return Guete(False, GRUND_KEINE_LEISTUNG)
    if m.p_el_w is None or m.p_el_w < STANDBY_W:
        return Guete(False, GRUND_KEINE_LEISTUNG)
    if m.durchfluss_lh is None or m.durchfluss_lh <= 0:
        return Guete(False, GRUND_KEIN_DURCHFLUSS)

    spreiz = spreizung_k(m.vorlauf_c, m.ruecklauf_c)
    if spreiz is None:
        return Guete(False, GRUND_UNPLAUSIBEL)
    if spreiz < preset.spreizung_min_gueltig_k:
        return Guete(False, GRUND_SPREIZUNG_ZU_KLEIN)

    p_th = waermeleistung_w(m.durchfluss_lh, spreiz, preset.waermetraeger_faktor)
    wert = cop(p_th, m.p_el_w)
    if wert is None or wert <= 0 or wert > COP_MAX_PLAUSIBEL:
        return Guete(False, GRUND_UNPLAUSIBEL)

    return Guete(True, GRUND_OK)
