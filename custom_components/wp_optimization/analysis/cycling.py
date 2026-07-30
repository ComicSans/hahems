"""Taktung: Verdichterstarts und Laufzeit.

Beides sind Zaehler, keine Messgroessen. Der Grund steht im Kontrakt: das
Stundenmittel einer Startzahl ist bedeutungslos, deshalb werden Starts und
Laufzeit monoton fortgeschrieben und jede Aussage ueber einen Zeitraum
entsteht aus der Differenz zweier Zaehlerstaende.
"""
from __future__ import annotations

from .types import Messwert, TaktZustand, latch

# Schwellen fuer "Verdichter laeuft", getrennt nach verfuegbarer Messgroesse.
# Zwei Schwellen statt einer, sonst zaehlt jedes Rauschen um den Grenzwert als
# eigener Start und die Taktzahl waere frei erfunden.
HZ_AN = 15.0
HZ_AUS = 8.0
W_AN = 400.0
W_AUS = 200.0

# Laenger als diese Luecke zwischen zwei Abtastpunkten wird nicht als Laufzeit
# gutgeschrieben — nach einem Neustart oder Ausfall waere das sonst eine
# erfundene Dauerlaufphase.
MAX_LUECKE_S = 900.0


def laeuft_verdichter(zustand: bool, m: Messwert) -> bool:
    """Verdichterzustand mit Hysterese fortschreiben.

    Die Frequenz ist das verlaesslichere Signal. Fehlt sie, muss die
    elektrische Leistung herhalten — dann liegt die Schwelle deutlich ueber
    dem Sockel aus Regelung und Umwaelzpumpe, der auch bei stehendem
    Verdichter anliegt.
    """
    if m.verdichter_hz is not None:
        return latch(zustand, m.verdichter_hz, on=HZ_AN, off=HZ_AUS)
    if m.p_el_w is not None:
        return latch(zustand, m.p_el_w, on=W_AN, off=W_AUS)
    return zustand


def fortschreiben(z: TaktZustand, m: Messwert) -> TaktZustand:
    """Einen Abtastpunkt in den Taktzustand einrechnen.

    Ein Start wird beim Uebergang von aus nach an gezaehlt. Laufzeit wird nur
    fuer die tatsaechlich verstrichene Zeit zwischen zwei Punkten
    gutgeschrieben, und nur wenn der Verdichter davor schon lief.
    """
    lief = z.laeuft
    laeuft = laeuft_verdichter(lief, m)

    laufzeit = z.laufzeit_s
    if lief and z.letzter_ts is not None:
        delta = m.ts - z.letzter_ts
        if 0 < delta <= MAX_LUECKE_S:
            laufzeit += delta

    return TaktZustand(
        laeuft=laeuft,
        starts=z.starts + (1 if laeuft and not lief else 0),
        laufzeit_s=laufzeit,
        letzter_ts=m.ts,
    )


def mittlere_laufzeit_min(starts: int, laufzeit_s: float) -> float | None:
    """Mittlere Laufzeit je Takt in Minuten.

    Reiner Anzeigewert, abgeleitet aus beiden Zaehlern. Fuer den
    Langzeitverlauf zaehlen die Zaehler selbst, nicht dieser Quotient.
    """
    if starts <= 0:
        return None
    return laufzeit_s / starts / 60.0
