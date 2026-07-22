"""Domänengetrennte Planner-Strategien (rein-funktional, HA-frei).

Jedes Modul kapselt eine Domäne, die `planner.compute_plan` orchestriert:

- ``types``    – gemeinsame Laufzeit-Datentypen und der Schmitt-Trigger `_latch`
- ``demand``   – Verbrauchs-/Bedarfsmodell (erwartete Last, Fensterenergie)
- ``battery``  – Speicher-Saldoregelung und Akku-Schonung (Ladedeckel)
- ``forecast`` – PV-Kurve, Entladeplan, SoC-Prognose
- ``loads``    – Überschussregelung modulierbarer Lasten (Wallboxen)
- ``heating``  – witterungsgeführter Heizkreis
- ``water``    – Warmwasser-Empfehlung

Abhängigkeiten laufen nur in eine Richtung (types ← demand/battery ← forecast),
sodass keine Zyklen entstehen.
"""
