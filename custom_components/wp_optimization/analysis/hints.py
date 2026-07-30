"""Hinweise an die Nutzenden.

Jeder Hinweis hat zwei Schwellen, nie eine, und wird ueber Tage gemittelt
statt je Zyklus ausgewertet. Ein Hinweis, der im Abfragetakt kippt, ist kein
Hinweis, sondern Flackern.

Bewusst qualitativ: "Umwaelzpumpe drosseln" statt "80 Prozent wuerden
reichen". Die Pumpenkennlinie ist hier nicht bekannt, eine Prozentangabe
waere Scheingenauigkeit.
"""
from __future__ import annotations

from dataclasses import dataclass

from .types import (
    DATENBASIS_BELASTBAR,
    DATENBASIS_VORLAEUFIG,
    HinweisZustand,
    latch,
)

# Spreizung: zu klein heisst, die Umwaelzpumpe foerdert mehr als noetig; zu
# gross heisst, es kommt zu wenig durch.
SPREIZUNG_NIEDRIG_AN = 3.0
SPREIZUNG_NIEDRIG_AUS = 4.0
SPREIZUNG_HOCH_AN = 8.0
SPREIZUNG_HOCH_AUS = 7.0

# Takte pro Tag. Ein Verdichter, der oefter startet, verschleisst schneller,
# ohne mehr zu leisten.
TAKTE_AN = 20.0
TAKTE_AUS = 14.0

# Abweichung vom Datenblatt in Prozent, negativ heisst schlechter als
# erwartet. Die Schwelle liegt bewusst jenseits des Modellfehlers.
ABWEICHUNG_AN = -25.0
ABWEICHUNG_AUS = -15.0

# Ueberhoehung des gefahrenen Vorlaufs gegenueber dem empfohlenen, in Kelvin.
VORLAUF_UEBER_AN = 5.0
VORLAUF_UEBER_AUS = 3.0


@dataclass(frozen=True)
class Tagesbild:
    """Ueber Tage verdichtete Kennzahlen — die Eingangsgroesse der Hinweise."""

    spreizung_mittel_k: float | None = None
    takte_pro_tag: float | None = None
    cop_abweichung_prozent: float | None = None
    vorlauf_ueberhoehung_k: float | None = None
    datenbasis: str = DATENBASIS_VORLAEUFIG


def bewerte(z: HinweisZustand, bild: Tagesbild) -> HinweisZustand:
    """Hinweiszustaende fortschreiben.

    Fehlt eine Kennzahl, bleibt der zugehoerige Hinweis auf seinem letzten
    Stand — er wird nicht stillschweigend geloescht, denn ein Messausfall ist
    kein Beleg dafuer, dass das Problem weg ist.
    """
    return HinweisZustand(
        spreizung_niedrig=_halte(
            z.spreizung_niedrig,
            bild.spreizung_mittel_k,
            SPREIZUNG_NIEDRIG_AN,
            SPREIZUNG_NIEDRIG_AUS,
        ),
        spreizung_hoch=_halte(
            z.spreizung_hoch,
            bild.spreizung_mittel_k,
            SPREIZUNG_HOCH_AN,
            SPREIZUNG_HOCH_AUS,
        ),
        taktung_hoch=_halte(
            z.taktung_hoch, bild.takte_pro_tag, TAKTE_AN, TAKTE_AUS
        ),
        vorlauf_zu_hoch=_halte(
            z.vorlauf_zu_hoch,
            bild.vorlauf_ueberhoehung_k,
            VORLAUF_UEBER_AN,
            VORLAUF_UEBER_AUS,
        ),
        # Der Datenblattvergleich ist der einzige Hinweis, der eine belastbare
        # Datenbasis verlangt: er stellt eine Behauptung ueber das Geraet auf,
        # nicht ueber die Hydraulik.
        effizienz_unter_erwartung=_halte(
            z.effizienz_unter_erwartung,
            bild.cop_abweichung_prozent
            if bild.datenbasis == DATENBASIS_BELASTBAR
            else None,
            ABWEICHUNG_AN,
            ABWEICHUNG_AUS,
        ),
    )


def _halte(aktiv: bool, wert: float | None, on: float, off: float) -> bool:
    if wert is None:
        return aktiv
    return latch(aktiv, wert, on=on, off=off)
