# HEMS

<img src="assets/icon.png" alt="HEMS Icon" width="128" align="right">

Home Energy Management System als Home-Assistant-Custom-Integration.
Geräte-agnostisch: Akkus, PV-Prognosen, Warmwasser, Wärmepumpe und Wallbox werden
als Rollen über die UI konfiguriert, keine Entity-IDs im Code.

**Status: Phase 1 (Beobachten).** Die Integration rechnet Prognosen und
Empfehlungen, steuert aber noch nichts. Konzept und Phasenplan: [CONCEPT.md](CONCEPT.md).

> **Breaking Change (0.6.0):** `sensor.hems_einspeiseplan` heißt jetzt
> `sensor.hems_entladeplan` — „Einspeisung" meinte fälschlich Netzeinspeisung,
> gemeint war aber immer die Akku-Entladung ins Haus. Wer die Entität in
> Lovelace-Karten, Templates oder Automationen referenziert, muss den Namen
> nach dem Update manuell anpassen; die alte Entität bleibt sonst als
> „nicht verfügbar" in der Entity-Registry zurück und sollte gelöscht werden.

## Installation

Variante HACS: dieses Repo als Custom Repository (Typ "Integration") hinzufügen.

Variante manuell: den Ordner `custom_components/hems/` in das
`config/custom_components/`-Verzeichnis der HA-Instanz kopieren und HA neu starten.

## Einrichtung

1. Einstellungen → Geräte & Dienste → Integration hinzufügen → "HEMS"
2. Zähler-Entität (Momentanleistung am Netzanschluss, W) und Grundlast angeben
3. Danach über "Konfigurieren" die Geräte anlegen:
   PV-Prognoseflächen, Speicher, Warmwasser, Heizkreis,
   schaltbare/modulierbare Lasten

## Entitäten (Phase 1)

- `sensor.hems_pv_heute` / `hems_pv_rest_heute` / `hems_pv_morgen` (kWh, alle Flächen summiert)
- `sensor.hems_pv_leistung_jetzt` (W, geschätzt)
- `sensor.hems_saldo` (W, normalisiert: positiv = Netzbezug)
- `sensor.hems_hausverbrauch` (W, PV + Batterie-Entladung + Netzbezug —
  derselbe Wert wie der Haus-Knoten der Lastfluss-Karte)
- `sensor.hems_nachtdefizit` (kWh, erwarteter Verbrauch Sonnenuntergang → Sonnenaufgang)
- `sensor.hems_ueberschuss_rest_heute` (kWh, Prognose)
- `sensor.hems_speicher_soc` / `hems_speicher_verfuegbar` / `hems_speicher_ziel_soc`
- `sensor.hems_empfehlung` (Text; Details als Attribute, u.a. das gelernte
  24-h-Lastprofil je Wochentagstyp und dessen Quelle `lastprofil_quelle`)
- `sensor.hems_lastfluss` (W, Hausverbrauch; alle Flusswerte als Attribute)
- `sensor.hems_entladeplan` (W, geplante Speicher-Entladung ins Haus jetzt —
  nicht zu verwechseln mit echter Netzeinspeisung, siehe `hems_saldo`;
  Stunden-Slots, SoC-Prognose, PV-Stundenkurve, Warmwasser-Sperr- und
  Legionellen-Fenster sowie die Status der Regelungen als Attribute)
- `sensor.hems_warmwasser_soll` (°C, empfohlener WW-Sollwert; Status
  aus/legionellenschutz/pv_boost/basis als Attribut)
- `sensor.hems_speicher_regelung` (Modus der Saldo-Regelung
  entladen/laden/pausiert; Soll-Leistung und Zuteilung je Speicher als
  Attribute)
- `sensor.hems_heizkreis` (Modus-Empfehlung heizen/kuehlen/aus;
  Vorlauf-Soll, Außentemperatur und Flüster-Empfehlung als Attribute)
- `select.hems_modus` (beobachten / aus)

## Lastfluss-Karte

Die Integration liefert eine eigene Lovelace-Karte mit und registriert sie
automatisch — keine Ressourcen-Konfiguration nötig. Im Dashboard einfach
hinzufügen ("HEMS Lastfluss" im Karten-Picker) oder per YAML:

```yaml
type: custom:hems-flow-card
entity: sensor.hems_lastfluss   # optional, das ist der Default
title: Lastfluss                # optional
height: 440                     # optional, px; "auto" = inhaltsabhängig
```

Die Karte zeigt animierte Flüsse zwischen PV, Netz, Batterie und Haus;
Wärmepumpe und Wallbox erscheinen als Chips, sobald für sie eine
Power-Entität konfiguriert ist. Die Aufteilung auf die Kanten folgt der
Merit-Order des Planners (PV → Haus → Akku → Einspeisung).

Konventionen: `netz_w` positiv = Netzbezug, `batterie_w` positiv = Entladen.
Liefert der Speicher ein umgekehrtes Vorzeichen, hilft ein Template-Sensor.
Die PV-Leistung stammt aus der Prognose und ist als "geschätzt" markiert.

## Entladeplan-Karte

