"""Waermeverlust des Hauses und Vorschlag fuer die Heizkurve.

Beides entsteht aus linearer Regression ueber Stundenwerte. Die Empfehlung
wird nur veroeffentlicht — geschrieben wird sie nie, das bleibt Sache des
EMS.
"""
from __future__ import annotations

from .types import (
    DATENBASIS_BELASTBAR,
    DATENBASIS_KEINE,
    DATENBASIS_UNZUREICHEND,
    DATENBASIS_VORLAEUFIG,
    Kurvenempfehlung,
)

# Unter so vielen Stundenpaaren sagt eine Regression nichts. Die Schwellen
# entscheiden ueber die Datenbasis, nicht ueber Erfolg oder Misserfolg.
MIN_PUNKTE_VORLAEUFIG = 48
MIN_PUNKTE_BELASTBAR = 336  # rund zwei Wochen Heizbetrieb

# Sicherheitsabstand auf den empfohlenen Vorlauf. Die Regression beschreibt
# den Betrieb, der die Raeume warm bekommen hat — sie kennt keine Reserve fuer
# den kaeltesten Tag, den sie noch nicht gesehen hat.
RESERVE_K = 2.0

# Empfehlungen ausserhalb dieser Grenzen werden nicht ausgegeben.
VORLAUF_MIN_C = 20.0
VORLAUF_MAX_C = 60.0


def _regression(punkte: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Steigung und Achsenabschnitt einer Ausgleichsgeraden y = a*x + b."""
    n = len(punkte)
    if n < 2:
        return None
    summe_x = sum(x for x, _ in punkte)
    summe_y = sum(y for _, y in punkte)
    mittel_x = summe_x / n
    mittel_y = summe_y / n
    zaehler = sum((x - mittel_x) * (y - mittel_y) for x, y in punkte)
    nenner = sum((x - mittel_x) ** 2 for x, _ in punkte)
    if nenner == 0:
        return None
    a = zaehler / nenner
    return a, mittel_y - a * mittel_x


def waermeverlust(
    punkte: list[tuple[float, float]],
) -> tuple[float, float] | None:
    """Waermeverlustkoeffizient in W/K und Heizgrenze in Grad Celsius.

    Erwartet Paare aus Aussentemperatur und thermischer Leistung. Die
    Leistung faellt mit steigender Aussentemperatur, die Steigung ist also
    negativ; zurueckgegeben wird ihr Betrag. Die Heizgrenze ist die
    Aussentemperatur, bei der die Ausgleichsgerade null Leistung erreicht.
    """
    fit = _regression(punkte)
    if fit is None:
        return None
    steigung, abschnitt = fit
    if steigung >= 0:
        # Kein Heizverhalten erkennbar — etwa nur Sommerdaten.
        return None
    return abs(steigung), -abschnitt / steigung


def _datenbasis(n: int) -> str:
    if n < MIN_PUNKTE_VORLAEUFIG:
        return DATENBASIS_UNZUREICHEND if n else DATENBASIS_KEINE
    if n < MIN_PUNKTE_BELASTBAR:
        return DATENBASIS_VORLAEUFIG
    return DATENBASIS_BELASTBAR


def empfiehl_kurve(
    punkte: list[tuple[float, float]],
    heizgrenze_c: float | None = None,
) -> Kurvenempfehlung:
    """Heizkurve aus dem beobachteten Betrieb vorschlagen.

    Erwartet Paare aus Aussentemperatur und **gemessener** Vorlauftemperatur,
    und zwar nur aus Stunden, in denen geheizt wurde und die Raeume versorgt
    waren. Die Ausgleichsgerade durch diese Punkte ist die Kurve, die die
    Anlage faktisch gefahren hat und die ausgereicht hat.

    Bewusst konservativ: Der Vorschlag bildet den erfolgreichen Betrieb ab
    und senkt ihn nicht eigenmaechtig weiter ab. Wer die Kurve druecken will,
    sieht am Effizienzverlauf, ob es getragen hat — eine Empfehlung, die
    ueber das Beobachtete hinausgeht, waere geraten.
    """
    n = len(punkte)
    basis = _datenbasis(n)
    if basis in (DATENBASIS_KEINE, DATENBASIS_UNZUREICHEND):
        return Kurvenempfehlung(datenbasis=basis)

    fit = _regression(punkte)
    if fit is None:
        return Kurvenempfehlung(datenbasis=DATENBASIS_UNZUREICHEND)
    steigung, abschnitt = fit
    if steigung >= 0:
        # Vorlauf steigt mit der Aussentemperatur — das ist keine
        # witterungsgefuehrte Kurve, daraus laesst sich nichts ableiten.
        return Kurvenempfehlung(datenbasis=DATENBASIS_UNZUREICHEND)

    grenze = heizgrenze_c if heizgrenze_c is not None else 15.0
    fusspunkt = steigung * grenze + abschnitt + RESERVE_K
    # Steilheit als Vorlaufanhebung je Kelvin Aussentemperaturabfall.
    steilheit = abs(steigung)

    if not (VORLAUF_MIN_C <= fusspunkt <= VORLAUF_MAX_C):
        return Kurvenempfehlung(datenbasis=DATENBASIS_UNZUREICHEND)

    return Kurvenempfehlung(
        fusspunkt_c=round(fusspunkt, 1),
        steilheit=round(steilheit, 2),
        vorlauf_min_c=round(max(VORLAUF_MIN_C, fusspunkt - RESERVE_K), 1),
        heizgrenze_c=round(grenze, 1),
        datenbasis=basis,
    )
