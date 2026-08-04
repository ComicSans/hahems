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
    entity: str, on: bool, heat_mode: str | None = None
) -> tuple[str, str, dict]:
    """(Domain, Service, Daten), um die Entität ein- oder auszuschalten.

    Bei `climate` wird `set_hvac_mode` benutzt statt `turn_on`/`turn_off`:
    `climate.turn_on` ist ein optionales Feature (`ClimateEntityFeature.TURN_ON`),
    das viele Integrationen nicht implementieren — und es stellt, wo es
    existiert, den zuletzt aktiven Modus wieder her statt eines definierten.
    """
    domain = domain_of(entity)
    if domain == "climate":
        modus = (heat_mode or DEFAULT_HEAT_MODE) if on else CLIMATE_OFF
        return domain, "set_hvac_mode", {"hvac_mode": modus}
    return domain, f"turn_{'on' if on else 'off'}", {}
