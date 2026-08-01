"""Übernahme der gemessenen Heizkurve in den Heizkreis.

Die Wärmepumpen-Analyse schlägt eine Heizkurve vor: Fußpunkt, Steilheit und
Vorlauf-Minimum, aus der Regression von Vorlauf gegen Außentemperatur über
Wochen. Ob HEMS danach fährt oder bei den konfigurierten Werten bleibt, steht
hier.

**Warum das gedämpft sein muss.** Die Empfehlung entsteht aus Betrieb, den
HEMS mit der vorigen Empfehlung selbst erzeugt hat. Das ist eine echte
Rückkopplung: Senkt HEMS die Kurve, misst die Analyse anschließend niedrigere
Vorläufe und schlägt wieder eine niedrigere Kurve vor. Ohne Dämpfung wandert
die Kurve, bis das Haus kalt ist — und jeder einzelne Schritt sähe dabei
begründet aus.

Drei Bremsen, die zusammen wirken müssen:

1. **Nur bei belastbarer Datenbasis.** Nicht `datenbasis`, sondern
   `datenbasis_empfehlung` — die eine sagt, wie sauber gerade gemessen wird,
   die andere, wie lange schon beobachtet wurde. Für eine Kurve zählt die
   zweite.
2. **Höchstens einmal am Tag.** Eine Kurve, die sich stündlich bewegt, ist
   keine Kurve. Und die Rückkopplung braucht Zeit: nach einer Änderung muss
   das Haus erst in den neuen Zustand kommen, bevor die nächste Messung
   überhaupt etwas Neues aussagt.
3. **Erst ab einem spürbaren Unterschied.** Unter einem Kelvin Fußpunkt ändert
   sich am Vorlauf-Sollwert nichts, was die Anlage auflöst — die Aktuierung
   schreibt auf ganze Grad. Ohne diese Schwelle liefe der Tagesrhythmus
   dauerhaft, ohne je etwas zu bewirken.

Was hier **nicht** übernommen wird: `vlt_min_cold_c`, die Untergrenze bei
tiefen Außentemperaturen. Sie ist eine Komfort- und Sicherheitsentscheidung
über den Absenkbetrieb, keine Aussage über das Wärmeabgabesystem — die Analyse
schätzt sie nicht und soll sie nicht überschreiben.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ..const import (
    DATENBASIS_BELASTBAR,
    KURVE_FUSSPUNKT_MAX_C,
    KURVE_FUSSPUNKT_MIN_C,
    KURVE_MIN_ABSTAND_H,
    KURVE_SCHWELLE_FUSSPUNKT_K,
    KURVE_SCHWELLE_STEILHEIT,
    KURVE_STEILHEIT_MAX,
    KURVE_STEILHEIT_MIN,
)
from .types import HeatingState, PlanInput, PlanResult

# Zustände von `quelle`. Sie stehen als Attribut am Heizkreis-Sensor, damit
# ablesbar bleibt, warum gerade welche Kurve gilt.
QUELLE_KONFIGURIERT = "konfiguriert"
QUELLE_EMPFEHLUNG = "empfehlung"
QUELLE_WARTET = "wartet"


@dataclass
class KurvenWahl:
    """Welche Heizkurve gilt, und warum."""

    fusspunkt_c: float
    steilheit: float
    vorlauf_min_c: float
    quelle: str = QUELLE_KONFIGURIERT
    grund: str = "Übernahme ist aus"


def kurven_wahl(inp: PlanInput, res: PlanResult) -> KurvenWahl:
    """Die gültige Heizkurve bestimmen und die Übernahme fortschreiben.

    Schreibt bei einer Übernahme die neuen Werte und den Zeitpunkt in
    `res.flags`; sonst reicht sie die bisherigen durch. Wie überall im Planner
    hält der Aufrufer die Flags zwischen zwei Läufen.
    """
    h = inp.heating
    fest = KurvenWahl(
        fusspunkt_c=h.curve_base_c,
        steilheit=h.curve_slope,
        vorlauf_min_c=h.vlt_min_c,
    )
    if not h.curve_from_analysis:
        _flags_leeren(res)
        return fest

    if h.empfehlung_mehrdeutig:
        _flags_leeren(res)
        fest.grund = (
            "Mehrere Wärmepumpen-Analysen konfiguriert — welche gemeint ist, "
            "ist nicht entscheidbar"
        )
        return fest

    vorschlag = _vorschlag(h)
    uebernommen = _uebernommene(inp)

    if vorschlag is None:
        if uebernommen is None:
            _flags_leeren(res)
            fest.quelle = QUELLE_WARTET
            fest.grund = _warte_grund(h)
            return fest
        # Eine einmal übernommene Kurve bleibt stehen, wenn die Datenbasis
        # abfällt. Sie war belastbar, als sie kam; auf die konfigurierten Werte
        # zurückzuspringen wäre eine zweite Änderung ohne neue Erkenntnis, und
        # zwar genau dann, wenn gerade niemand mehr hinsieht.
        _flags_halten(inp, res)
        return KurvenWahl(
            *uebernommen,
            quelle=QUELLE_EMPFEHLUNG,
            grund=(
                "Datenbasis der Empfehlung reicht gerade nicht; die zuletzt "
                "übernommene Kurve gilt weiter"
            ),
        )

    if uebernommen is None:
        _flags_setzen(inp, res, vorschlag)
        return KurvenWahl(
            *vorschlag, quelle=QUELLE_EMPFEHLUNG, grund="Empfehlung erstmals übernommen"
        )

    seit = inp.flags.kurve_uebernommen_am
    if seit is not None and inp.now - seit < timedelta(hours=KURVE_MIN_ABSTAND_H):
        _flags_halten(inp, res)
        naechste = seit + timedelta(hours=KURVE_MIN_ABSTAND_H)
        return KurvenWahl(
            *uebernommen,
            quelle=QUELLE_EMPFEHLUNG,
            grund=f"Nächste Prüfung frühestens {naechste:%d.%m. %H:%M}",
        )

    if not _spuerbar(uebernommen, vorschlag):
        _flags_halten(inp, res)
        return KurvenWahl(
            *uebernommen,
            quelle=QUELLE_EMPFEHLUNG,
            grund="Empfehlung weicht zu wenig ab, um etwas zu ändern",
        )

    _flags_setzen(inp, res, vorschlag)
    return KurvenWahl(
        *vorschlag, quelle=QUELLE_EMPFEHLUNG, grund="Empfehlung heute übernommen"
    )


def _vorschlag(h: HeatingState) -> tuple[float, float, float] | None:
    """Der Empfehlung entnommene Kurve, begrenzt — oder None.

    Begrenzt und nicht verworfen: Eine Regression über wenige Wochen kann
    Werte liefern, die außerhalb jedes sinnvollen Bereichs liegen, ohne dass
    die Datenbasis das merkt. Die Grenzen sind dieselben, die der
    Konfigurationsdialog zulässt — was dort niemand eintragen könnte, soll
    auch nicht über die Empfehlung hineinkommen.
    """
    if h.empfehlung_datenbasis != DATENBASIS_BELASTBAR:
        return None
    if h.empfehlung_fusspunkt_c is None or h.empfehlung_steilheit is None:
        return None

    fusspunkt = _grenze(
        h.empfehlung_fusspunkt_c, KURVE_FUSSPUNKT_MIN_C, KURVE_FUSSPUNKT_MAX_C
    )
    steilheit = _grenze(h.empfehlung_steilheit, KURVE_STEILHEIT_MIN, KURVE_STEILHEIT_MAX)
    # Ohne eigene Empfehlung fürs Minimum bleibt der konfigurierte Wert. Er
    # darf das Maximum nicht überschreiten — sonst stünde die Untergrenze über
    # der Obergrenze und der Vorlauf hinge fest.
    vorlauf_min = h.vlt_min_c
    if h.empfehlung_vorlauf_min_c is not None:
        vorlauf_min = h.empfehlung_vorlauf_min_c
    vorlauf_min = _grenze(vorlauf_min, KURVE_FUSSPUNKT_MIN_C, h.vlt_max_c)
    return round(fusspunkt, 1), round(steilheit, 2), float(round(vorlauf_min))


def _uebernommene(inp: PlanInput) -> tuple[float, float, float] | None:
    f = inp.flags
    if f.kurve_fusspunkt_c is None or f.kurve_steilheit is None:
        return None
    if f.kurve_vorlauf_min_c is None:
        return None
    return f.kurve_fusspunkt_c, f.kurve_steilheit, f.kurve_vorlauf_min_c


def _spuerbar(
    alt: tuple[float, float, float], neu: tuple[float, float, float]
) -> bool:
    return (
        abs(neu[0] - alt[0]) >= KURVE_SCHWELLE_FUSSPUNKT_K
        or abs(neu[1] - alt[1]) >= KURVE_SCHWELLE_STEILHEIT
        or abs(neu[2] - alt[2]) >= 1.0
    )


def _warte_grund(h: HeatingState) -> str:
    if h.empfehlung_datenbasis in (None, ""):
        return "Keine Wärmepumpen-Analyse konfiguriert"
    if h.empfehlung_datenbasis != DATENBASIS_BELASTBAR:
        return (
            f"Datenbasis der Empfehlung ist {h.empfehlung_datenbasis!r}, "
            "übernommen wird erst bei 'belastbar'"
        )
    return "Die Analyse hat noch keine Kurve vorgeschlagen"


def _grenze(wert: float, unten: float, oben: float) -> float:
    return max(unten, min(wert, oben))


def _flags_leeren(res: PlanResult) -> None:
    res.flags.kurve_fusspunkt_c = None
    res.flags.kurve_steilheit = None
    res.flags.kurve_vorlauf_min_c = None
    res.flags.kurve_uebernommen_am = None


def _flags_halten(inp: PlanInput, res: PlanResult) -> None:
    res.flags.kurve_fusspunkt_c = inp.flags.kurve_fusspunkt_c
    res.flags.kurve_steilheit = inp.flags.kurve_steilheit
    res.flags.kurve_vorlauf_min_c = inp.flags.kurve_vorlauf_min_c
    res.flags.kurve_uebernommen_am = inp.flags.kurve_uebernommen_am


def _flags_setzen(
    inp: PlanInput, res: PlanResult, werte: tuple[float, float, float]
) -> None:
    res.flags.kurve_fusspunkt_c, res.flags.kurve_steilheit = werte[0], werte[1]
    res.flags.kurve_vorlauf_min_c = werte[2]
    res.flags.kurve_uebernommen_am = inp.now
