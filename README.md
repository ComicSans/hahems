# HEMS

<img src="https://raw.githubusercontent.com/ComicSans/hahems/main/assets/icon.png" alt="HEMS Icon" width="128" align="right">

Home Energy Management System als Home-Assistant-Custom-Integration.

HEMS prognostiziert PV-Ertrag und Verbrauch, plant daraus Speicher, Warmwasser
und Lasten und zeigt jederzeit an, was es warum empfiehlt. Auf Wunsch schaltet
es die Geräte auch selbst. Der Schwerpunkt liegt auf dem Akku: wann er lädt,
wie weit, und wer ihm den Überschuss streitig machen darf.

**Geräte-agnostisch:** Du konfigurierst *Rollen* — „Speicher“, „Warmwasser“,
„modulierbare Last“ — und weist ihnen deine Entitäten zu. Im Code steht keine
einzige Entity-ID, also funktioniert HEMS mit jedem Fabrikat, dessen Werte in
Home Assistant ankommen.

## Voraussetzungen

| | |
|---|---|
| **Home Assistant** | 2024.12 oder neuer |
| **Zähler** | Eine Entität mit der momentanen Leistung am Netzanschluss in Watt. Das ist die einzige Pflichtangabe und die zentrale Regelgröße. |
| **PV-Prognose** | Eine separate Integration wie [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) oder Solcast. HEMS rechnet keine eigene Prognose, es liest deren Sensoren. |
| **Recorder** | Für gelernte Lastprofile und den Messverlauf in der Plan-Karte. Standardmäßig aktiv. |

Alles Weitere — Speicher, Warmwasser, Lasten — ist optional. HEMS
läuft auch mit reinem Zähler und PV-Prognose und liefert dann eben nur die
Empfehlungen, die es aus diesen Daten ableiten kann.

## Installation

**Über HACS:** dieses Repository als Custom Repository vom Typ „Integration“
hinzufügen, dann installieren und Home Assistant neu starten.

**Manuell:** den Ordner `custom_components/hems/` in das Verzeichnis
`config/custom_components/` der HA-Instanz kopieren und neu starten.

## Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → „HEMS“**
2. Zähler-Entität und Grundlast angeben. Mehr fragt die Einrichtung nicht ab.
3. Im **HEMS-Panel** der Seitenleiste unter **Konfiguration** die Geräte als
   Rollen anlegen: PV-Prognoseflächen, Speicher, Warmwasser, Heizung sowie
   schaltbare und modulierbare Lasten. Dort stehen sie alle auf einer Seite und
   lassen sich jederzeit ändern, ergänzen und entfernen. Alternativ über
   **Konfigurieren** an der Integration.
4. `binary_sensor.hems_konfiguration` prüfen — er sagt dir, ob die
   Konfiguration für den Auto-Modus taugt.

Jedes Feld ist im Formular selbst erklärt — Label und Hilfetext stehen direkt
am Eingabefeld, im Options-Flow wie im HEMS-Panel.

## Betriebsmodi

`select.hems_modus` trennt **denken**, **messen** und **schalten**:

| Modus | Verhalten |
|---|---|
| **beobachten** | Standard. Empfehlungen werden berechnet und angezeigt, aber nichts geschaltet. |
| **auto** | HEMS schreibt die Empfehlungen zusätzlich auf die konfigurierten Steuer-Entitäten. |
| **invers-auto** | Wie **auto**, nur der Richtungs-Select des Speichers wird verkehrt herum gestellt: Laden → Ausgangsmodus, Entladen → Eingangsmodus. Für Geräte, deren Ein-/Ausgangsmodus vertauscht beschriftet ist. Die Lade-/Entlade-Sollwerte bleiben unverändert. |
| **aus** | Kill-Switch. Keinerlei Schreibzugriffe. Geräte behalten ihren letzten Zustand, parallele Automationen übernehmen sofort wieder. |

> **Der Auto-Modus schaltet echte Hardware.** Vor dem Scharfschalten:
> `binary_sensor.hems_konfiguration` muss `bereit_fuer_auto` melden, und
> bestehende Automationen auf denselben Entitäten müssen deaktiviert sein — das
> Attribut `ueberlappung` zeigt sie an. Zwei Regler auf einem Gerät arbeiten
> gegeneinander.

Der Actuator ist bewusst konservativ: Er schreibt nur auf konfigurierte
Steuer-Entitäten, nur bei Wertänderung, nie auf eine fehlende Empfehlung, und
isoliert Fehler je Gerät. Rollen ohne Steuer-Entität bleiben auch im Auto-Modus
reine Beobachtung.

**Warmwasser schaltet höchstens alle 30 Minuten.** Zwischen zwei Ein-/Aus-Kanten
liegt immer mindestens diese Zeit — in beide Richtungen, denn Takten entsteht
aus dem Wechsel. Der Sollwert folgt dem Überschuss unabhängig davon weiter im
Minutentakt.

