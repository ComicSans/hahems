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

# Kopfraum (SoC-%), den ein geräteseitiger Ziel-SoC über dem Ist-SoC braucht,
# damit das Gerät einen Ladebefehl überhaupt ausführt. Am 14.08.2026 an drei
# Zendure Hyper 2000 gemessen: L1 stand auf Ziel-SoC 27 bei Ist-SoC 27 und zog
# 0 W, während L2 und L3 mit demselben 581-W-Sollwert und Ziel-SoC 70 die volle
# Leistung nahmen. Ein Ziel-SoC auf Höhe des Ist-SoC heißt für das Gerät
# „fertig" — der Leistungs-Sollwert läuft dann ins Leere.
#
# Zwei Punkte, nicht einer: Der Ziel-SoC wird ganzzahlig geschrieben (`step: 1`
# an der Number-Entität), ein Kopfraum von 1 verschwände im Runden.
SOC_SET_KOPFRAUM = 2.0

# Ab dieser gemessenen Leistung (W, Betrag) gilt ein Speicher als arbeitend —
# in beide Richtungen. Nicht `> 0`: Die Leistungssensoren der Speicher rauschen
# um die Null, und ein stehendes Gerät meldet gelegentlich zweistellige Werte.
SPEICHER_LADEN_MIN_W = 50.0


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


@dataclass(frozen=True)
class WwPlan:
    """Ergebnis einer Warmwasser-Entscheidung.

    ``action`` ist die nötige Aktion oder ``None``. ``nicht_uebernommen``
    meldet, dass HEMS die Freigabe bereits geschrieben hat und das Gerät sie
    danach immer noch nicht zeigt.
    """

    action: WwAction | None = None
    nicht_uebernommen: bool = False


def plan_ww_action(
    *,
    status: str,
    soll_c: float | None,
    domain: str,
    state: str | None,
    schaltabstand_erreicht: bool,
    current_setpoint: float | None,
    has_setpoint_entity: bool,
    last_written_on: bool | None = None,
) -> WwPlan:
    """Nächste nötige WW-Aktion, oder ``None`` wenn nichts zu tun ist.

    Idempotent: gibt nur dann eine Aktion zurück, wenn der Ist-Zustand vom Ziel
    abweicht — kein Bus-Spam. Ein Schaltvorgang braucht zusätzlich Abstand zum
    vorigen (siehe ``schaltabstand_erreicht``); der Sollwert folgt erst im
    Zyklus nach dem Einschalten, weil das Gerät ihn vorher nicht annimmt.

    Parameter:
    - ``status``/``soll_c``: die Empfehlung aus dem Plan. ``status == "aus"``
      oder fehlender Sollwert bedeutet: WW ausschalten.
    - ``domain``: Domain des Steuer-Entitys (``water_heater``/``switch``/
      ``input_boolean``). Entscheidet, ob der Sollwert am Entity selbst
      (``set_temperature``) oder an einer Number (``set_number``) gestellt wird.
    - ``state``: Ist-Zustand des Steuer-Entitys. ``None``/``unavailable``/
      ``unknown`` → keine Aktion (Gerät nicht ansprechbar).
    - ``schaltabstand_erreicht``: ob seit der letzten Ein/Aus-Kante genug Zeit
      vergangen ist. Gilt in beide Richtungen — gegen Takten hilft nur, den
      *Wechsel* zu bremsen, nicht eine seiner Richtungen. Sollwert-Änderungen
      hängen ausdrücklich nicht daran: sie sollen dem Überschuss weiter im
      Minutentakt folgen dürfen.
    - ``current_setpoint``: aktueller Sollwert — bei ``water_heater`` das
      Attribut ``temperature``, sonst der Wert der Number-Entität.
    - ``has_setpoint_entity``: ob (in der Schalter-Variante) überhaupt eine
      Sollwert-Number konfiguriert ist. Ohne sie wird nur geschaltet.
    - ``last_written_on``: der Ein/Aus-Zustand, den HEMS zuletzt selbst
      geschrieben hat — und nur dann gesetzt, wenn er inzwischen angekommen sein
      müsste (die Frist prüft der Actuator, weil sie Zeitstempel braucht).
      ``None`` heißt „nichts bekannt": dann verhält sich die Funktion exakt wie
      vor dieser Buchführung.

    Zwei Dinge hängen daran, beide für Geräte, deren Ist-Zustand nicht zeigt,
    was HEMS geschrieben hat — gemessen am 01.08.2026 an einer LG Therma V:
    HEMS schrieb die Warmwasser-Freigabe sechsmal ein, und die Anlage fiel
    jedes Mal nach Sekunden auf „aus" zurück. Kein einziger Aus-Befehl kam von
    HEMS; sichtbar war davon trotzdem nichts.

    - Die **Meldung**: Ist der zuletzt geschriebene Zustand nach Fristablauf
      immer noch nicht der Ist-Zustand, steht ``nicht_uebernommen``.
    - Der **Rückweg**: Meldet ein Gerät nach einem HEMS-„aus" weiter „ein",
      stimmen Ziel und Ist beim Wiedereinschalten überein — geschaltet würde
      nie wieder. Weicht der zuletzt geschriebene Zustand vom Ziel ab, wird
      deshalb einmal aktiv geschrieben. Der Mindestabstand bleibt davon
      unberührt: er schützt die Hardware und wiegt schwerer als eine
      Buchführung über verlorene Befehle.
    """
    if state in (None, "unavailable", "unknown"):
        return WwPlan()

    is_on = state != "off"
    aus = status == "aus" or soll_c is None
    ziel_on = not aus

    # Nicht übernommen: HEMS hat geschrieben, die Frist ist um, und der
    # Ist-Zustand zeigt etwas anderes.
    nicht_uebernommen = last_written_on is not None and last_written_on != is_on
    # Rückweg: der zuletzt geschriebene Zustand weicht vom aktuellen Ziel ab.
    # Dann ist ein Aufruf auch dann fällig, wenn Ist und Ziel gleich aussehen.
    rueckweg = last_written_on is not None and last_written_on != ziel_on

    if aus:
        # Nur abschalten, wenn eingeschaltet UND der Mindestabstand erfüllt ist.
        if (is_on or rueckweg) and schaltabstand_erreicht:
            return WwPlan(WwAction("turn_off"), nicht_uebernommen)
        return WwPlan(None, nicht_uebernommen)

    if not is_on or rueckweg:
        # Einschalten; der Sollwert folgt erst im nächsten Zyklus, weil das
        # Gerät Befehle erst nach dem Warmup annimmt (wie die Automation).
        # Vor Ablauf des Mindestabstands passiert gar nichts — insbesondere
        # wird kein Sollwert gestellt, solange das Gerät aus ist.
        if not schaltabstand_erreicht:
            return WwPlan(None, nicht_uebernommen)
        return WwPlan(WwAction("turn_on"), nicht_uebernommen)

    # Eingeschaltet und Betrieb erwünscht: Sollwert angleichen.
    soll = int(soll_c)
    same = current_setpoint is not None and int(current_setpoint) == soll

    if domain in SETPOINT_CARRYING_DOMAINS:
        return WwPlan(
            None if same else WwAction("set_temperature", float(soll)),
            nicht_uebernommen,
        )

    # Schalter-Variante: Sollwert nur stellbar, wenn eine Number konfiguriert ist.
    if not has_setpoint_entity:
        return WwPlan(None, nicht_uebernommen)
    return WwPlan(
        None if same else WwAction("set_number", float(soll)), nicht_uebernommen
    )


