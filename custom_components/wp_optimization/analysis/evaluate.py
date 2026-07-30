"""Auswertelauf: fuehrt die Einzelteile zu einer Analyse zusammen.

Reine Funktion, wie `compute_plan` in HEMS — kein Zustand, kein Home
Assistant, keine Uhr. Alles, was sie wissen muss, steht im Eingang.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import curve, hints, presets, thermal
from .types import (
    DATENBASIS_BELASTBAR,
    DATENBASIS_KEINE,
    DATENBASIS_UNZUREICHEND,
    DATENBASIS_VORLAEUFIG,
    Analyse,
    HinweisZustand,
    Messwert,
    Preset,
    schlechtere_datenbasis,
)

# Die im Kontrakt festgelegten Gruende. Die Liste ist offen nach oben.
GRUENDE_BEKANNT = frozenset({"normal", "pv_ueberschuss", "lastspitze", "sperre"})


@dataclass
class AnalyseEingang:
    """Alles, was ein Auswertelauf braucht."""

    messwert: Messwert
    preset: Preset
    hinweise: HinweisZustand = field(default_factory=HinweisZustand)
    tagesbild: hints.Tagesbild = field(default_factory=hints.Tagesbild)
    # Stundenpaare aus der Langzeitstatistik.
    verlust_punkte: list[tuple[float, float]] = field(default_factory=list)
    kurven_punkte: list[tuple[float, float]] = field(default_factory=list)


def bewerte_steuerung(grund: str | None) -> tuple[str, bool]:
    """Steuerungsgrund normalisieren.

    Ein unbekannter Grund wird wie `normal` behandelt **und** als unbekannt
    gemeldet, damit die Datenbasis abgewertet werden kann. Er wird weder
    still verworfen noch fuehrt er zu einem Fehler: sonst braeche ein EMS,
    das spaeter einen fuenften Grund einfuehrt, diese Seite lautlos.
    """
    if grund is None:
        return "normal", True
    if grund in GRUENDE_BEKANNT:
        return grund, True
    return "normal", False


def analysiere(eingang: AnalyseEingang) -> Analyse:
    """Einen Abtastpunkt auswerten und die abgeleiteten Groessen bilden."""
    m = eingang.messwert
    preset = eingang.preset

    guete = thermal.bewerte(m, preset)
    spreiz = thermal.spreizung_k(m.vorlauf_c, m.ruecklauf_c)

    p_th = None
    cop_ist = None
    if guete.gueltig:
        p_th = thermal.waermeleistung_w(
            m.durchfluss_lh, spreiz, preset.waermetraeger_faktor
        )
        cop_ist = thermal.cop(p_th, m.p_el_w)

    cop_soll = presets.erwarteter_cop(preset, m.t_aussen_c, m.vorlauf_c)
    abweichung = None
    if cop_ist is not None and cop_soll:
        abweichung = (cop_ist - cop_soll) / cop_soll * 100.0

    verlust = curve.waermeverlust(eingang.verlust_punkte)
    heizgrenze = verlust[1] if verlust else None
    empfehlung = curve.empfiehl_kurve(eingang.kurven_punkte, heizgrenze)

    basis = _datenbasis(eingang, guete.gueltig)

    # Der Datenblattvergleich ist eine Aussage ueber das Geraet ueber die
    # Zeit, nicht ueber diesen Abtastpunkt. Er braucht deshalb beides: eine
    # saubere Messkette *und* genug Historie.
    tagesbild = eingang.tagesbild
    langfrist = schlechtere_datenbasis(basis, empfehlung.datenbasis)
    if tagesbild.datenbasis != langfrist:
        tagesbild = hints.Tagesbild(
            spreizung_mittel_k=tagesbild.spreizung_mittel_k,
            takte_pro_tag=tagesbild.takte_pro_tag,
            cop_abweichung_prozent=tagesbild.cop_abweichung_prozent,
            vorlauf_ueberhoehung_k=tagesbild.vorlauf_ueberhoehung_k,
            datenbasis=langfrist,
        )

    return Analyse(
        cop_momentan=_gerundet(cop_ist, 2),
        cop_soll=_gerundet(cop_soll, 2),
        cop_soll_unsicherheit=preset.modellfehler_prozent,
        cop_abweichung=_gerundet(abweichung, 1),
        waermeleistung_w=_gerundet(p_th, 0),
        spreizung_k=_gerundet(spreiz, 2),
        verwerfungsgrund=guete.grund,
        waermeverlust_w_pro_k=_gerundet(verlust[0], 1) if verlust else None,
        kurve=empfehlung,
        hinweise=hints.bewerte(eingang.hinweise, tagesbild),
        datenbasis=basis,
    )


def _datenbasis(eingang: AnalyseEingang, gueltig: bool) -> str:
    """Guete der Messkette fuer diesen Abtastpunkt.

    Bewusst getrennt von der Datenbasis der Kurvenempfehlung: die eine sagt,
    wie sauber gerade gemessen wird, die andere, wie lange schon beobachtet
    wurde. In einen Wert zusammengeworfen saehe ein tadellos gemessener COP
    wochenlang wertlos aus, nur weil die Historie fuer eine Kurvenempfehlung
    noch nicht reicht.

    Abwertung ist an vielen Stellen moeglich und immer richtig: die Aussage
    ist nur so gut wie ihre schwaechste Zutat.
    """
    m = eingang.messwert
    if not gueltig:
        # Kein Fehler: Warmwasser, Abtauung und Stillstand sind Normalbetrieb,
        # nur eben keine Grundlage fuer eine Effizienzaussage.
        return DATENBASIS_KEINE if m.p_el_w is None else DATENBASIS_UNZUREICHEND
    basis = DATENBASIS_BELASTBAR

    # Ohne Betriebsart vermischen sich Heizen und Warmwasser.
    if m.betrieb is None:
        basis = schlechtere_datenbasis(basis, DATENBASIS_VORLAEUFIG)

    # Ein generisches Profil ist eine Hausnummer, kein Datenblatt.
    if eingang.preset.generisch:
        basis = schlechtere_datenbasis(basis, DATENBASIS_VORLAEUFIG)

    # Ausserhalb des gefitteten Kennfelds ist die Erwartung hochgerechnet.
    if presets.ausserhalb_kennfeld(eingang.preset, m.t_aussen_c):
        basis = schlechtere_datenbasis(basis, DATENBASIS_VORLAEUFIG)

    # Fremdgesteuerter Betrieb ist kein Normalbetrieb.
    _grund, bekannt = bewerte_steuerung(m.steuerung_grund)
    if not bekannt:
        basis = schlechtere_datenbasis(basis, DATENBASIS_UNZUREICHEND)
    elif m.steuerung_aktiv and m.steuerung_grund != "normal":
        basis = schlechtere_datenbasis(basis, DATENBASIS_VORLAEUFIG)

    return basis


def _gerundet(wert: float | None, stellen: int) -> float | None:
    return None if wert is None else round(wert, stellen)
