# HEMS — Konzept

Warum HEMS so gebaut ist, wie es gebaut ist. Was es tut, steht im
[README](README.md); wie man es einstellt, steht im Formular selbst.

**Ziel: Autarkie zuerst.** Das Netz ist Rückfallebene, nie Optimierungsziel.

## Entscheidungen

**Eigene Integration statt EMHASS.** Eine Custom Integration läuft im selben
Prozess wie Home Assistant, kennt dessen Entities direkt und braucht keinen
zweiten Dienst, keinen zweiten Konfigurationsort und keine zweite
Fehlerquelle.

**Heuristik statt Linearprogrammierung.** Bei festem Strompreis ist die
Zielfunktion einfach: Netzbezug minimieren. Eine Heuristik bleibt dabei
erklärbar — jede Empfehlung hat eine Begründung, die im Panel steht, und
lässt sich Zeile für Zeile nachvollziehen. Ein Optimierer liefert eine Zahl
und kein Argument. Bei dynamischen Tarifen kippt diese Abwägung; dann wäre die
Entscheidung neu zu treffen.

**Rollen statt Geräte.** Im Code steht keine einzige Entity-ID. Konfiguriert
werden Rollen — Speicher, Heizkreis, modulierbare Last — und ihnen werden
Entities zugewiesen. Damit funktioniert HEMS mit jedem Fabrikat, dessen Werte
in Home Assistant ankommen, und ein weiteres Gerät ist eine weitere
Rolleninstanz statt eines Codepfads.

**Fachlogik ohne Home Assistant.** Alles unter `strategies/` und
`waermepumpe/analysis/` kommt mit der Standardbibliothek aus. Das ist der
Grund, warum die Testsuite ohne HA-Installation in gut einer Sekunde
durchläuft — und warum sie über 340 Fälle abdeckt statt einer Handvoll.

**Denken, messen und schalten sind getrennt.** `select.hems_modus` trennt
beobachten (rechnen und anzeigen), auto (zusätzlich schreiben) und aus
(Kill-Switch). Wer HEMS ausprobieren will, sieht wochenlang zu, bevor
irgendetwas geschaltet wird.

**Nie zwei Schreiber auf einem Sollwert.** Der Actuator schreibt nur auf
konfigurierte Steuer-Entities, nur bei Wertänderung, nie auf eine fehlende
Empfehlung, und isoliert Fehler je Gerät. Die Wärmepumpen-Analyse hat gar
keinen Schreibpfad — geprüft von `tests/waermepumpe/test_architektur.py`,
nicht nur zugesagt.

**Jede Ja-Nein-Entscheidung hat zwei Schwellen.** Eine einzelne Schwelle lässt
die Entscheidung um sich herum flattern; an einer Wärmepumpe hieße das Ein und
Aus in jedem Abfragetakt. `_latch` in `strategies/types.py` ist ein
Schmitt-Trigger, und die Konstanten kommen paarweise.

## Rollenmodell

Der Planner kennt keine Hersteller, nur diese Rollen. Pflicht ist genau eine:
der Zähler.

| Rolle | Anzahl | Wofür |
|---|---|---|
| Zähler | genau 1 | Momentanleistung am Netzanschluss. Die zentrale Regelgröße. |
| PV-Prognose | 0..n | je eine Dachfläche oder Ausrichtung |
| Speicher | 0..n | SoC, Kapazität, Reserve, Lade- und Entladegrenzen, Sollwert-Entities |
| Warmwasser | 0..n | Temperatur, Basis- und Komfort-Soll, Sperrzeiten, Legionellenschutz |
| Heizkreis | 0..n | witterungsgeführte Modus- und Vorlaufempfehlung |
| Schaltbare Last | 0..n | An/Aus mit Taktschutz |
| Modulierbare Last | 0..n | stufenlos regelbar, etwa eine Wallbox |
| Wärmepumpen-Analyse | 0..n | misst Effizienz, schreibt nie |

Mehrere Speicher werden zu einem virtuellen Gesamtspeicher aggregiert;
Sollwerte verteilt der Regler proportional zu freier Kapazität und Leistung.
Reserven bleiben Parameter des einzelnen Geräts.

## Aufbau

```
custom_components/hems/
  coordinator.py    liest Entities, ruft den Planner, ruft den Actuator
  planner.py        rollierender Plan, reine Funktion
  strategies/       die Fachlogik, frei von Home Assistant
  waermepumpe/      Effizienzanalyse
    analysis/       ebenfalls HA-frei, ohne jeden Schreibpfad
    presets/        Gerätekennlinien als JSON
  actuator.py       der einzige Ort, an dem geschrieben wird
  frontend/         Panel und zwei Lovelace-Karten
```

Die Trennlinie, auf die es ankommt, verläuft zwischen HA-Schicht und
Fachlogik. Der Planner ist eine reine Funktion: Der Koordinator reicht den
Zustand des letzten Laufs hinein und übernimmt den neuen. Deshalb lässt sich
jede Regel gegen einen konstruierten Zustand prüfen, ohne eine Anlage.

**Prognose.** Fremde PV-Prognosen werden aggregiert und gegen ein gelerntes
Lastprofil gerechnet. Primärquelle ist der rekonstruierte Hausverbrauch, aus
dem ein 24-Stunden-Profil je Wochentagstyp entsteht; fällt die Historie noch
aus, greift das Nachtprofil aus dem rohen Zähler, zuletzt die konfigurierte
Grundlast. HEMS rechnet **keine eigene PV-Prognose** — dafür gibt es
Integrationen, die es besser können.

