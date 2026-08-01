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

# Konfigurationsschluessel; sie entsprechen den Rollen des Kontrakts
# (Abschnitt A und C) und werden deshalb nie umbenannt.
CONF_PRESET = "preset"
CONF_VORLAUF = "vorlauf_temp"
CONF_RUECKLAUF = "ruecklauf_temp"
CONF_DURCHFLUSS = "durchfluss"
CONF_LEISTUNG = "leistung_elektrisch"
CONF_AUSSENTEMPERATUR = "aussentemperatur"
CONF_VERDICHTER = "verdichter_frequenz"
CONF_BETRIEBSART = "betriebsart"
CONF_STEUERUNG_AKTIV = "steuerung_aktiv"
CONF_STEUERUNG_GRUND = "steuerung_grund"

# Anlagenspezifische Ueberschreibung des Standby-Sockels. 0 heisst: Wert aus
# dem Preset nehmen. Der Sockel haengt an der Umwaelzpumpe der Anlage, nicht
# am Geraetemodell allein.
CONF_STANDBY_W = "standby_w"

# Abfragetakt. Muss deutlich feiner sein als ein Verdichtertakt, sonst gehen
# kurze Takte in der Zaehlung verloren.
ABFRAGE_SEKUNDEN = 30

# Ringpuffer fuer die feine Aufloesung. Er traegt nur, was unterhalb der
# Stunde passiert; alles Langfristige laeuft ueber die Langzeitstatistik der
# veroeffentlichten Sensoren.
RINGPUFFER_STUNDEN = 48

# Schluesselwoerter zur Normalisierung der Betriebsart. Kuehlen zaehlt fuer
# die Heizeffizienz als "aus": es ist regulaerer Betrieb, aber keine
# Waermeerzeugung.
BETRIEBSART_SCHLUESSEL = (
    ("abtau", "abtauen"),
    ("defrost", "abtauen"),
    ("warmwasser", "warmwasser"),
    ("brauchwasser", "warmwasser"),
    ("dhw", "warmwasser"),
    ("water", "warmwasser"),
    ("heiz", "heizen"),
    ("heat", "heizen"),
    ("kuehl", "aus"),
    ("kühl", "aus"),
    ("cool", "aus"),
    ("aus", "aus"),
    ("off", "aus"),
    ("idle", "aus"),
)

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
