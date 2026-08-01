"""Wärmepumpen-Analyse: Effizienz messen und Verbesserungen benennen.

Beantwortet die Fragen, die eine Wärmepumpe selbst nicht beantwortet: wie gut
sie gerade wirklich arbeitet, wie gut sie es laut Datenblatt sollte, und was
sich verbessern ließe.

Herkunft: bis August 2026 die eigenständige Integration `wp_optimization`.
Die Trennung hatte genau einen Zweck — nie zwei Schreiber auf demselben
Sollwert. Den erfüllt hier `analysis/` als Paket ohne jeden Schreibpfad,
geprüft durch `tests/waermepumpe/test_architektur.py`.

Aufbau:

    analysis/   Fachlogik, frei von Home Assistant
    presets/    Gerätekennlinien als JSON

Die Rollen, unter denen die Ergebnisse veröffentlicht werden, stehen in
`docs/waermepumpen-analyse.md`. Sie sind eine öffentliche Schnittstelle:
Automationen und Dashboards hängen daran.
"""
