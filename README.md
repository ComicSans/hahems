# HEMS

<img src="https://raw.githubusercontent.com/ComicSans/hahems/main/assets/icon.png" alt="HEMS Icon" width="128" align="right">

Home Energy Management System als Home-Assistant-Custom-Integration.

HEMS prognostiziert PV-Ertrag und Verbrauch, plant daraus Speicher, Warmwasser,
Wärmepumpe und Wallbox und zeigt jederzeit an, was es warum empfiehlt. Auf Wunsch
schaltet es die Geräte auch selbst.

**Geräte-agnostisch:** Du konfigurierst *Rollen* — „Speicher“, „Heizkreis“,
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

Alles Weitere — Speicher, Warmwasser, Heizkreis, Lasten — ist optional. HEMS
läuft auch mit reinem Zähler und PV-Prognose und liefert dann eben nur die
Empfehlungen, die es aus diesen Daten ableiten kann.

## Installation

**Über HACS:** dieses Repository als Custom Repository vom Typ „Integration“
hinzufügen, dann installieren und Home Assistant neu starten.

**Manuell:** den Ordner `custom_components/hems/` in das Verzeichnis
`config/custom_components/` der HA-Instanz kopieren und neu starten.

## Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → „HEMS“**
2. Zähler-Entität und Grundlast angeben.
3. Über **Konfigurieren** die Geräte als Rollen anlegen: PV-Prognoseflächen,
   Speicher, Warmwasser, Heizkreis, schaltbare und modulierbare Lasten.
   Alternativ im HEMS-Panel unter **Konfiguration**.
4. `binary_sensor.hems_konfiguration` prüfen — er sagt dir, ob die
   Konfiguration für den Auto-Modus taugt.

Jedes Feld ist im Formular selbst erklärt. Als Nachschlagewerk gibt es
[docs/konfiguration.md](https://github.com/ComicSans/hahems/blob/main/docs/konfiguration.md).

## Betriebsmodi

`select.hems_modus` trennt **denken**, **messen** und **schalten**:

| Modus | Verhalten |
|---|---|
| **beobachten** | Standard. Empfehlungen werden berechnet und angezeigt, aber nichts geschaltet. |
| **auto** | HEMS schreibt die Empfehlungen zusätzlich auf die konfigurierten Steuer-Entitäten. |
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

Eine Einschränkung: **Warmwasser bleibt ohne Sperrfenster rund um die Uhr an.**
HEMS meldet Warmwasser nur während des konfigurierten Fensters als „aus“.

## HEMS-Panel

Die Integration registriert einen eigenen Eintrag **HEMS** in der Seitenleiste
mit folgenden Ansichten:

- **Übersicht** — Lastfluss- und Entladeplan-Karte
- **Steuerung** — Betriebsmodus, Optimierungsziel und E-Auto-Zwangsladung
- **Diagnose** — Fehler, Warnungen und Überlappungen auf einen Blick
- **Konfiguration** — vollständiger Editor für Grundeinstellungen und alle Rollen

Der native Options-Flow (Einstellungen → Geräte & Dienste → HEMS →
Konfigurieren) bleibt als gleichwertiger Weg erhalten.

### Effizienz — erscheint automatisch

Ist die eigenständige Integration
[wp-optimization](https://github.com/ComicSans/wp-optimization) installiert,
kommt ein weiterer Reiter **Effizienz** hinzu: COP gegen Datenblatt,
Spreizung, Taktung, Wärmeverlustkoeffizient und ein Vorschlag für die
Heizkurve. Ohne sie bleibt der Reiter aus, und HEMS ist unverändert
vollständig.

Es ist nichts zu verdrahten. HEMS erkennt die Integration über die Kennungen
der Entity-Registry, nicht über `entity_id` — umbenannte Entities brechen die
Erkennung deshalb nicht.

Die Werte dort sind **beratend**. Geschaltet wird nichts: Steuerung passiert
am Gerät oder über die Steuerung in HEMS.

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
Haus. Wärmepumpe und Wallbox erscheinen als Chips, sobald für sie eine
Leistungs-Entität konfiguriert ist. Konventionen: `netz_w` positiv = Netzbezug,
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
- `sensor.hems_speicher_regelung` — Modus entladen / laden / pausiert, Zuteilung je Speicher als Attribut
- `sensor.hems_heizkreis` — Modus heizen / kuehlen / aus, Vorlauf-Soll als Attribut

**Steuerung und Diagnose**

- `select.hems_modus` — beobachten / auto / aus
- `select.hems_optimierungsziel` — eigenverbrauch / nulleinspeisung / vollladen
- `switch.hems_e_auto_zwangsladung`
- `binary_sensor.hems_konfiguration` — Config-Check für den Auto-Modus
- `binary_sensor.hems_warmepumpen_storung` — Quelle für einen Handy-Push

## Weiterlesen

- [docs/konfiguration.md](https://github.com/ComicSans/hahems/blob/main/docs/konfiguration.md) — jedes Feld im Detail, plus
  die Steuer-Entitäten je Rolle
- [docs/regelverhalten.md](https://github.com/ComicSans/hahems/blob/main/docs/regelverhalten.md) — wie Speicher-Regelung,
  Ladedeckel, Lastensteuerung und Bedarfsprognose rechnen
- [docs/diagnose.md](https://github.com/ComicSans/hahems/blob/main/docs/diagnose.md) — Config-Check, Störungsmeldungen,
  Push-Automation
- [CONCEPT.md](https://github.com/ComicSans/hahems/blob/main/CONCEPT.md) — Konzept und Phasenplan
- [CHANGELOG.md](https://github.com/ComicSans/hahems/blob/main/CHANGELOG.md) — Änderungen, die nach einem Update eine manuelle
  Anpassung erfordern
- [lg-therma-v-esphome-modbus](https://github.com/ComicSans/lg-therma-v-esphome-modbus)
  — Wärmepumpe per Modbus RTU anbinden, ohne Cloud und ohne Gateway; die
  Entitäten passen direkt auf die HEMS-Rollen Heizkreis und Warmwasser
