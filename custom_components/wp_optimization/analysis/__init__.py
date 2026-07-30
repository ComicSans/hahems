"""Fachliche Analyse — frei von Home Assistant.

Kein Modul unterhalb dieses Pakets darf `homeassistant` importieren. Genau
das macht die Analyse ohne laufende Instanz testbar; sobald ein Modul hier
die HA-Schicht anfasst, faellt es aus der Testsuite heraus.

`types.py` importiert nur aus der Standardbibliothek und nie aus einem
anderen Analysemodul — es ist die gemeinsame Heimat der Laufzeittypen, damit
kein Importzyklus entstehen kann.
"""
