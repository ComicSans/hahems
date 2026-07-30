# CLAUDE.md

Hinweise für Claude Code (claude.ai/code) zur Arbeit in diesem Repository.

## Was das ist

**WP-Optimierung** — eine Home-Assistant-Integration, die die Effizienz einer
Wärmepumpe misst und Verbesserungen benennt. Geräteunabhängig: sie nimmt
Sensorwerte entgegen und kennt weder Protokolle noch Register.

Einer von drei Bausteinen, die sich ergänzen und einzeln funktionieren:
Energiemanagement (HEMS) misst, denkt und steuert; die Modbus-Anbindung
verbindet; dieses Repository optimiert. Verbunden über
[docs/kontrakt-v1.md](docs/kontrakt-v1.md).

## Diese Integration schreibt nie an die Anlage

Kein Aktuierungspfad, keine Steuer-Entities, kein Auto-Modus. Empfehlungen
werden veröffentlicht, umgesetzt werden sie vom Energiemanagement.

Das ist keine vorläufige Einschränkung, sondern die Trennlinie zwischen den
Repositories. Wer hier eine Schreiboperation einbaut, hebt sie auf — und
schafft die Möglichkeit, dass zwei Integrationen gleichzeitig denselben
Sollwert einer realen Heizung stellen.

## Bauen und Testen

```sh
./.venv-test/bin/python -m pytest
./.venv-test/bin/python -m pytest tests/test_thermal.py
```

Es braucht keine Home-Assistant-Instanz.

## Aufbau

Die Trennlinie, auf die es ankommt, ist **HA-freie Fachlogik gegen
HA-Schicht**:

```
custom_components/wp_optimization/
  analysis/         Fachlogik, frei von Home Assistant
    types.py        Laufzeittypen und der Schmitt-Trigger
    thermal.py      thermische Leistung, COP, Gültigkeitsprüfung
    presets.py      Kennlinien laden, Erwartung berechnen
    cycling.py      Verdichterstarts und Laufzeit
    curve.py        Wärmeverlust und Heizkurvenvorschlag
    hints.py        Hinweise mit Hysterese
    evaluate.py     Auswertelauf, reine Funktion
  presets/          Gerätekennlinien als JSON
  const.py          Konstanten der HA-Schicht
  __init__.py       HA-Schicht (derzeit Platzhalter)
```

Regeln, die das zusammenhalten:

- **Kein Modul unter `analysis/` importiert `homeassistant`.** Sobald eines
  es tut, fällt es aus der Testsuite heraus.
- **`analysis/types.py` importiert nur aus der Standardbibliothek**, nie aus
  einem anderen Analysemodul. Es ist die gemeinsame Heimat der Laufzeittypen,
  damit kein Importzyklus entstehen kann.
- Presets sind JSON und nicht YAML, damit die Fachlogik ohne Fremdpakete
  auskommt.

## Wichtige Muster

**Erst prüfen, dann mitteln.** Messwerte mit zu kleiner Spreizung, aus
Abtauung, Warmwasserbereitung oder vom Anlaufsockel werden im Abfragetakt
verworfen — *bevor* gemittelt wird. Ein Stundenmittel, in das sie eingehen,
ist unbrauchbar und nachträglich nicht mehr zu retten.

**Jede Ja-Nein-Entscheidung hat zwei Schwellen, nie eine.** `latch` in
`analysis/types.py` ist ein Schmitt-Trigger, und die Konstanten kommen paarweise
(`SPREIZUNG_NIEDRIG_AN` / `..._AUS`). Eine einzelne Schwelle lässt die
Entscheidung um sich herum flattern. Wer eine Entscheidung ergänzt, ergänzt
beide Schwellen.

**Zwei Datenbasen, nicht eine.** Die Güte der Messung und die Länge der
Beobachtung sind verschiedene Aussagen und werden getrennt geführt. In einen
Wert zusammengeworfen sähe ein sauber gemessener COP wochenlang wertlos aus.

**Presets sind modellscharf, nicht markenscharf.** Allein die Therma-V-Reihe
hat vier verschiedene Kennlinien.

**Qualitative Hinweise, keine Scheingenauigkeit.** „Umwälzpumpe drosseln"
statt „80 Prozent würden reichen" — die Pumpenkennlinie ist nicht bekannt.

## Fallstricke

**Einheiten werden gelesen, nie geraten.** Ein angenommenes l/min statt l/h
verfälscht jeden COP um Faktor 60. Fehlende oder unbekannte Einheit ist ein
Konfigurationsfehler, der angezeigt wird.

**Ein Umbenennen bricht Dashboards, Attribute brechen still.** Tragende Werte
sind Zustände, keine Attribute — Attribute sind nicht in der Registry
verankert, und eine Karte mit `state_attr(...)` wird schlicht leer.

**Der Kontrakt ist öffentliche Schnittstelle.** Eine Rolle zu entfernen,
umzubenennen oder umzudeuten erhöht die Kontraktversion.

## Barrierefreiheit

Bei einer diagrammlastigen Oberfläche kein Nachgang: kein Zustand allein über
Farbe, jedes Diagramm mit Textfassung seiner Kernaussage, Tastaturbedienung,
Kontraste nach WCAG AA auch im dunklen Erscheinungsbild.

## Offen

Die HA-Schicht: Konfigurationsdialog, Entities, Koordinator mit Ringpuffer,
Lovelace-Karte. `config_flow` steht im Manifest bewusst auf `false`, solange
es keinen Dialog gibt.
