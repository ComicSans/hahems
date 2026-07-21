# HEMS

<img src="assets/icon.png" alt="HEMS Icon" width="128" align="right">

Home Energy Management System als Home-Assistant-Custom-Integration.
Geräte-agnostisch: Akkus, PV-Prognosen, Warmwasser, Wärmepumpe und Wallbox werden
als Rollen über die UI konfiguriert, keine Entity-IDs im Code.

**Status: Phase 1 (Beobachten) + optionale Aktuierung.** Die Integration rechnet
Prognosen und Empfehlungen. Standardmäßig zeigt sie diese nur an (`beobachten`);
im Modus `auto` schaltet sie zusätzlich auf konfigurierte Steuer-Entitäten
(siehe [Auto-Modus](#auto-modus-aktuierung)). Konzept und Phasenplan:
[CONCEPT.md](CONCEPT.md).

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
- `select.hems_modus` (beobachten / auto / aus — siehe [Auto-Modus](#auto-modus-aktuierung))
- `select.hems_optimierungsziel` (eigenverbrauch / nulleinspeisung / vollladen —
  siehe [Optimierungsziel](#optimierungsziel))
- `switch.hems_e_auto_zwangsladung` (erzwingt die E-Auto-Ladeempfehlung, siehe
  [E-Auto: Zwangsladung](#e-auto-zwangsladung-force-loading))
- `binary_sensor.hems_konfiguration` (Config-Sanity-Check für den Auto-Modus;
  siehe [Config-Sanity-Check](#config-sanity-check))

## HEMS-Panel (Seitenleiste)

Die Integration registriert einen eigenen Eintrag **HEMS** in der HA-
Seitenleiste (`panel_custom`, dependency-freies Web-Component `hems-panel.js`).
Phase 1 ist reines Frontend auf den vorhandenen Entitäten — kein zusätzlicher
Backend-Zustand:

- **Übersicht** — bettet die Lastfluss- und Entladeplan-Karte ein (zusätzlich
  zu ihrer Nutzung in Dashboards).
- **Steuerung** — Betriebsmodus (beobachten/auto/aus), Optimierungsziel und
  E-Auto-Zwangsladung direkt schaltbar (`select`/`switch`).
- **Diagnose** — der [Config-Sanity-Check](#config-sanity-check) mit Fehlern,
  Warnungen und Überlappungen auf einen Blick.
- **Konfiguration** — Geräte-Editor direkt im Panel: Rollen mit ihren Geräten
  auflisten, hinzufügen, bearbeiten, entfernen. Die Formularfelder werden aus
  den **bestehenden** Config-Flow-Schemas abgeleitet (kein zweiter Feld-Katalog,
  keine Drift), die Entitätsauswahl ist ein eigener Picker aus `hass.states`
  (kein fragiles HA-internes Element). Gespeichert wird über WebSocket-Befehle
  (`hems/config/*`, Schreibzugriffe admin-pflichtig), die `entry.options`
  schreiben und die Integration neu laden.

Der native Options-Flow (Einstellungen → Geräte & Dienste → HEMS →
Konfigurieren) bleibt als gleichwertiger Weg erhalten; Grundwerte (Zähler,
Grundlasten, Prioritätsmodus) laufen weiterhin dort.

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
wieder aus (Hysterese); geladen wird er immer mit. Im Modus `beobachten` wird
die Empfehlung nur angezeigt; im Modus `auto` schreibt HEMS die Zuteilung auf
die Lade-/Entlade-Sollwerte der Speicher (siehe [Auto-Modus](#auto-modus-aktuierung)).

## Optimierungsziel

`select.hems_optimierungsziel` steuert zur Laufzeit, worauf die Speicher-
Regelung optimiert. Das Ziel ist unabhängig vom Prioritätsmodus (`priority_mode`
aus der Einrichtung), der nur die Reihenfolge der Überschussverteilung bestimmt.
Es wird als Attribut `ziel` an `sensor.hems_empfehlung` gespiegelt.

- **eigenverbrauch** (Standard): bisheriges Verhalten. Bezug minimieren, der
  Regel-Rest wird bewusst leicht in die Einspeisung geschoben; der Akku wird nur
  bis zur Nachtdeckung geladen (voll nur, wenn morgen wenig Ertrag erwartet
  wird).
- **nulleinspeisung**: echter Zero-Export. Der Regler hält das Netz auf einem
  kleinen Bezug (~100 W) statt auf leichter Einspeisung: gegen realen Export
  wird der Akku geladen, ein kleiner Restbezug wird toleriert statt in die
  Einspeisung ausgeregelt, am Nullpunkt bleibt er stehen (kein Zwangsbezug).
  Zusätzlich wird der Akku voll geladen, um PV-Überschuss aufzunehmen.
  Physikalische Grenze: ist der Akku voll und die PV liefert weiter mehr als das
  Haus braucht, lässt sich Einspeisung ohne PV-Abregelung (die diese
  Integration in Phase 1 nicht stellt) nicht vermeiden.
- **vollladen**: hält das Ladeziel dauerhaft auf 100 %, sonst wie
  eigenverbrauch. Das ist die manuelle Variante der automatischen
  Schlechtwetter-Vollladung (`morgen_knapp`).

## Auto-Modus (Aktuierung)

`select.hems_modus` hat drei Stufen — sie trennen **denken** (Planner),
**messen** (Coordinator) und **schalten** (Actuator):

- **beobachten**: Empfehlungen werden berechnet und geloggt, aber nicht
  ausgeführt (Standard).
- **auto**: HEMS schreibt die Empfehlung zusätzlich auf konfigurierte
  Steuer-Entitäten.
- **aus**: reiner Stopp — keinerlei Schreibzugriffe (Kill-Switch). Geräte
  behalten ihren letzten Zustand; parallele Automationen übernehmen sofort
  wieder.

Der Actuator ist bewusst konservativ: Er schreibt **nur** auf konfigurierte
Steuer-Entitäten (sonst reine Beobachtung, auch im Auto-Modus), **nur bei
Wertänderung** (idempotent, kein Bus-Spam), **nie** auf eine fehlende/unbekannte
Empfehlung, und **isoliert Fehler je Gerät**. Reihenfolge WW → WP → Akku → E-Auto.

Steuer-Entitäten je Rolle (alle optional, im Options-Flow zu setzen):

| Rolle | Empfehlung | Steuer-Entitäten | Service |
|---|---|---|---|
| Warmwasser | `ww_soll_c` + Status | `control_entity` (water_heater) | on/off + `set_temperature` |
| Wärmepumpe | `heizung.modus`/`vlt`/`leise` | `control_entity` (climate), `silent_switch_entity`, `season_select_entity` | `set_hvac_mode` + `set_temperature` + Silent + Saison |
| Speicher | `regelung` (Zuteilung je Einheit) | `charge_setpoint_entity`, `discharge_setpoint_entity`, optional `mode_entity` + `mode_charge/discharge_option` | `number.set_value` (+ `select_option`) |
| E-Auto | nur Zwangsladung | `current_entity`, `switch_entity` (Rolle „Modulierbare Last") | `number.set_value` + on/off |

Zwei Einschränkungen für das Scharfschalten:

1. **Warmwasser-Nacht-Aus braucht das Sperrfenster.** HEMS meldet WW nur während
   des konfigurierten Sperrfensters (`block_start`/`block_end`) als „aus", sonst
   „basis" (Grundtemperatur). Ohne gesetztes Fenster hält der Auto-Modus WW rund
   um die Uhr an. Das Sperrfenster ist eine feste Uhrzeit, kann die saisonale
   Tag/Nacht-Umschaltung der alten Automation also nur annähern.
2. **E-Auto: nur Zwangsladung.** HEMS modelliert (noch) keinen Überschuss-
   Ladestrom. Im Auto-Modus wird nur die Zwangsladung
   (`switch.hems_e_auto_zwangsladung`) auf `current_entity`/`switch_entity`
   geschaltet; das PV-Überschussladen bleibt bei der bestehenden Automation.
   → E-Auto-Automationen vorerst aktiv lassen.

## Config-Sanity-Check

`binary_sensor.hems_konfiguration` (device_class `problem`) prüft jeden Zyklus,
ob die Konfiguration für den Auto-Modus taugt — die Antwort auf „Kann ich
scharfschalten?". **An = Problem**: harte Fehler immer, eine Überlappung nur im
Auto-Modus (im Beobachten-/Aus-Modus sind aktive Automationen ja erwünscht).
Alles Weitere steht in den Attributen:

- `bereit_fuer_auto` — keine harten Fehler.
- `auto_schaltet` — welche Rollen der Auto-Modus tatsächlich stellt (die mit
  konfiguriertem Steuer-Entity); der Rest bleibt reine Beobachtung.
- `fehler` — der Auto-Modus würde scheitern: Steuer-Entity existiert nicht,
  falsche Domain, Richtungs-Select ohne Optionswerte.
- `warnungen` — funktioniert, aber Vorsicht: nur ein Speicher-Setpoint gesetzt,
  Warmwasser ohne Sperrfenster (24/7 an), …
- `ueberlappung` — **der Scharfschalt-Killer**: aktive Automationen, die auf
  dieselbe Steuer-Entity schreiben wie HEMS (heuristisch aus den
  `referenced_entities` der Automationen; Templates/indirekte Referenzen
  entgehen). Vor dem Auto-Modus die jeweilige Automation deaktivieren.
- `ueberlappungspruefung` — `ok` oder `nicht verfügbar` (falls HA die
  Automations-Referenzen intern nicht hergibt).

Fehler und Warnungen werden zusätzlich bei Änderung ins Log geschrieben.

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

## E-Auto: Zwangsladung (Force Loading)

`switch.hems_e_auto_zwangsladung` erzwingt die Ladeempfehlung "E-Auto laden
(Zwang)" — unabhängig von Überschuss und Wallbox-Mindestleistung. Der Zustand
wird als Attribut `ev_zwang` an `sensor.hems_empfehlung` gespiegelt.

Damit der Hausakku dabei nicht still ins Auto leerläuft, rechnet die Saldo-
Regelung die aktuelle Wallbox-Leistung (`wallbox_w`) aus dem Saldo heraus, den
sie ausregelt: Der Akku hält seinen SoC, das Zwangs-Delta kommt aus dem Netz
("Akku schonen"). Liefert die PV gerade Überschuss, lädt der Akku daraus wie
gewohnt weiter — er wird nur nicht zusätzlich für die Wallbox entladen. Im
Modus `auto` wird die Zwangsladung tatsächlich geschaltet (max. Ladestrom auf
`current_entity`, `switch_entity` an); das reguläre Überschussladen bleibt
dagegen bei der bestehenden Automation (siehe [Auto-Modus](#auto-modus-aktuierung)).
