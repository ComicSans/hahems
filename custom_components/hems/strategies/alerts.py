"""Warnmeldungen an den Nutzer — reine Bewertung, HA-frei.

Beantwortet: Welche Meldungen sind gerade aktiv, und über welche Kanäle laufen
sie? Die Zustellung (Repair-Issue, persistente Notification, Push-Sensor) macht
der Coordinator; hier liegt nur die Logik, damit sie ohne Home-Assistant-Instanz
testbar ist.

Eine Quelle: die harten Fehler des Config-Sanity-Checks, aggregiert zu einer
Meldung. Die Überwachung von Wärmeerzeuger-Störungen ist mit der Rolle
Heizkreis entfallen — HEMS regelt Speicher, Warmwasser und Lasten und
beobachtet keine fremden Anlagen mehr.

Der Reconcile-Ansatz (jeder Kandidat trägt ein `active`-Flag; der Coordinator
legt aktive an und löscht inaktive) ist restart-fest ohne persistierten
Zustand: nach einem HA-Neustart wird die volle Kandidatenmenge neu bewertet.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..const import ALERT_ERROR


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


def evaluate(config_errors: list[str]) -> AlertResult:
    """Volle Kandidatenmenge bewerten. Der Coordinator reconcilet die Alerts."""
    return AlertResult(alerts=[_config_error_alert(config_errors)])
