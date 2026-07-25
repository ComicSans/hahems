# Architektur-Review (2026-07-25)

Bewertung der Struktur, nicht des Regel-Verhaltens. Bias auf verteidigbare
Defekte (Dead Code, Duplikation, Grenzverletzungen), nicht auf stilistische
Umbauten der sorgfältig dokumentierten Regel-Logik.

## Gesamtbild

~6.200 Zeilen Python, sauber in zwei Schichten getrennt:

- **`planner.py` + `strategies/*`** — reine Funktionen, keine Home-Assistant-
  Abhängigkeit. Das ist der wertvollste architektonische Zug im Projekt: die
  komplette Regel-Logik ist ohne laufendes HA testbar (`conftest.py` erzwingt
  das sogar strukturell — ein HA-Import in einem getesteten Modul lässt den
  Import fehlschlagen). 33 Tests, alle grün, decken Kernverhalten inklusive
  Regressionen (Wallbox-Herausrechnung, Auswahl-Hysterese) ab.
- **`coordinator.py`, `actuator.py`, `config_flow.py`, `sensor.py`, …** — die
  HA-Schicht: Entities lesen, Einheiten normalisieren, Modelle lernen (Last-
  profil, WP-Heizgradstunden), den Plan anwenden. **Ungetestet** — es gibt
  keinen Test, der ohne echtes HA gegen diese Schicht läuft.

Lint-Befund (ruff, Regeln F/B/E7/W6/PLE über den ganzen Baum): **6 unbenutzte
Imports, sonst nichts.** Keine TODOs/FIXMEs, keine bare `except:`, keine
Debug-`print()`, keine offensichtlichen Bugbear-Muster. Das bestätigt den
Eindruck aus dem Code selbst: dichte Warum-Kommentare, bewusst dokumentierte
Tradeoffs (Greedy-Entladen, Lead-Hysterese, Echter-Saldo-Schutz), Regressions-
tests zu genau den Fehlern, die schon mal aufgetreten sind. Das ist überdurch-
schnittlich gepflegter Code, kein Sanierungsfall.

## Umgesetzt (risikolos, HA-frei, testabgesichert)

- `strategies/types.py`: unbenutzte Imports `time`, `timedelta`, `tzinfo`
  entfernt.
- 3× `tests/*.py`: unbenutzte Test-Imports entfernt (`storages`, `zuteilung`
  je einmal ungenutzt importiert).

Vollständige Suite nach der Bereinigung weiter grün (33 Tests).

## Befunde — nur vorgeschlagen, nicht umgesetzt

Diese Punkte betreffen die HA-Schicht: ungetestet, treibt echte Hardware
(Speicher, Wallbox, Schaltlasten). Vor jeder Änderung dort ist eine bewusste
Freigabe nötig — ein stiller Fehler würde erst am Live-System auffallen.

### 1. `coordinator.py` ist ein God Object (1.168 Zeilen, 29 Methoden)

Bündelt fünf eigentlich getrennte Zuständigkeiten in einer Klasse:
Entity-I/O (Einheiten-Normalisierung), Lastprofil-Lernen, WP-Heizgradstunden-
Modell, Wetter-Fetch (mit eigenem Cache), Orchestrierung des Update-Zyklus.
Jede einzelne Methode ist für sich verständlich und gut kommentiert — das
Problem ist die Ansammlung, nicht die einzelne Methode. Vorschlag: die drei
Lern-/Fetch-Cluster (`_refresh_load_model`/`_wp_hourly_stats`/
`_learn_wp_model`/`_meter_night_stats`/`_house_load_profile`,
`_weather_tomorrow`/`_temp_forecast_hourly`) in eigene Collaborator-Klassen
ziehen, die der Coordinator nur noch aufruft. Reduziert die Klasse auf
Orchestrierung + Entity-I/O. Risiko der Umsetzung: mittel — reine
Verschiebung ohne Verhaltensänderung, aber ungetestet und live.

### 2. Zwei echte Pure-Functions stecken in einem HA-importierenden Modul

`_parse_weekday()` (Zeile 84) und `_profile_rows()` (Zeile 95) in
`coordinator.py` sind zustandslos und HA-frei bis auf ein `dt_util`-Import in
`_profile_rows`. Weil sie aber in einem Modul stecken, das top-level
`homeassistant.*` importiert, sind sie über die bestehende Test-Infrastruktur
nicht erreichbar (`conftest.py` blockiert HA-Importe strukturell). Verschieben
nach `strategies/` (oder ein neues `hems/util.py` ohne HA-Import) würde sie
kostenlos testbar machen — kleine, klar abgegrenzte Änderung.

### 3. Fehlende Testabdeckung der HA-Schicht ist der größte Blast-Radius-Faktor

Nicht nur diese beiden Funktionen: `actuator.py`, `config_check.py`,
`config_ws.py`, `power_memory.py`, `changelog.py`, `sensor.py` und alles JS
haben keinerlei automatisierten Test. Für die Regel-Logik federt die reine
Architektur das ab (dort *ist* Testen billig); für die Aktuierungs-Schicht
nicht. Das ist kein Refactoring-Punkt, sondern eine Empfehlung: bevor an
`actuator.py`/`coordinator.py` mehr als Verschiebungen passieren, lohnt sich
ein dünner Layer von Unit-Tests für die reinen Teile (Einheiten-Umrechnung,
`_parse_weekday`, `_profile_rows`, sobald verschoben).

## Nicht verändert (bewusst)

Die Kern-Regel-Logik (Greedy-Entladen mit Auswahl-Hysterese, asymmetrische
Gains, Echter-Saldo-Schutz, Ladedeckel-Rampe) ist absichtlich so gebaut, wie
sie ist — jede dieser Entscheidungen trägt einen Kommentar, der eine frühere
Fehlbeobachtung referenziert (Schaltverschleiß, Netzbezug-Spikes,
Akku-Alterung). Eine stilistische Reorganisation dieser Module wäre kein
Architektur-Fix, sondern ein Risiko ohne Gegenwert.