**Nimmt ein Gerät die Freigabe nicht an, sagt HEMS es.** Zwei Minuten nach dem
Schreiben wird geprüft, ob der Ist-Zustand sie zeigt; wenn nicht, steht das
Attribut `freigabe_nicht_uebernommen` auf `sensor.hems_warmwasser_soll`, im
Entscheidungs-Log und im HA-Log. HEMS schreibt weiter dagegen — erzwingen kann
es den Befehl nicht.

Eine Einschränkung: **Warmwasser bleibt ohne Sperrfenster rund um die Uhr an.**
HEMS meldet Warmwasser nur während des konfigurierten Fensters als „aus“.

## Akku-Ladestrategie über den Tag

Für die Alterung eines Speichers zählt nicht der Spitzen-SoC, sondern die **Zeit
bei hohem SoC**. HEMS lädt deshalb **so spät wie vertretbar und nur so hoch wie
nötig** — und rechnet beides aus dem Bedarf statt aus festen Uhrzeiten:

| | |
|---|---|
| **Wie hoch?** | Das Abendziel ist der errechnete Nachtbedarf (Nachtdefizit + Reserve) plus 10 Prozentpunkte Marge — im Sommer oft 60 statt 100 %. `sensor.hems_speicher_regelung` zeigt ihn als `lade_ziel_soc`. |
| **Wann fertig?** | Eine Stunde vor Sonnenuntergang. Nicht zu einer festen Uhrzeit: im Juli wäre der Speicher sonst um 16:00 voll und stünde fünf Stunden ungenutzt oben. |
| **Wann los?** | So spät, dass der erwartete Restüberschuss dafür reicht, mit 50 % Zeitzuschlag gegen Prognosefehler. Bis dahin hält der Ladedeckel auf dem aktuellen Stand: der Akku drängelt sich nicht vor die Lasten. Attribut `lade_start`. |
| **Mittags** | Zwischen 11:00 und 14:00 (Ortszeit) reserviert der Akku grundsätzlich keinen Überschuss vor Warmwasser, Wallbox und Wärmepumpe — deren Puffer kostet keine Zyklenfestigkeit. Attribut `lade_pause`. |

**Bevor eingespeist wird, wird geladen.** Der Deckel ordnet den *Vorrang*, er
verschenkt keine Energie: Bleibt nach den Lasten Überschuss übrig, den sonst
niemand nimmt, lädt der Akku auch über den Deckel hinaus bis 100 % — auch in der
Mittagspause. Das Attribut `laden_statt_einspeisen` zeigt diesen Fall an, und der
geräteseitige Ziel-SoC wird dafür mit angehoben. **Ohne konkurrierende
Verbraucher greift der Deckel deshalb praktisch nie:** Wer nur Speicher und
Zähler hat, sieht weiter einen Akku, der vormittags volläuft — für ihn gäbe es
zum Laden nur die Alternative Einspeisen. Die Schonung verdient sich dort, wo
eine Last um denselben Überschuss konkurriert.

Der Plan wird sofort aufgegeben, wenn Deckung vor Schonung geht — Ziel
*Nulleinspeisung* oder *Vollladen*, morgen wenig Ertrag, es ist Nacht, oder der
Restertrag heute reicht nicht einmal mehr fürs Abendziel. Dann entfallen Rampe
**und** Mittagspause, und geladen wird sofort.

Und noch etwas fällt weg: Im **Winterhalbjahr** ist der Nachtbedarf regelmäßig
größer als die Kapazität. Das Abendziel steht dann ohnehin auf 100 %, die Rampe
beginnt früh, und die Strategie verhält sich wie vorher. Die Schonung ist ein
Sommer- und Übergangszeit-Gewinn.

### Speicher als Notstromreserve

`switch.hems_speicher_als_notstromreserve` (auch im Panel unter **Steuerung**)
kehrt die Abwägung um: Ziel 100 %, sofort statt just in time, Ladevorrang vor
allen Lasten — unabhängig vom eingestellten Vorrang und ohne Mittagspause — und
die volle Regel-Schrittweite beim Laden. Sehr schnell sehr voll; ein leerer
Speicher im Ausfall kostet mehr als ein paar Zyklen Lebensdauer.

Was der Schalter **nicht** tut: die Entladung begrenzen. Die untere Grenze bleibt
die **Reserve-SoC** der Speicher-Rolle. Wer eine Reserve will, die auch über die
Nacht stehen bleibt, hebt sie dort an — sonst ist der Speicher am Morgen wieder
so leer wie sonst auch.

## HEMS-Panel

Die Integration registriert einen eigenen Eintrag **HEMS** in der Seitenleiste
mit folgenden Ansichten:

- **Übersicht** — Lastfluss- und Entladeplan-Karte
- **Steuerung** — Betriebsmodus, Optimierungsziel, Regel-Aggressivität,
  E-Auto-Zwangsladung und Notstromreserve
- **Heizung** — Außentemperatur, Status (Frostschutz, Sommersperre, Heizgrenze),
  Vorlauf-Sollwert gegen Ist-Wert und die eingestellte Heizkurve