Zweite mitgelieferte Karte ("HEMS Entladeplan" im Karten-Picker):

```yaml
type: custom:hems-plan-card
entity: sensor.hems_entladeplan   # optional, das ist der Default
title: Entladeplan                # optional
height: 440                       # optional, px; "auto" = inhaltsabhängig
pv_entity: sensor.…               # optional, Verlaufsquelle PV-Leistung
soc_entity: sensor.…              # optional, Verlaufsquelle Speicher-SoC
```

`pv_entity`/`soc_entity` sind nur nötig, wenn der gemessene Verlauf aus
anderen Entitäten kommen soll als den HEMS-eigenen — die meldet die
Integration der Karte von selbst.

Beide Karten haben dieselbe Standardhöhe (440 px) und sind damit in jedem
Dashboard-Layout gleich hoch — auch im Masonry-Layout, wo sich Karten sonst
nach ihrem eigenen Seitenverhältnis richten. Wer das nicht will, setzt
`height: auto` (oder einen eigenen Wert) in beiden Karten.

Der Zeitstrahl umfasst den kompletten heutigen und den kompletten morgigen
Kalendertag: orange die geschätzte PV-Stundenkurve (Tagesenergie sinusförmig
über das Sonnenfenster verteilt), grün die geplante nächtliche Entladung
(Akku-Abgabe ins Haus, nicht Netzeinspeisung). Eine rote Linie markiert
"jetzt".

Für die bereits vergangenen Stunden des heutigen Tages holt die Karte den
tatsächlich **gemessenen** Verlauf von PV-Leistung und Speicher-SoC per
WebSocket aus dem Recorder nach (alle 5 Minuten, ab lokal 00:00). Die
PV-Messwerte werden dabei zeitgewichtet auf Stundenmittel verdichtet, damit
sie im selben Raster wie die Prognosebalken stehen; zeitgewichtet deshalb,
weil der Recorder bei Zustandsänderung schreibt und ein ungewichtetes Mittel
ruhige Phasen unterschlagen würde.

Messwerte sind kräftiger gezeichnet als die Prognose, der SoC durchgezogen
statt gestrichelt — so bleibt Prognose und Realität nebeneinander ablesbar.
Der Vergangenheitsbereich bleibt dezent hinterlegt. Ist der Verlauf nicht
abrufbar (Recorder deaktiviert, Quellen nicht auflösbar, ältere HA-Version
ohne die WebSocket-API), bleibt der Bereich leer und die Karte sagt "Verlauf
nicht verfügbar" — die Prognosedarstellung funktioniert unabhängig davon.

Gestrichelt liegt darüber die SoC-Prognose: ein stündlicher Vorwärtslauf ab
jetzt, bei dem Überschuss lädt (begrenzt durch Ladeleistung und Kapazität) und
Defizit bis zur Reserve entlädt.

Das Band am unteren Rand zeigt die Warmwasser-Verfügbarkeit: türkis heißt
freigegeben, grau eine konfigurierte Sperrzeit (siehe unten), violett das
wöchentliche Legionellenschutz-Fenster mit erhöhtem Sollwert.

Unter dem Diagramm fassen Chips die aktuellen Empfehlungen zusammen:
PV-Rest und Morgen-Prognose, Entlade-Budget, der empfohlene
WW-Sollwert samt Status (Basis / PV-Boost / Legionellenschutz / aus), der
Modus der Speicher-Saldo-Regelung (inkl. Hinweis "Kaltreserve", wenn ein
Reserve-Speicher mit entlädt) und die Heizkreis-Empfehlung mit
Vorlauf-Soll. Dieselben Status-Chips zeigt auch die Lastfluss-Karte.

Die Nachtlast je Stunde stammt aus einem gelernten Lastprofil (14 Tage
Zähler-Statistik); reicht das Akku-Budget nicht für die ganze Nacht, werden
alle Stunden proportional reduziert, damit der Speicher bis Sonnenaufgang
durchhält.

## Warmwasser-Sperrzeiten

Beim Warmwasser-Gerät lassen sich "Sperrzeit ab" und "Sperrzeit bis" angeben
(Konfigurieren → Warmwasser). In diesem Fenster empfiehlt der Planner weder
Basis- noch Komfortladung — der Speicher darf also bis zum Ende der Sperre
unter die Basistemperatur auskühlen, statt aus dem Netz nachzuheizen.

Liegt das Ende vor dem Anfang, läuft das Fenster über Mitternacht: `18:00` bis
`06:00` sperrt jede Nacht von abends bis morgens. Beide Felder leer (oder zwei
gleiche Zeiten) bedeutet keine Sperre.

## Warmwasser: PV-Boost und Legionellenschutz

Der Planner orchestriert den WW-Sollwert nach Priorität
**Legionellenschutz > PV-Boost > Basis**; in der Sperrzeit ist Warmwasser
aus (`sensor.hems_warmwasser_soll` wird `unbekannt`, Status `aus`).

