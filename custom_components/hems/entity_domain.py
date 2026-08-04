"""Domänen-Unterschiede beim Lesen und Schalten einer Steuer-Entität.

HEMS schaltet Lasten über sehr verschiedene Entitäten: einen `switch` (SG-Ready-
Kontakt, Steckdose), ein `input_boolean` — oder eine `climate`-Entität, wenn das
Gerät nur als Thermostat in Home Assistant auftaucht. Die beiden Formen sagen
„an" auf unterschiedliche Weise:

  • switch/input_boolean: der Zustand IST `on` bzw. `off`.
  • climate: der Zustand ist der HVAC-Modus (`heat`, `cool`, `auto`, `off`).
    „An" heißt hier „irgendein Modus außer `off`" — und Einschalten heißt
    `set_hvac_mode` auf einen konfigurierten Modus, nicht `turn_on` (das ist
    optionales Geräte-Feature und kippt sonst still).

**Welcher Modus was bedeutet, sagt die Konfiguration** (`mode_heat_option`,
`mode_cool_option`) — nicht dieses Modul. Der Grund steht in `betriebsart`:
Ein Modus, den HEMS nicht zugeordnet bekommt, wird nicht geregelt. Das betrifft
vor allem `heat_cool`/`auto`, wo die Anlage selbst entscheidet, ob sie heizt
oder kühlt; HEMS kann dort weder die Heizgrenze anwenden noch beurteilen, was
ein Abschalten anrichtet.

Dieses Modul ist HA-frei: es bekommt Entity-ID und Zustandsstring und liefert
die Auswertung bzw. den zu rufenden Service. Beide Lastrollen (Schaltlast und
Heizung) benutzen es, damit eine `climate`-Entität überall gleich verstanden
wird — egal in welcher Rolle sie steckt.
"""
from __future__ import annotations

from .const import STATE_UNAVAILABLE_VALUES

# HVAC-Modus, auf den eine climate-Entität gestellt wird, wenn keiner
# konfiguriert ist. `heat` ist der einzige Modus, den jede Heizungs-Integration
# kennt; `auto` gibt es nicht überall.
DEFAULT_HEAT_MODE = "heat"

CLIMATE_OFF = "off"

# Betriebsarten, die HEMS unterscheidet. `fremd` heißt: die Anlage läuft in
# einem Modus, den die Konfiguration nicht zuordnet — HEMS regelt sie nicht.
BETRIEBSART_HEIZEN = "heizen"
BETRIEBSART_KUEHLEN = "kuehlen"
BETRIEBSART_FREMD = "fremd"


def betriebsart(
    entity: str | None,
    state: str | None,
    heat_mode: str | None = None,
    cool_mode: str | None = None,
) -> str:
    """Was die Anlage laut ihrem Zustand gerade tut.

    Für `switch`/`input_boolean` immer `heizen`: eine Schaltlast hat keinen
    Modus, und ein SG-Ready-Kontakt sperrt bestenfalls das Heizen.

    Für `climate` entscheidet der HVAC-Modus gegen die Konfiguration. Ein nicht
    zugeordneter Modus ist `fremd` — insbesondere `heat_cool`/`auto`. Das ist
    die vorsichtige Auslegung: Wer nicht weiß, ob die Anlage kühlt, darf sie im
    Hochsommer nicht als Heizung abschalten. Am 04.08.2026 tat HEMS genau das —
    eine Anlage im Modus `heat_cool` kühlte bei 39 °C Außentemperatur, und die
    Sommersperre nahm sie weg.

    `off` bekommt `heizen`, damit Frostschutz und Heizgrenze eine abgeschaltete
    Anlage weiter beurteilen können. Der Aufrufer, der den zuletzt aktiven Modus
    kennt, reicht diesen statt `off` herein (siehe Coordinator).
    """
    if domain_of(entity) != "climate":
        return BETRIEBSART_HEIZEN
    if state is None or state.lower() in STATE_UNAVAILABLE_VALUES:
        return BETRIEBSART_HEIZEN
    if state == CLIMATE_OFF:
        return BETRIEBSART_HEIZEN
    if state == (heat_mode or DEFAULT_HEAT_MODE):
        return BETRIEBSART_HEIZEN
    if cool_mode and state == cool_mode:
        return BETRIEBSART_KUEHLEN
    return BETRIEBSART_FREMD


def ziel_modus(
    art: str, heat_mode: str | None = None, cool_mode: str | None = None
) -> str:
    """Modus, auf den eingeschaltet wird — passend zur Betriebsart.

    Ohne das würde HEMS eine Anlage, die es im Kühlbetrieb abgeschaltet hat,
    auf `heat` wieder einschalten. Bei 39 °C Außentemperatur ist das kein
    Schönheitsfehler, sondern die Umkehr dessen, was das Haus braucht.
    """
    if art == BETRIEBSART_KUEHLEN and cool_mode:
        return cool_mode
    return heat_mode or DEFAULT_HEAT_MODE


def domain_of(entity: str | None) -> str:
    return entity.split(".")[0] if entity else ""


def ist_an(entity: str | None, state: str | None) -> bool:
    """Ob die Steuer-Entität eingeschaltet ist.

    Unbekannte oder nicht erreichbare Zustände zählen als „aus" — bei einem
    `switch` ist das die alte Bedeutung von ``state == "on"``, bei `climate`
    verhindert es, dass `unavailable` als „läuft" durchgeht.
    """
    if state is None or state.lower() in STATE_UNAVAILABLE_VALUES:
        return False
    if domain_of(entity) == "climate":
        return state != CLIMATE_OFF
    return state == "on"


def schalt_service(
    entity: str,
    on: bool,
    heat_mode: str | None = None,
    cool_mode: str | None = None,
    art: str = BETRIEBSART_HEIZEN,
) -> tuple[str, str, dict]:
    """(Domain, Service, Daten), um die Entität ein- oder auszuschalten.

    Bei `climate` wird `set_hvac_mode` benutzt statt `turn_on`/`turn_off`:
    `climate.turn_on` ist ein optionales Feature (`ClimateEntityFeature.TURN_ON`),
    das viele Integrationen nicht implementieren — und es stellt, wo es
    existiert, den zuletzt aktiven Modus wieder her statt eines definierten.

    `art` ist die Betriebsart, in die eingeschaltet werden soll; beim
    Ausschalten spielt sie keine Rolle.
    """
    domain = domain_of(entity)
    if domain == "climate":
        modus = ziel_modus(art, heat_mode, cool_mode) if on else CLIMATE_OFF
        return domain, "set_hvac_mode", {"hvac_mode": modus}
    return domain, f"turn_{'on' if on else 'off'}", {}