**Plan.** Rollierend in 15-Minuten-Schritten über 24 bis 48 Stunden. Je Slot:
Warmwasser-Basis, dann Hausverbrauch, dann in prognoseabhängiger Reihenfolge
Warmwasser-Komfort, Speicher und modulierbare Lasten, Einspeisung als Rest.

## Regeln, deren Begründung nicht offensichtlich ist

**Warmwasser hat Priorität 1.** Der Speicher ist der billigste Puffer im
System: ein Kelvin Speicherhub kostet nichts an Zyklenfestigkeit, ein
Batteriezyklus schon. Zwei Sollwerte — Basis wird immer gehalten, notfalls aus
dem Netz; Komfort nur bei Überschuss.

**Der Ladedeckel über den Tag.** Kalendarische Alterung ist bei hohem SoC am
größten. Tagsüber wird deshalb nur bis zu einem Zwischenstand geladen; erst
vor Sonnenuntergang steigt der Deckel, sodass der Speicher zur Nacht voll ist
und möglichst wenig Zeit bei 100 % verbringt.

**Modulierbare Lasten weichen vor dem Speicher.** Lässt der Ertrag nach, wird
zuerst der Ladestrom heruntergeregelt, erst danach hilft der Akku. Sonst
finanzierte der Speicher das Laden.

**Der Taktschutz senkt die Startrate, nicht die Taktlänge.** Reißt die
Startzahl im rollierenden Fenster die Schwelle, empfiehlt HEMS eine
Zwangspause — die Anlage bekommt eine echte Ruhephase statt der vier Minuten
ihrer eigenen Wiederanlaufsperre. Der einzelne Takt wird davon nicht länger.

**Die Taupunkt-Untergrenze im Kühlbetrieb hebt an, sie senkt nie.** An einer
Flächenkühlung schlägt sich Wasser nieder, sobald die Oberfläche den Taupunkt
unterschreitet. Die Vorlauftemperatur ist dabei nicht die
Oberflächentemperatur — deshalb ein konfigurierbarer Sicherheitsabstand und
keine Grenze exakt auf dem Taupunkt.

**Die Kurvenübernahme ist dreifach gebremst.** Siehe
[docs/waermepumpen-analyse.md](docs/waermepumpen-analyse.md): Die Empfehlung
entsteht aus Betrieb, den HEMS mit der vorigen Empfehlung selbst erzeugt hat.

## Was bewusst fehlt

- **Eigene PV-Prognose.** Siehe oben.
- **Dynamische Stromtarife.** Die Heuristik setzt einen festen Preis voraus.
  Mit variablem Preis ist „Netzbezug minimieren" nicht mehr dasselbe wie
  „Kosten minimieren", und die Zielfunktion wäre neu zu denken.
- **Ein eigener Langzeitspeicher.** Die Statistik von Home Assistant trägt
  das; eine Kopie davon wäre eine zweite Wahrheit.
- **Automatische Erkennung von Geräten.** Welche Entity welche Rolle spielt,
  weiß nur, wer die Anlage kennt. Geraten wäre schlimmer als gefragt.

## Standortannahmen

HEMS rechnet mit Sonnenauf- und -untergang aus Home Assistant und ist damit
vom Standort unabhängig. Eine Ausnahme gibt es: Die **Vorbelegung** der
Sommersperre im Formular ist die Nordhalbkugel (Mai bis September). Liegt die
konfigurierte Breite südlich des Äquators, schlägt das Formular November bis
März vor. Die Regel selbst überspannt den Jahreswechsel in beide Richtungen,
und ändern lässt sie sich immer.

## Stand

Beobachten, Warmwasser, Speicher, Lasten, Heizkreis und die
Wärmepumpen-Analyse sind gebaut und laufen. Offen ist ein Simulationsdienst,
der die Vergangenheit mit virtuellen Speichergrößen durchrechnet
(Autarkiegrad, Eigenverbrauchsquote, vermiedener Netzbezug) — die Rechenkerne
dafür stehen in `tests/simulate.py`, ein Dienst darum herum nicht.

## Referenzinstallation

Kein Teil des Konzepts, sondern die Anlage, an der HEMS entwickelt und
gemessen wird. Sie sagt, welche Kombination wirklich erprobt ist — und dass
alles andere aus dem Rollenmodell folgt, nicht aus Erfahrung.

- PV mit drei Prognoseflächen (Ost, Süd, West)
- drei Speicher, einer je Phase, zusammen 11,52 kWh
- LG Therma V R290 Monobloc, über
  [Modbus RTU](https://github.com/ComicSans/lg-therma-v-esphome-modbus)
  angebunden, mit getrennter Sensorik für Heizen und Warmwasser
- Wallbox mit Ampere-Steuerung, dreiphasig, ohne Zugriff auf den Auto-SoC
- Hauptzähler-Leistung nach OBIS 16.7.0 als einzige Regelgröße
- fester Strompreis

Was daran nicht repräsentativ ist: Der Wasserdurchfluss der Wärmepumpe ist
über Modbus nicht erreichbar, ein COP lässt sich an dieser Anlage also nicht
rechnen. Die Wärmepumpen-Analyse braucht dafür einen eigenen
Volumenstromsensor.
