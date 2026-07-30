"""Störungs- und Warnmeldungen an den Nutzer — reine Bewertung, HA-frei.

Beantwortet: Welche Meldungen sind gerade aktiv, und über welche Kanäle laufen
sie? Die Zustellung (Repair-Issue, persistente Notification, Push-Sensor) macht
der Coordinator; hier liegt nur die Logik, damit sie ohne Home-Assistant-Instanz
testbar ist (siehe CLAUDE.md — der HA-Layer ist die ungetestete Fläche).

Zwei Quellen:
  • WP-Betriebsstörung — das Rohsignal einer als Störungs-Entität konfigurierten
    Rolle (binary_sensor = an/aus, sensor = Fehlercode ≠ „ok"). Über
    aufeinanderfolgende Zyklen entprellt (Schmitt-Trigger auf Zählern), weil die
    Modbus-/ESPHome-Strecke real für einzelne Polls ausfällt.
  • Config-Fehler — die harten Fehler des Config-Sanity-Checks, aggregiert zu
    einer Meldung.

Der Reconcile-Ansatz (jeder Kandidat trägt ein `active`-Flag; der Coordinator
legt aktive an und löscht inaktive) ist restart-fest ohne persistierten
Zustand: nach einem HA-Neustart wird die volle Kandidatenmenge neu bewertet.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..const import (
    ALERT_ERROR,
    ALERT_FAULT,
    ALERT_UNAVAILABLE,
    FAULT_DEBOUNCE_OFF,
    FAULT_DEBOUNCE_ON,
    FAULT_OK_VALUES,
    STATE_UNAVAILABLE_VALUES,
)

# Rohsignal-Deutung
FAULT = "fault"  # Störsignal liegt an
CLEAR = "clear"  # eindeutig störungsfrei
UNKNOWN = "unknown"  # nicht erreichbar / unbestimmt — hält die letzte Wertung


@dataclass(frozen=True)
class FaultSignal:
    """Rohsignal einer Störungs-Entität, wie es der Coordinator abliest."""

    role_id: str
    role_name: str
    entity_id: str
    domain: str  # "binary_sensor" oder "sensor"
    raw: str | None  # aktueller Zustand; None = Entität existiert nicht


@dataclass
class FaultLatch:
    """Entprellter Störungszustand einer Rolle über die Zyklen hinweg."""

    active: bool = False
    on_count: int = 0
    off_count: int = 0
    unreachable_count: int = 0
    last_code: str = ""  # zuletzt gesehener Fehlercode/Rohwert (für die Meldung)


@dataclass(frozen=True)
class Alert:
    """Ein Meldungskandidat. `active` steuert den Reconcile im Coordinator."""

    key: str  # stabile, eindeutige ID (Issue-/Notification-Basis)
    active: bool
    severity: str
    translation_key: str  # für das lokalisierte Repair-Issue
    placeholders: dict[str, str]  # Freitext fürs Repair (translation_placeholders)
    title: str  # fertiger Text für die Notification
    message: str  # fertiger Text für die Notification


@dataclass
class AlertResult:
    alerts: list[Alert] = field(default_factory=list)
    latches: dict[str, FaultLatch] = field(default_factory=dict)


def classify(signal: FaultSignal) -> str:
    """Rohzustand einer Störungs-Entität deuten: FAULT / CLEAR / UNKNOWN."""
    raw = signal.raw
    if raw is None:
        return UNKNOWN
    val = raw.strip().lower()
    if val in STATE_UNAVAILABLE_VALUES:
        return UNKNOWN
    if signal.domain == "binary_sensor":
        # on = Störung; off = ok; alles andere ist unbestimmt.
        if val in ("on", "true"):
            return FAULT
        if val in ("off", "false"):
            return CLEAR
        return UNKNOWN
    # sensor: „ok"-Wert = keine Störung, sonst gilt der Rohwert als Fehlercode.
    return CLEAR if val in FAULT_OK_VALUES else FAULT


def advance_latch(prev: FaultLatch, signal: FaultSignal) -> FaultLatch:
    """Entprellung fortschreiben. Asymmetrisch (langsam an, schneller aus);
    UNKNOWN hält den Zustand und zählt die Nichterreichbarkeit."""
    verdict = classify(signal)
    nxt = FaultLatch(
        active=prev.active,
        on_count=prev.on_count,
        off_count=prev.off_count,
        unreachable_count=prev.unreachable_count,
        last_code=prev.last_code,
    )
    if verdict == UNKNOWN:
        nxt.unreachable_count = prev.unreachable_count + 1
        # on_count/off_count bewusst HALTEN (weder verwerfen noch weiterzählen):
        # Ein Signal, das zwischen „on" und „unavailable" flattert, würde die
        # Störungs-Entprellung sonst nie erreichen — bei jeder Runde zurück auf
        # 0 — und wäre für immer stumm, obwohl die Anlage real gestört ist.
        return nxt

    nxt.unreachable_count = 0
    if verdict == FAULT:
        nxt.off_count = 0
        nxt.on_count = prev.on_count + 1
        nxt.last_code = (signal.raw or "").strip()
        if nxt.on_count >= FAULT_DEBOUNCE_ON:
            nxt.active = True
    else:  # CLEAR
        nxt.on_count = 0
        nxt.off_count = prev.off_count + 1
        if nxt.off_count >= FAULT_DEBOUNCE_OFF:
            nxt.active = False
    return nxt


def _fault_alerts(signal: FaultSignal, latch: FaultLatch) -> list[Alert]:
    """Aus einem entprellten Zustand die Meldungskandidaten der Rolle ableiten:
    die Betriebsstörung selbst und — getrennt — die Nichterreichbarkeit."""
    name = signal.role_name
    code = latch.last_code or "—"
    # Die Nichterreichbarkeit gilt erst nach derselben Entprellung wie die
    # Störung, damit ein einzelner Poll-Aussetzer nichts meldet — und nur,
    # solange nicht ohnehin eine Störung gelatcht ist (sonst zwei Meldungen zur
    # selben Anlage; die Störung ist dann die wichtigere).
    unreachable = latch.unreachable_count >= FAULT_DEBOUNCE_ON and not latch.active
    return [
        Alert(
            key=f"wp_fault:{signal.role_id}",
            active=latch.active,
            severity=ALERT_FAULT,
            translation_key="wp_stoerung",
            placeholders={"name": name, "code": code, "entity": signal.entity_id},
            title=f"Wärmepumpe {name}: Störung",
            message=(
                f"Die Wärmepumpe „{name}“ meldet eine Störung "
                f"(Code/Status: {code}). Bitte die Anlage prüfen."
            ),
        ),
        Alert(
            key=f"wp_unreachable:{signal.role_id}",
            active=unreachable,
            severity=ALERT_UNAVAILABLE,
            translation_key="stoerung_quelle_weg",
            placeholders={"name": name, "entity": signal.entity_id},
            title=f"Wärmepumpe {name}: Störungsmeldung nicht verfügbar",
            message=(
                f"Die Störungs-Entität „{signal.entity_id}“ der Wärmepumpe "
                f"„{name}“ ist nicht erreichbar — eine echte Störung würde "
                f"gerade nicht erkannt. Bitte die Anbindung prüfen."
            ),
        ),
    ]


def _config_error_alert(errors: list[str]) -> Alert:
    """Alle harten Config-Fehler zu einer stabilen Repair-Meldung bündeln.

    Eine aggregierte Meldung mit fester ID statt N Einzel-Issues aus Freitext:
    ändert sich der Wortlaut, aktualisiert sich derselbe Eintrag, statt einen
    alten stehen zu lassen."""
    liste = "\n".join(f"• {e}" for e in errors)
    return Alert(
        key="config_error",
        active=bool(errors),
        severity=ALERT_ERROR,
        translation_key="config_fehler",
        placeholders={"anzahl": str(len(errors)), "fehler": liste or "—"},
        title="HEMS: Konfigurationsfehler",
        message=(
            "Der Auto-Modus würde mit der aktuellen Konfiguration scheitern:\n"
            f"{liste}"
        ),
    )


def evaluate(
    signals: list[FaultSignal],
    config_errors: list[str],
    prev_latches: dict[str, FaultLatch],
) -> AlertResult:
    """Volle Kandidatenmenge bewerten und die fortgeschriebenen Latches
    zurückgeben. Der Coordinator reconcilet die Alerts und hält die Latches."""
    result = AlertResult()
    for sig in signals:
        latch = advance_latch(prev_latches.get(sig.role_id, FaultLatch()), sig)
        result.latches[sig.role_id] = latch
        result.alerts.extend(_fault_alerts(sig, latch))
    result.alerts.append(_config_error_alert(config_errors))
    return result
