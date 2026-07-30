# WP-Optimierung

Effizienzmessung und Verbesserungshinweise für Wärmepumpen, als
Home-Assistant-Integration.

Beantwortet die Fragen, die eine Wärmepumpe selbst nicht beantwortet: Wie gut
arbeitet sie gerade wirklich, wie gut *sollte* sie es laut Datenblatt, und
was ließe sich verbessern — läuft die Umwälzpumpe zu stark, taktet der
Verdichter zu oft, ist die Heizkurve höher als nötig.

> **Stand:** Einsatzbereit. Fachlicher Kern und HA-Schicht stehen, 65 Tests
> grün, Import gegen HA 2026.7.2 geprüft. Offen ist die eigene
> Lovelace-Karte; die Anzeige läuft zunächst über das HEMS-Panel.

## Geräteunabhängig

Es gibt rund zwanzig Wärmepumpen-Integrationen für Home Assistant, und jede
ist an einen Hersteller gebunden. Diese hier ist es nicht: Sie kennt keine
Protokolle und keine Register, sondern nimmt **fünf Sensorwerte** entgegen,
egal woher sie kommen — Vorlauf, Rücklauf, Durchfluss, elektrische Leistung
und Außentemperatur. Betriebsart und Verdichterfrequenz verbessern das
Ergebnis, sind aber nicht Pflicht.

Herstellerwissen steckt ausschließlich in den **Presets**, und die sind
Daten, keine Logik: ein weiteres Gerät ist eine weitere JSON-Datei.

## Drei Bausteine, die sich ergänzen

Jedes Repository steht für sich und funktioniert allein.

| Baustein | Aufgabe |
|---|---|
| Energiemanagement (HEMS) | messen, denken, steuern |
| Modbus-Anbindung | verbinden |
| **WP-Optimierung** | optimieren, Effizienz messen, Verbesserungen finden |

Verbunden sind sie über einen dokumentierten, versionierten Kontrakt:
[docs/kontrakt-v1.md](docs/kontrakt-v1.md). Er definiert **Rollen, keine
Entity-IDs** — Nutzende benennen Entities um, und ein Kontrakt über
`entity_id` bräche beim ersten Mal.

**Diese Integration schreibt nie an die Anlage.** Kein Aktuierungspfad, keine
Steuer-Entities. Empfehlungen werden veröffentlicht; ob sie umgesetzt werden,
entscheidet das Energiemanagement. So können zwei Integrationen sich nie um
denselben Sollwert streiten.

## Was gemessen wird

Thermische Leistung aus Durchfluss und Spreizung, daraus der COP, verglichen
mit der Erwartung aus der Gerätekennlinie. Dazu Taktung, Wärmeverlust­­
koeffizient des Hauses und ein Vorschlag für die Heizkurve.

Zwei Dinge sind dabei wichtiger, als sie klingen:

**Erst prüfen, dann mitteln.** Messwerte mit zu kleiner Spreizung, aus der
Abtauung, aus der Warmwasserbereitung oder vom bloßen Anlaufsockel sind kein
Heizbetrieb. Sie werden im Abfragetakt verworfen — *bevor* gemittelt wird. Ein
Stundenmittel, in das sie eingehen, lässt sich nachträglich nicht mehr retten.

**Jede Zahl kommt mit ihrer Datenbasis.** Vier Stufen von `keine_daten` bis
`belastbar`. Getrennt geführt werden dabei die Güte der Messung und die Länge
der Beobachtung: ein sauber gemessener COP ist sofort belastbar, eine
Heizkurvenempfehlung braucht Wochen. „Noch zu wenig Daten" ist deshalb über
längere Zeit ein regulärer Zustand und kein Fehler.

## Hinweise

Bewusst qualitativ formuliert. „Umwälzpumpe drosseln" statt „80 Prozent
würden reichen" — die Pumpenkennlinie ist hier nicht bekannt, eine
Prozentangabe wäre Scheingenauigkeit.

Jeder Hinweis hat **zwei Schwellen**, nie eine, und wird über Tage gemittelt
statt je Zyklus ausgewertet. Ein Hinweis, der im Abfragetakt kippt, ist kein
Hinweis, sondern Flackern.

## Presets

Mitgeliefert sind zehn Profile: vier Varianten der LG Therma V und sechs
generische Typen als Rückfall für unbekannte Geräte.

Der Preset-Schlüssel ist **modellscharf, nicht markenscharf**. Allein die
Therma-V-Reihe hat vier deutlich verschiedene Kennlinien; ein Profil je Marke
wäre für drei davon schlicht falsch.

Herkunft und Lizenz der Kennwerte: [ATTRIBUTION.md](ATTRIBUTION.md).

## Entwicklung

```sh
python3 -m venv .venv-test && ./.venv-test/bin/pip install pytest
./.venv-test/bin/python -m pytest
```

Es braucht keine Home-Assistant-Installation: alles unter `analysis/` ist
frei von HA-Importen und kommt mit der Standardbibliothek aus. Genau das
macht es überhaupt testbar.

## Barrierefreiheit

Bei einer diagrammlastigen Oberfläche ist das kein Nachgang: kein Zustand
allein über Farbe, jedes Diagramm mit einer Textfassung seiner Kernaussage,
Bedienung per Tastatur, Kontraste nach WCAG AA auch im dunklen
Erscheinungsbild.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
