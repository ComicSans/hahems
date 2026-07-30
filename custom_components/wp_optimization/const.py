"""Konstanten der HA-Schicht.

Fachliche Schwellen stehen bewusst nicht hier, sondern bei der Regel, die sie
benutzt — in `analysis/`. Hier liegt nur, was Home Assistant selbst braucht.
"""
from __future__ import annotations

DOMAIN = "wp_optimization"

# Version des Kontrakts zum Energiemanagement. Wird als eigene Entity
# veroeffentlicht, damit die konsumierende Seite pruefen kann, womit sie
# spricht. Siehe docs/kontrakt-v1.md.
KONTRAKT_VERSION = 1

# Rollen der Messeingaenge (Abschnitt A des Kontrakts).
ROLLE_VORLAUF = "vorlauf_temp"
ROLLE_RUECKLAUF = "ruecklauf_temp"
ROLLE_DURCHFLUSS = "durchfluss"
ROLLE_LEISTUNG = "leistung_elektrisch"
ROLLE_AUSSENTEMPERATUR = "aussentemperatur"
ROLLE_VERDICHTER = "verdichter_frequenz"
ROLLE_BETRIEBSART = "betriebsart"

# Rollen aus der Gegenrichtung (Abschnitt C).
ROLLE_STEUERUNG_AKTIV = "steuerung_aktiv"
ROLLE_STEUERUNG_GRUND = "steuerung_grund"

# Einheiten werden aus `unit_of_measurement` gelesen, nie geraten: ein
# stillschweigend angenommenes l/min statt l/h verfaelscht jeden COP um den
# Faktor 60.
DURCHFLUSS_UMRECHNUNG = {
    "l/h": 1.0,
    "lph": 1.0,
    "l/min": 60.0,
    "lpm": 60.0,
    "m³/h": 1000.0,
    "m3/h": 1000.0,
}
