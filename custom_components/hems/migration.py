"""Options-Migration: Gerätelisten alter Schemas auf das aktuelle heben.

Bewusst HA-frei und von `async_migrate_entry` getrennt. Die Migration ist die
einzige Stelle, an der HEMS bestehende Nutzerkonfiguration umschreibt — ein
Fehler hier kostet Einstellungen, die niemand mehr rekonstruieren kann. Als
reine Listen-Transformation ist sie ohne Home Assistant testbar; im Paket
bleibt nur das Schreiben in den ConfigEntry.
"""
from __future__ import annotations

from .const import ROLE_HEATING, ROLE_SWITCHABLE

# Rollen, die es nicht mehr gibt (Schritt 2 → 3). Ihre Einträge werden ohnehin
# nicht mehr gelesen, blieben aber sonst als unbekannte Geräte im Panel stehen.
ENTFALLENE_ROLLEN = ("heating_circuit", "heat_pump_analysis")


def migriere_geraete(devices: list[dict], von_version: int) -> list[dict]:
    """Gerätelisten-Migration bis Schema 4. Gibt eine neue Liste zurück.

    1 → 2: Bis dahin galten *alle* schaltbaren Lasten als Wärmepumpe; bestehende
    Einträge bekommen `heat_coupled` auf True, damit sich ihr Verhalten durch
    die Einführung des Flags nicht ändert.

    2 → 3: Die Rollen Heizkreis und Wärmepumpen-Analyse sind entfallen.

    3 → 4: `heat_coupled` wird zur eigenen Rolle Heizung. Das Flag versprach im
    Hilfetext ein Heizgradstunden-Modell, das mit Schritt 3 verschwunden war;
    übrig blieben ein Label im Lastfluss und ein höherer Lern-Boden. Wer es
    gesetzt hatte, meinte einen Wärmeerzeuger — und bekommt jetzt einen, samt
    Frostschutz, Sommersperre und Heizkurve.

    Die Geräte-`id` bleibt dabei erhalten: An ihr hängt die gelernte
    Leistungsaufnahme (`power_memory`). Eine neue id würde die Anlage auf den
    2-kW-Fallback zurückwerfen, und HEMS lernte wochenlang neu, was es schon
    wusste. Alles Übrige — Schalter, Mindestzeiten, Priorität — heißt in der
    neuen Rolle genauso und wird unverändert übernommen. Die Frostschutzwerte
    kommen aus den Defaults der Rolle; das ist die einzige Stelle, an der die
    Migration etwas erfindet, und sie erfindet dort in die sichere Richtung.
    """
    devices = [dict(d) for d in devices]

    if von_version < 2:
        for device in devices:
            if device.get("role") == ROLE_SWITCHABLE and "heat_coupled" not in device:
                device["heat_coupled"] = True

    devices = [d for d in devices if d.get("role") not in ENTFALLENE_ROLLEN]

    for device in devices:
        # `pop` in der Bedingung: Das Flag verschwindet in beiden Fällen, aber
        # nur ein gesetztes macht aus der Last eine Heizung.
        if device.get("role") == ROLE_SWITCHABLE and device.pop("heat_coupled", False):
            device["role"] = ROLE_HEATING
        device.pop("heat_coupled", None)

    return devices