def plan_soc_set(
    *,
    deckel_soc: float,
    laden_statt_einspeisen: bool,
    laedt: bool,
    ist_soc: float | None,
) -> float:
    """Geräteseitiger Ziel-SoC (%) für diesen Zyklus.

    Grundlage ist der Ladedeckel des Tages — er hält das Gerät davon ab, nach
    seinem EIGENEN Ziel weiterzuladen, wenn der Leistungs-Sollwert schon 0 ist.
    Lädt die Regelung bewusst über den Deckel hinaus, weil der Überschuss sonst
    eingespeist würde, muss der Ziel-SoC auf 100 %.

    Der Kopfraum ist der Punkt: Ein Deckel auf Höhe des Ist-SoC ist *kein*
    Deckel mehr, sondern ein Abbruchbefehl — das Gerät hält sich für fertig und
    ignoriert die zugeteilte Ladeleistung. Solange dieser Speicher laden soll,
    liegt der Ziel-SoC deshalb mindestens ``SOC_SET_KOPFRAUM`` über dem Ist.
    Soll er nicht laden, bleibt der Deckel unangetastet — sonst höbe der
    Kopfraum genau die Grenze auf, für die er da ist.
    """
    ziel = 100.0 if laden_statt_einspeisen else deckel_soc
    if laedt and ist_soc is not None:
        ziel = max(ziel, ist_soc + SOC_SET_KOPFRAUM)
    return min(100.0, max(0.0, ziel))


def speicher_laedt(gemessen_w: float | None) -> bool:
    """Ob ein Speicher messbar lädt.

    Vorzeichen wie in der Konfiguration beschrieben: positiv = entladen ins
    Haus, negativ = laden. ``None`` (kein Leistungssensor oder unbekannt) ist
    kein Nachweis fürs Laden — aber auch keiner dagegen; die Frist in
    ``actuator.py`` entscheidet, ob daraus eine Meldung wird.
    """
    return gemessen_w is not None and gemessen_w <= -SPEICHER_LADEN_MIN_W


def speicher_entlaedt(gemessen_w: float | None) -> bool:
    """Ob ein Speicher messbar entlädt — die Gegenrichtung zu ``speicher_laedt``.

    Eigene Funktion und nicht `not speicher_laedt(...)`: Zwischen den beiden
    Schwellen liegt das Rauschband um die Null, in dem der Speicher weder das
    eine noch das andere tut. Eine Negation würde genau dieses Band als
    „entlädt\" durchgehen lassen — und damit den Fall verdecken, den die
    Quittung sucht.
    """
    return gemessen_w is not None and gemessen_w >= SPEICHER_LADEN_MIN_W


def speicher_folgt(gemessen_w: float | None, *, laden: bool) -> bool:
    """Ob ein Speicher der kommandierten Richtung messbar folgt.

    Der gemeinsame Einstieg für die Quittung in ``actuator.py``: Sie prüft
    Laden und Entladen mit demselben Ablauf, nur die Richtung wechselt.
    """
    return speicher_laedt(gemessen_w) if laden else speicher_entlaedt(gemessen_w)
