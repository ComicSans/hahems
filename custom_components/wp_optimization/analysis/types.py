"""Gemeinsame Laufzeittypen der Analyse.

Importiert ausschliesslich aus der Standardbibliothek — nie aus einem anderen
Analysemodul und nie aus Home Assistant. Damit kann kein Importzyklus
entstehen und die Analyse bleibt ohne laufende HA-Instanz testbar.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- Datenbasis -------------------------------------------------------------
# Vier Stufen, aufsteigend nach Verlaesslichkeit. Konsumierende Seiten werten
# Empfehlungen erst ab BELASTBAR aus.
DATENBASIS_KEINE = "keine_daten"
DATENBASIS_UNZUREICHEND = "unzureichend"
DATENBASIS_VORLAEUFIG = "vorlaeufig"
DATENBASIS_BELASTBAR = "belastbar"

_DATENBASIS_RANG = {
    DATENBASIS_KEINE: 0,
    DATENBASIS_UNZUREICHEND: 1,
    DATENBASIS_VORLAEUFIG: 2,
    DATENBASIS_BELASTBAR: 3,
}


def schlechtere_datenbasis(a: str, b: str) -> str:
    """Die schwaechere von zwei Stufen zurueckgeben.

    Abwertung ist immer erlaubt, Aufwertung nie: eine einzige unsichere
    Teilgroesse macht die Gesamtaussage unsicher.
    """
    unbekannt = _DATENBASIS_RANG.get(a) is None or _DATENBASIS_RANG.get(b) is None
    if unbekannt:
        return DATENBASIS_UNZUREICHEND
    return a if _DATENBASIS_RANG[a] <= _DATENBASIS_RANG[b] else b


# --- Betriebsarten ----------------------------------------------------------
# Normalisierte Formen. Die Uebersetzung der geraetespezifischen Klartexte
# passiert in der HA-Schicht, damit hier kein Hersteller auftaucht.
BETRIEB_HEIZEN = "heizen"
BETRIEB_WARMWASSER = "warmwasser"
BETRIEB_ABTAUEN = "abtauen"
BETRIEB_AUS = "aus"

# --- Verwerfungsgruende -----------------------------------------------------
GRUND_OK = "ok"
GRUND_SPREIZUNG_ZU_KLEIN = "spreizung_zu_klein"
GRUND_KEINE_LEISTUNG = "keine_leistung"
GRUND_KEIN_DURCHFLUSS = "kein_durchfluss"
GRUND_ABTAUEN = "abtauen"
GRUND_WARMWASSER = "warmwasser"
GRUND_UNPLAUSIBEL = "unplausibel"


@dataclass(frozen=True)
class Preset:
    """Kennlinie eines Geraetemodells.

    Schluessel ist modellscharf, nicht markenscharf: allein die
    LG-Therma-V-Reihe hat vier deutlich verschiedene Kennlinien.
    """

    schluessel: str
    anzeigename: str
    quelle: str
    # COP-Polynom: cop = p1*t_aussen + p2*t_vorlauf + p3 + p4*t_aussen
    p1: float
    p2: float
    p3: float
    p4: float
    modellfehler_prozent: float
    generisch: bool = False
    spreizung_min_gueltig_k: float = 2.0
    # Nennvolumenstrom bei 5 K Auslegungsspreizung, abgeleitet aus der
    # Nennwaermeleistung. Ersatz, wenn kein Volumenstromzaehler verdrahtet
    # ist — dann ist der COP geschaetzt und nie belastbar.
    durchfluss_nominal_lh: float | None = None
    # Leistungsaufnahme bei stehendem Verdichter (Regelung, Umwaelzpumpe).
    # Anlagenspezifisch; der Wert im Preset ist ein Startwert.
    standby_w: float = 150.0
    # Waermetraeger: reines Wasser 1,163 Wh/(l*K); Glykolgemische liegen
    # einige Prozent darunter.
    waermetraeger_faktor: float = 1.163
    # Aussentemperaturbereich, in dem die Kennlinie gefittet wurde. Ausserhalb
    # extrapoliert das Polynom, deshalb wird dort abgewertet.
    gueltig_ab_c: float = -20.0
    gueltig_bis_c: float = 20.0


@dataclass(frozen=True)
class Messwert:
    """Ein Abtastpunkt. Zeitstempel als Sekunden seit Epoche, damit die
    Analyse keine Zeitzone kennen muss."""

    ts: float
    vorlauf_c: float | None = None
    ruecklauf_c: float | None = None
    durchfluss_lh: float | None = None
    p_el_w: float | None = None
    t_aussen_c: float | None = None
    verdichter_hz: float | None = None
    betrieb: str | None = None
    # Aus der Gegenrichtung des Kontrakts: uebersteuert das EMS gerade?
    steuerung_aktiv: bool = False
    steuerung_grund: str = "normal"


@dataclass(frozen=True)
class Guete:
    """Ergebnis der Gueltigkeitspruefung eines Abtastpunkts."""

    gueltig: bool
    grund: str


@dataclass
class TaktZustand:
    """Fortgeschriebener Verdichterzustand.

    Starts und Laufzeit sind monoton wachsende Zaehler. Aussagen ueber einen
    Zeitraum entstehen aus der Zaehlerdifferenz, nie aus einem Mittelwert —
    ein Stundenmittel einer Startzahl sagt nichts.
    """

    laeuft: bool = False
    starts: int = 0
    laufzeit_s: float = 0.0
    letzter_ts: float | None = None


@dataclass
class HinweisZustand:
    """Latch-Zustaende der Hinweise, damit sie nicht flattern."""

    spreizung_niedrig: bool = False
    spreizung_hoch: bool = False
    taktung_hoch: bool = False
    vorlauf_zu_hoch: bool = False
    effizienz_unter_erwartung: bool = False
    # Kein Anlagenproblem, sondern ein Messproblem: Vor- und Ruecklauf melden
    # dauerhaft denselben Wert, obwohl der Verdichter laeuft. Dann stimmt die
    # Registerzuordnung oder die Verdrahtung nicht — und die Spreizung, an der
    # fast alles haengt, ist strukturell null.
    temperaturen_identisch: bool = False


@dataclass(frozen=True)
class Kurvenempfehlung:
    """Vorschlag fuer die Heizkurve. Wird nur veroeffentlicht, nie geschrieben."""

    fusspunkt_c: float | None = None
    steilheit: float | None = None
    vorlauf_min_c: float | None = None
    heizgrenze_c: float | None = None
    datenbasis: str = DATENBASIS_KEINE


@dataclass(frozen=True)
class Analyse:
    """Gesamtergebnis eines Auswertelaufs."""

    cop_momentan: float | None = None
    cop_soll: float | None = None
    cop_soll_unsicherheit: float | None = None
    cop_abweichung: float | None = None
    waermeleistung_w: float | None = None
    spreizung_k: float | None = None
    verwerfungsgrund: str = GRUND_OK
    # Wahr, wenn der Volumenstrom aus dem Preset stammt und nicht gemessen
    # wurde. Die Datenbasis ist dann gedeckelt.
    durchfluss_geschaetzt: bool = False
    waermeverlust_w_pro_k: float | None = None
    kurve: Kurvenempfehlung = field(default_factory=Kurvenempfehlung)
    hinweise: HinweisZustand = field(default_factory=HinweisZustand)
    datenbasis: str = DATENBASIS_KEINE
    # Fortgeschriebene Zaehler; die aufrufende Schicht haelt sie und gibt sie
    # beim naechsten Lauf wieder herein.
    takt: TaktZustand = field(default_factory=TaktZustand)
    laufzeit_mittel_min: float | None = None

    @property
    def datenbasis_empfehlung(self) -> str:
        """Datenbasis der Heizkurvenempfehlung.

        Eigene Groesse neben `datenbasis`: die eine sagt, wie sauber gerade
        gemessen wird, die andere, wie lange schon beobachtet wurde.
        """
        return self.kurve.datenbasis


def latch(aktiv: bool, wert: float, on: float, off: float) -> bool:
    """Schmitt-Trigger mit zwei Schwellen.

    Eine einzelne Schwelle laesst jede Ja-Nein-Entscheidung um sich herum
    flattern — bei einer Waermepumpe hiesse das Ein und Aus in jedem
    Abfragetakt. Die Richtung ergibt sich aus der Lage der Schwellen: liegt
    `on` unter `off`, schaltet der Trigger bei fallendem Wert ein, sonst bei
    steigendem.
    """
    if on <= off:
        if wert <= on:
            return True
        if wert >= off:
            return False
    else:
        if wert >= on:
            return True
        if wert <= off:
            return False
    return aktiv