- **Basis:** Der Basis-Sollwert wird immer gehalten, notfalls aus dem Netz.
- **PV-Boost:** Aufheizen auf den Komfort-Sollwert wird nur empfohlen, wenn
  der Gesamt-Speicher fast voll ist **und** kräftig eingespeist wird. Beide
  Schwellen (Speicher-SoC und Netzsaldo) haben je ein Ein- und ein
  Aus-Niveau (Hysterese) und sind am Warmwasser-Gerät konfigurierbar.
- **Legionellenschutz:** Ein wöchentliches Fenster (Wochentag + Uhrzeiten),
  in dem der Sollwert unabhängig vom Überschuss auf das Legionellen-Soll
  (Standard 60 °C) angehoben wird — Hygiene geht vor. Das Fenster erscheint
  violett im WW-Band der Plan-Karte.

## Speicher: Saldo-Regelung (Empfehlung)

Aus Netzsaldo und gemessener Speicherleistung berechnet der Planner eine
Regel-Empfehlung je Speicher (`sensor.hems_speicher_regelung`):
Proportionalregler mit Priorität "Bezug minimieren" — schnell gegen teuren
Netzbezug, gemächlich beim Laden, Sollwert leicht in die Einspeisung
verschoben, Totband gegen Dauerkorrekturen. Entladen wird proportional zur
verfügbaren Energie oberhalb der Reserve verteilt, Laden proportional zur
freien Kapazität. Speicher ohne SoC-Wert fallen aus der Zuteilung.

Ein als **Kaltreserve** markierter Speicher entlädt erst mit, wenn der
mittlere SoC der übrigen unter 40 % fällt, und scheidet oberhalb von 45 %
wieder aus (Hysterese); geladen wird er immer mit. In Phase 1 wird die
Empfehlung nur angezeigt, nicht ausgeführt.

## Heizkreis (Wärmepumpe)

Die Rolle "Heizkreis" liefert eine Modus-Empfehlung aus der Außentemperatur
(heizen unter / aus über bzw. kühlen über / aus unter, jeweils mit
Hysterese) plus einen witterungsgeführten Vorlauf-Sollwert
(`sensor.hems_heizkreis`). Die Heizkurve (Fußpunkt bei 0 °C, Steigung,
Min/Max) ist konfigurierbar; eine optionale Wärmeanforderungs-Entität
(0–100 %, z. B. PID-Thermostate per Template kombiniert) hebt die Kurve um
bis zu 5 K an — ohne Anforderung fällt der Vorlauf auf das Minimum
(Absenkbetrieb). In den Sperrmonaten (Standard Mai–September) wird Heizen
nie empfohlen. Bei niedrigem Vorlauf-Soll meldet das Attribut
`leise_empfohlen`, dass der Flüsterbetrieb der Anlage reicht.

## Wärmepumpe in der Bedarfsprognose

Ist ein Heizkreis konfiguriert und hat die Wärmepumpe (schaltbare Last)
eine Leistungs-Entität, lernt HEMS ein temperaturabhängiges
WP-Verbrauchsmodell aus 45 Tagen Langzeitstatistik:
`P = Basis + k × (Heizgrenze − Außentemperatur)`. Die Basis ist die
mittlere WP-Leistung oberhalb der Heizgrenze (Warmwasser, Standby), k die
gelernte Steigung in W/K; gedeckelt auf die historisch beobachtete
Spitzenleistung. Solange die Historie nicht reicht, überbrückt ein
Richtwert (40 W/K, Attribut `quelle: richtwert` statt `gelernt`).

Das Lastprofil wird dann WP-bereinigt gelernt und die WP stattdessen
explizit je Stunde aufgeschlagen — mit der Temperatur aus der stündlichen
Wettervorhersage (Fallback: aktuelle Außentemperatur). Damit reagieren
Nachtdefizit, Ziel-SoC, Entladeplan und SoC-Prognose sofort auf
Kälteeinbrüche, statt dem 28-Tage-Mittel wochenlang hinterherzulaufen.
Während der Sommersperre zählt nur die Basisleistung.

Transparenz: `sensor.hems_nachtdefizit` weist den WP-Anteil als Attribut
`wp_anteil_kwh` aus, `sensor.hems_heizkreis` das gelernte Modell unter
`verbrauchsmodell`. Ohne Heizkreis oder ohne WP-Leistungs-Entität bleibt
alles beim alten Verhalten (WP implizit im Lastprofil).

## E-Auto: Mindestladeleistung der Wallbox

Die Empfehlung "E-Auto mit Überschuss" prüft, ob der Momentanüberschuss die
physikalische Mindestladeleistung der konfigurierten modulierbaren Last
erreicht (`min_a × Phasen × 230 V`) — darunter könnte die Wallbox den
gemeldeten Überschuss real gar nicht abnehmen. Die Ein-Schwelle liegt mit
200 W Sicherheitsmarge über diesem Minimum, die Aus-Schwelle am nackten
Minimum (Hysterese), damit die Empfehlung nicht bei jedem Wolkenschatten
kippt. Ist keine modulierbare Last konfiguriert, gilt weiterhin das alte
Verhalten: jeder Überschuss über 200 W genügt für die Empfehlung.
