"""Reine Helfer zum Schalter „Akkus aufladen" (T-004, T-013, T-015).

HA-frei, damit Text und Migration ohne Home Assistant testbar sind; genutzt
von `switch.py`.
"""
from __future__ import annotations

from datetime import datetime


def alte_reserve_unique_id(entry_id: str) -> str:
    """unique_id des früheren Schalters „Speicher als Notstromreserve"
    (T-004), dessen Registry-Eintrag beim Start entfernt wird."""
    return f"{entry_id}_notstromreserve"


def aufladen_hinweis(seit: datetime | None) -> tuple[str, str]:
    """Titel und Text des Dauer-Hinweises, solange „Akkus aufladen" an ist
    (T-013). `seit` ist der Einschaltzeitpunkt in Ortszeit oder None."""
    zeit = seit.strftime("%d.%m. %H:%M") if seit else "unbekannt"
    return (
        "HEMS: Akkus aufladen aktiv",
        f"„Akkus aufladen“ ist seit {zeit} eingeschaltet. HEMS lädt die "
        "Akkus mit voller Leistung, notfalls aus dem Netz, und hält sie voll, "
        "bis der Schalter ausgeschaltet wird.",
    )