- **Diagnose** — Fehler, Warnungen und Überlappungen auf einen Blick
- **Konfiguration** — vollständiger Editor für Grundeinstellungen und alle Rollen

Der native Options-Flow (Einstellungen → Geräte & Dienste → HEMS →
Konfigurieren) bleibt als gleichwertiger Weg erhalten.

## Lovelace-Karten

Beide Karten werden automatisch registriert — keine Ressourcen-Konfiguration
nötig. Im Dashboard über den Karten-Picker hinzufügen oder per YAML:

```yaml
type: custom:hems-flow-card
entity: sensor.hems_lastfluss   # optional, das ist der Default
title: Lastfluss                # optional
height: 440                     # optional, px; "auto" = inhaltsabhängig
```

Die **Lastfluss-Karte** zeigt animierte Flüsse zwischen PV, Netz, Batterie und
Haus. Schaltbare Lasten stehen einzeln als Zeilen darunter, die Wallbox als
Chip, sobald für sie eine Leistungs-Entität konfiguriert ist. Konventionen: `netz_w` positiv = Netzbezug,
`batterie_w` positiv = Entladen.

```yaml
type: custom:hems-plan-card
entity: sensor.hems_entladeplan   # optional, das ist der Default
title: Entladeplan                # optional
height: 440                       # optional, px; "auto" = inhaltsabhängig
pv_entity: sensor.…               # optional, eigene Verlaufsquelle PV-Leistung
soc_entity: sensor.…              # optional, eigene Verlaufsquelle Speicher-SoC
```

Die **Entladeplan-Karte** zeigt heute und morgen im Zeitstrahl: orange die
geschätzte PV-Stundenkurve, grün die geplante nächtliche Akku-Entladung,
gestrichelt die SoC-Prognose. Für die vergangenen Stunden holt sie die
tatsächlich gemessenen Werte aus dem Recorder nach — Prognose und Realität
stehen so nebeneinander. Ein Band am unteren Rand zeigt die
Warmwasser-Verfügbarkeit (türkis frei, grau Sperrzeit, violett
Legionellenschutz).

`pv_entity` und `soc_entity` sind nur nötig, wenn der gemessene Verlauf aus
anderen Entitäten kommen soll als den HEMS-eigenen. Ist der Verlauf nicht
abrufbar, bleibt der Bereich leer und die Karte sagt es — die Prognose
funktioniert unabhängig davon.

## Entitäten

**Prognose und Messung**

- `sensor.hems_pv_heute` / `hems_pv_rest_heute` / `hems_pv_morgen` — kWh, alle Flächen summiert
- `sensor.hems_pv_leistung_jetzt` — W, geschätzt
- `sensor.hems_saldo` — W, normalisiert: positiv = Netzbezug
- `sensor.hems_hausverbrauch` — W, PV + Akku-Entladung + Netzbezug
- `sensor.hems_nachtdefizit` — kWh, erwarteter Verbrauch Sonnenuntergang → Sonnenaufgang
- `sensor.hems_ueberschuss_rest_heute` — kWh, Prognose
- `sensor.hems_speicher_soc` / `hems_speicher_verfuegbar` / `hems_speicher_ziel_soc`

**Empfehlungen**

- `sensor.hems_empfehlung` — Text, Details als Attribute
- `sensor.hems_lastfluss` — W, alle Flusswerte als Attribute
- `sensor.hems_entladeplan` — W, geplante Akku-Entladung ins Haus; Stunden-Slots, SoC-Prognose und PV-Kurve als Attribute
- `sensor.hems_warmwasser_soll` — °C, mit Status (aus / legionellenschutz / pv_boost / basis)
- `sensor.hems_speicher_regelung` — Modus entladen / laden / pausiert; Zuteilung je Speicher sowie Ladeplan (`lade_ziel_soc`, `lade_start`, `lade_deckel_soc`, `lade_pause`, `laden_statt_einspeisen`) als Attribute

**Steuerung und Diagnose**

- `select.hems_modus` — beobachten / auto / invers-auto / aus
- `select.hems_optimierungsziel` — eigenverbrauch / nulleinspeisung / vollladen
- `switch.hems_e_auto_zwangsladung`
- `switch.hems_speicher_als_notstromreserve` — Speicher auf Ausfall-Bereitschaft
- `binary_sensor.hems_konfiguration` — Config-Check für den Auto-Modus

## Weiterlesen

- [CONCEPT.md](CONCEPT.md) — Konzept und Phasenplan
- [CHANGELOG.md](CHANGELOG.md) — Änderungen, die nach einem Update eine
  manuelle Anpassung erfordern
- [lg-therma-v-esphome-modbus](https://github.com/ComicSans/lg-therma-v-esphome-modbus)
  — Wärmepumpe per Modbus RTU anbinden, ohne Cloud und ohne Gateway; ihre
  Entitäten passen auf die HEMS-Rollen Warmwasser und schaltbare Last

Jedes Konfigurationsfeld ist im Formular selbst erklärt — Label und Hilfetext
stehen in den Übersetzungsdateien und werden von `test_config_ws_labels.py`
auf Vollständigkeit geprüft.
