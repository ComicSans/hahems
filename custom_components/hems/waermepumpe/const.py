"""Konstanten der HA-Schicht der Wärmepumpen-Analyse.

Fachliche Schwellen stehen bewusst nicht hier, sondern bei der Regel, die sie
benutzt — in `analysis/`. Hier liegt nur, was Home Assistant selbst braucht.
"""
from __future__ import annotations

# Abfragetakt der Analyse. Bewusst feiner als der 60-s-Takt des HEMS-
# Koordinators und deshalb ein eigener Timer: ein Verdichtertakt kann kürzer
# als zwei Minuten sein, und was zwischen zwei Abfragen anläuft und wieder
# ausgeht, fehlt in der Startzählung für immer.
ABFRAGE_SEKUNDEN = 30

# Ringpuffer für die feine Auflösung. Er trägt nur, was unterhalb der Stunde
# passiert; alles Langfristige läuft über die Langzeitstatistik der
# veröffentlichten Sensoren.
RINGPUFFER_STUNDEN = 48

# Wie weit die Regression für Wärmeverlust und Heizkurve zurückschaut, und wie
# lange ein einmal geholter Statistiklauf gilt.
STATISTIK_TAGE = 60
STATISTIK_CACHE_STUNDEN = 6

# Schlüsselwörter zur Normalisierung der Betriebsart. Kühlen zählt für die
# Heizeffizienz als "aus": es ist regulärer Betrieb, aber keine Wärmeerzeugung.
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
# stillschweigend angenommenes l/min statt l/h verfälscht jeden COP um den
# Faktor 60 — dieselbe Fehlerklasse wie W gegen kW.
DURCHFLUSS_UMRECHNUNG = {
    "l/h": 1.0,
    "lph": 1.0,
    "l/min": 60.0,
    "lpm": 60.0,
    "m³/h": 1000.0,
    "m3/h": 1000.0,
}

LEISTUNG_UMRECHNUNG = {"w": 1.0, "kw": 1000.0, "mw": 1_000_000.0, "va": 1.0}

# Gründe, die HEMS als eigene Übersteuerung meldet. Die Liste ist offen nach
# oben: ein unbekannter Grund wird wie `normal` behandelt und wertet die
# Datenbasis ab, statt still verworfen zu werden.
GRUND_NORMAL = "normal"
GRUND_PV_UEBERSCHUSS = "pv_ueberschuss"
GRUND_LASTSPITZE = "lastspitze"
GRUND_SPERRE = "sperre"
