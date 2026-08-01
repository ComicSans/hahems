"""Reine Aktuierungs-Entscheidungen (HA-frei, damit testbar).

Der Actuator (``actuator.py``) gehört zur HA-Schicht: er liest den Ist-Zustand
über ``hass`` und ruft Services auf. Die *Entscheidung*, welcher Service mit
welchem Wert nötig ist, ist davon getrennt und lebt hier als pure Funktion —
so wie die Planungsregeln in ``strategies/`` HA-frei bleiben, damit die
Testsuite sie erreicht (siehe planner-Docstring und CLAUDE.md: Aktuierung
gehört hinter Tests).

Dieses Modul importiert nur die Standardbibliothek.
"""
from __future__ import annotations

from dataclasses import dataclass

# Steuer-Domains, die den Sollwert selbst tragen (water_heater.set_temperature
# über das Attribut "temperature"). Alles andere (switch/input_boolean) schaltet
# nur ein/aus; der Sollwert läuft dann über eine separate Number-Entität.
SETPOINT_CARRYING_DOMAINS = ("water_heater",)


@dataclass(frozen=True)
class WwAction:
    """Eine einzelne geplante Warmwasser-Aktion.

    ``kind`` ist einer von ``"turn_on"``, ``"turn_off"``, ``"set_temperature"``
    (Sollwert am Steuer-Entity selbst) oder ``"set_number"`` (Sollwert an der
    separaten Number-Entität). ``value`` ist nur bei den Sollwert-Aktionen
    gesetzt (Ziel in °C).
    """

    kind: str
    value: float | None = None


def plan_ww_action(
    *,
    status: str,
    soll_c: float | None,
    domain: str,
    state: str | None,
    min_runtime_elapsed: bool,
    current_setpoint: float | None,
    has_setpoint_entity: bool,
) -> WwAction | None:
    """Nächste nötige WW-Aktion, oder ``None`` wenn nichts zu tun ist.

    Idempotent: gibt nur dann eine Aktion zurück, wenn der Ist-Zustand vom Ziel
    abweicht — kein Bus-Spam. Bildet das Verhalten der abgelösten
    WW-Automation ab (Mindestlaufzeit vor dem Abschalten, Sollwert erst nach dem
    Einschalten im Folgezyklus).

    Parameter:
    - ``status``/``soll_c``: die Empfehlung aus dem Plan. ``status == "aus"``
      oder fehlender Sollwert bedeutet: WW ausschalten.
    - ``domain``: Domain des Steuer-Entitys (``water_heater``/``switch``/
      ``input_boolean``). Entscheidet, ob der Sollwert am Entity selbst
      (``set_temperature``) oder an einer Number (``set_number``) gestellt wird.
    - ``state``: Ist-Zustand des Steuer-Entitys. ``None``/``unavailable``/
      ``unknown`` → keine Aktion (Gerät nicht ansprechbar).
    - ``min_runtime_elapsed``: ob die Mindestlaufzeit seit der letzten
      Ein/Aus-Kante abgelaufen ist (nur fürs Abschalten relevant, gegen Takten).
    - ``current_setpoint``: aktueller Sollwert — bei ``water_heater`` das
      Attribut ``temperature``, sonst der Wert der Number-Entität.
    - ``has_setpoint_entity``: ob (in der Schalter-Variante) überhaupt eine
      Sollwert-Number konfiguriert ist. Ohne sie wird nur geschaltet.
    """
    if state in (None, "unavailable", "unknown"):
        return None

    is_on = state != "off"
    aus = status == "aus" or soll_c is None

    if aus:
        # Nur abschalten, wenn eingeschaltet UND die Mindestlaufzeit erfüllt ist.
        if is_on and min_runtime_elapsed:
            return WwAction("turn_off")
        return None

    if not is_on:
        # Einschalten; der Sollwert folgt erst im nächsten Zyklus, weil das
        # Gerät Befehle erst nach dem Warmup annimmt (wie die Automation).
        return WwAction("turn_on")

    # Eingeschaltet und Betrieb erwünscht: Sollwert angleichen.
    soll = int(soll_c)
    same = current_setpoint is not None and int(current_setpoint) == soll

    if domain in SETPOINT_CARRYING_DOMAINS:
        return None if same else WwAction("set_temperature", float(soll))

    # Schalter-Variante: Sollwert nur stellbar, wenn eine Number konfiguriert ist.
    if not has_setpoint_entity:
        return None
    return None if same else WwAction("set_number", float(soll))
