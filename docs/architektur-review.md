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
- `_parse_weekday`/`_profile_rows` aus `coordinator.py` nach `planner.py`
  verschoben (als `parse_weekday`/`profile_rows`, öffentlich). `profile_rows`
  bekommt die Zeitzone jetzt als Parameter statt `dt_util.as_local` intern zu
  rufen — macht die Funktion vollständig HA-frei. 5 neue Tests in
  `tests/test_planner_helpers.py`.

Vollständige Suite nach der Bereinigung weiter grün (38 Tests).

## Umgesetzt, aber nicht test-abgesichert (HA-Schicht, live Hardware)

**Wichtig: diese Änderung konnte in dieser Umgebung nicht gegen ein echtes
Home Assistant laufen** — hier ist kein HA installiert, und `coordinator.py`
ist strukturell von der Testsuite ausgeschlossen (siehe oben). Verifiziert
wurden nur: Syntax (`py_compile`), Lint (`ruff` F/B — sauber), ein AST-Scan
auf verwaiste Referenzen der alten privaten Namen (keine gefunden) und ein
manueller Line-by-Line-Vergleich der verschobenen Methodenkörper gegen das
Original. **Vor dem nächsten Neustart der Integration bitte einen Blick auf
die Logs werfen**, insbesondere auf Fehler beim Laden des Lastprofils/
WP-Modells oder der Wettervorhersage.

### 1. `coordinator.py` war ein God Object (1.168 Zeilen, 29 Methoden)

Bündelte fünf eigentlich getrennte Zuständigkeiten in einer Klasse:
Entity-I/O (Einheiten-Normalisierung), Lastprofil-Lernen, WP-Heizgradstunden-
Modell, Wetter-Fetch (mit eigenem Cache), Orchestrierung des Update-Zyklus.
**Umgesetzt:** die zwei Lern-/Fetch-Cluster in eigene Collaborator-Klassen
gezogen — `LoadModelLearner` (Nachtlast, 24-h-Profil, WP-Modell) und
`WeatherClient` (Wetterlage morgen, stündliche Temperaturvorhersage, je
eigener Cache). Beide bekommen ihre HA-Zugriffe (`hass`, Options-Lookup,
Device-Registry, eigene Entity-IDs) als schmale Konstruktor-Parameter statt
des ganzen Coordinators — reduziert Kopplung zusätzlich zur reinen
Verschiebung. `HemsCoordinator` schrumpft dadurch von 29 auf 17 Methoden
(681 von 1.180 Zeilen); `LoadModelLearner` 265 Zeilen/7 Methoden,
`WeatherClient` 105 Zeilen/3 Methoden. Reine Verschiebung, keine
Verhaltensänderung.

### 2. Zwei echte Pure-Functions steckten in einem HA-importierenden Modul

`_parse_weekday()` und `_profile_rows()` — siehe oben, umgesetzt.

### 3. Fehlende Testabdeckung der HA-Schicht ist der größte Blast-Radius-Faktor

Nicht nur diese beiden Funktionen: `actuator.py`, `config_check.py`,
`config_ws.py`, `power_memory.py`, `changelog.py`, `sensor.py` und alles JS
haben keinerlei automatisierten Test. Für die Regel-Logik federt die reine
Architektur das ab (dort *ist* Testen billig); für die Aktuierungs-Schicht
nicht. Das ist kein Refactoring-Punkt, sondern eine Empfehlung: bevor an
`actuator.py`/`coordinator.py` mehr als Verschiebungen passieren, lohnt sich
ein dünner Layer von Unit-Tests für die verbleibenden reinen Teile
(Einheiten-Umrechnung `_power_w`/`_energy_kwh` — bräuchte dafür ebenfalls
eine Trennung von der State-Lookup, die HA voraussetzt).

## Nicht verändert (bewusst)

Die Kern-Regel-Logik (Greedy-Entladen mit Auswahl-Hysterese, asymmetrische
Gains, Echter-Saldo-Schutz, Ladedeckel-Rampe) ist absichtlich so gebaut, wie
sie ist — jede dieser Entscheidungen trägt einen Kommentar, der eine frühere
Fehlbeobachtung referenziert (Schaltverschleiß, Netzbezug-Spikes,
Akku-Alterung). Eine stilistische Reorganisation dieser Module wäre kein
Architektur-Fix, sondern ein Risiko ohne Gegenwert.
