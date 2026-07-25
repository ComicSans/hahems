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

> **Breaking Change (1.0.5):** Sensor-Attribute mit den Präfixen `wp_`/`ww_`
> heißen jetzt ausgeschrieben `waermepumpe_`/`warmwasser_` (z. B. `wp_modus` →
> `waermepumpe_modus`, `ww_soll_c` → `warmwasser_soll_c`). Wer diese Attribute
> direkt in Lovelace-Karten oder Templates referenziert (`state_attr(...)`),
> muss die Namen manuell anpassen — Attribute sind anders als `entity_id`
> nicht in der Entity-Registry verankert und ändern sich sofort mit dem
> Update. Die `entity_id`s selbst sind unberührt: Sie waren schon vorher
> ausgeschrieben (`sensor.hems_warmwasser_soll`) und hängen nicht an diesen
> Attribut-Präfixen.

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

## Konfigurationsparameter

Alle Felder sind auch direkt im Config-Flow als Hilfetext hinterlegt
(unter jedem Formularfeld); diese Tabelle dient als Nachschlagewerk.
Felder ohne Erklärung sind selbsterklärend (z. B. reine Namen/Labels).

### Grundeinstellungen

| Feld | Beschreibung |
|---|---|
| **Zähler (Leistung am Netzanschluss)** | Sensor mit der momentanen Leistung am Netzanschlusspunkt in Watt. Erwartet wird: positiv = Netzbezug, negativ = Einspeisung. |
| **Vorzeichen invertieren** | Aktivieren, falls dein Zähler Einspeisung als positiven Wert meldet. |
| **PV-Leistung jetzt (W, optional)** | Sensor mit der aktuellen PV-Gesamtleistung über alle Flächen. Wird für den Lastfluss und die Empfehlung genutzt. |
| **PV-Vorzeichen invertieren** | Aktivieren, falls dein Wechselrichter die PV-Leistung mit umgekehrtem Vorzeichen meldet (negativ = Erzeugung). |
| **PV-Sensor enthält Akkuleistung** | Aktivieren, wenn PV und Akku am selben Punkt gemessen werden (Hybrid-Wechselrichter) und der PV-Wert die Akkuleistung enthält. HEMS rechnet die Akkuleistung dann aus der gemessenen PV heraus (Entladen senkt sie, Laden hebt sie). |
| **Wettervorhersage (optional)** | Wetter-Entität, deren Tagesvorhersage in die Planung einfließt: Bei trübem Folgetag lädt HEMS den Speicher voll statt nur den Nachtbedarf zu sichern. |
| **Grundlast tagsüber (W)** | Typischer Dauerverbrauch des Hauses tagsüber ohne große Verbraucher (Kühlschrank, Netzwerk, Standby). |
| **Grundlast nachts (W)** | Startwert für den Verbrauch in der Nacht. HEMS lernt den echten Wert nach einigen Tagen automatisch aus der Zählerstatistik. |
| **Prioritäten bei Überschuss** | Wohin soll der Überschuss zuerst fließen? „Automatisch" sichert bei knappem Ertrag zuerst den Akku für die Nacht ab, bei reichlich Ertrag darf das E-Auto zuerst laden. Die Warmwasser-Basisladung hat immer Vorrang. |
| **Kapazität frei: Bedarf (kWh)** | Der Binärsensor „Kapazität frei" schaltet ein, wenn diese Energiemenge über die angegebene Dauer verfügbar ist, ohne Reserve und Nachtdeckung anzutasten. |
| **Kapazität frei: Dauer (h)** | Dauer, über die der Bedarf gedeckt sein muss. PV-Überschuss, der in dieses Zeitfenster fällt, zählt zur freien Kapazität. |

### PV-Prognosefläche

| Feld | Beschreibung |
|---|---|
| **Name** | Eine kurze Bezeichnung für diese Prognosefläche, z. B. die Dachausrichtung (nur zur Anzeige). |
| **Energie heute (kWh)** | Sensor mit der prognostizierten PV-Gesamtenergie für heute (kWh), aus deiner Prognose-Integration (z. B. Forecast.Solar, Solcast). |
| **Energie heute verbleibend (kWh)** | Sensor mit der prognostizierten PV-Restenergie für den restlichen heutigen Tag (kWh). Fließt in die Live-Überschuss- und Empfehlungsberechnung ein. |
| **Energie morgen (kWh)** | Sensor mit der prognostizierten PV-Gesamtenergie für morgen (kWh). Entscheidet, ob der Speicher heute schon voll als Puffer gegen einen schlechten Folgetag geladen wird. |

### Speicher

| Feld | Beschreibung |
|---|---|
| **Name** | Eine kurze Bezeichnung für diesen Speicher, z. B. Einbauort oder Gerätename. |
| **SoC-Entität (%)** | Sensor mit dem aktuellen Ladestand in Prozent. |
| **Leistungs-Entität (W, optional)** | Sensor mit der aktuellen Lade-/Entladeleistung in Watt. Konvention: positiv = Entladen ins Haus, negativ = Laden. Wird für die Lastfluss-Anzeige und die Regelung im Auto-Modus genutzt. |
| **Lade-Sollwert-Entität (W, optional)** | Number-Entität, über die HEMS die aktuelle Ladeleistung setzen kann. |
| **Entlade-Sollwert-Entität (W, optional)** | Number-Entität, über die HEMS die aktuelle Entladeleistung setzen kann (z. B. „jetzt mit 500 W entladen"). |
| **Kapazität (kWh)** | Nutzbare Kapazität dieses Speichers; bestimmt die Verteilung der Lade-/Entladeleistung über mehrere Speicher und die SoC-Prognose. |
| **Reserve-SoC (%)** | Unter diesen Ladestand soll der Akku nicht entladen werden (Notreserve). |
| **Max. Ladeleistung (W)** | Begrenzung der Ladeleistung; bestimmt, wie schnell der Akku im Sonnenfenster voll wird. |
| **Max. Entladeleistung (W)** | Begrenzung der Entladeleistung; mehr kann der Akku nicht ins Haus liefern. |
| **Kaltreserve** | Dieser Speicher nimmt am Entladen erst teil, wenn der mittlere SoC der übrigen Speicher unter die Reserve-Schwelle fällt (mit Hysterese). Geladen wird er immer mit, proportional zur freien Kapazität. |
| **Richtungs-Select (optional, z. B. Zendure ac_mode)** | Optionaler Select/Input_select, über den HEMS zwischen Lade- und Entladerichtung umschaltet (z. B. Zendures ac_mode). Nur nötig, wenn dein Speicher zusätzlich zum Sollwert einen Modus-Umschalter braucht. |
| **Richtungs-Option beim Laden** | Der Options-Wert, der den Speicher in den Lademodus versetzt. Muss exakt (Groß-/Kleinschreibung beachten) einer verfügbaren Option des Selects entsprechen — der Config-Check meldet es sonst. |
| **Richtungs-Option beim Entladen** | Der Options-Wert, der den Speicher in den Entlademodus versetzt. Muss exakt (Groß-/Kleinschreibung beachten) einer verfügbaren Option des Selects entsprechen — der Config-Check meldet es sonst. |
| **Ziel-SoC-Entity (soc_set, geräteseitiger Ladedeckel, optional)** | Optionale Number-Entität, die begrenzt, wie weit der Speicher eigenständig lädt (geräteseitiges Ziel-SoC). Manche Speicher ignorieren ein Lade-Limit von 0 und laden trotzdem bis zu diesem Ziel weiter — setze dies, falls das bei deinem Gerät zutrifft. |

### Warmwasser

| Feld | Beschreibung |
|---|---|
| **Name** | Eine kurze Bezeichnung für dieses Warmwassersystem (nur zur Anzeige). |
| **Temperatur-Entität (optional)** | Sensor mit der aktuellen Warmwassertemperatur. Ohne ihn empfiehlt HEMS weiterhin einen Sollwert, kann die tatsächliche Temperatur aber weder anzeigen noch prüfen. |
| **Basis-Soll (°C)** | Diese Temperatur wird immer gehalten, notfalls mit Netzstrom. |
| **Komfort-Soll (°C)** | Auf diese Temperatur wird nur bei PV-Überschuss aufgeheizt. |
| **Sperrzeit ab** | Beginn der täglichen Sperrzeit, in der kein Warmwasser bereitet wird. Beide Felder leer lassen heißt keine Sperre. |
| **Sperrzeit bis** | Ende der Sperrzeit. Liegt das Ende vor dem Anfang, läuft das Fenster über Mitternacht (z. B. 18:00 bis 06:00). |
| **Legionellenschutz: Wochentag** | Wöchentliches Hygiene-Fenster: An diesem Tag wird der Sollwert unabhängig vom Überschuss auf das Legionellenschutz-Soll angehoben — notfalls aus dem Netz. „Deaktiviert" schaltet die Funktion ab. |
| **Legionellenschutz: ab** | Lokale Startzeit des wöchentlichen Legionellen-Fensters. |
| **Legionellenschutz: bis** | Ende des Fensters. Ein Ende vor dem Anfang läuft über Mitternacht. |
| **Legionellenschutz-Soll (°C)** | Solltemperatur während des Legionellen-Fensters. |
| **PV-Boost: Speicher-SoC ab (%)** | Die Komfortladung wird erst empfohlen, wenn der Gesamt-Speicher-SoC dieses Niveau erreicht. |
| **PV-Boost: Speicher-SoC Ende (%)** | Die Boost-Empfehlung endet, wenn der SoC unter dieses Niveau fällt (Hysterese). |
| **PV-Boost: Netzsaldo ab (W)** | Netzsaldo, der zum Start des Boosts erreicht sein muss; negativ = Einspeisung (z. B. -2800 = 2,8 kW Einspeisung). |
| **PV-Boost: Netzsaldo Ende (W)** | Netzsaldo, bei dem der Boost endet (positiv = Bezug). |
| **Steuer-Entity (water_heater) für Auto-Modus** | water_heater-Entität, auf der HEMS im Auto-Modus den Sollwert setzt. Ohne sie wird der Sollwert nur empfohlen, nicht gestellt. |

### Heizkreis

| Feld | Beschreibung |
|---|---|
| **Name** | Eine kurze Bezeichnung für diesen Heizkreis (nur zur Anzeige). |
| **Außentemperatur-Entität** | Temperatursensor, der Modus-Entscheidung und Heizkurve speist. |
| **Wärmeanforderungs-Entität (%, optional)** | Sensor mit der Wärmeanforderung der Räume in Prozent (z. B. PID-Thermostat-Ausgang, mehrere Räume per Template-Sensor kombiniert). Hebt das Vorlauf-Soll um bis zu 5 K an; unter 1 % Anforderung fällt der Vorlauf auf das Minimum (Absenkbetrieb). |
| **Heizen ein unter (°C)** | Heizen wird empfohlen, sobald die Außentemperatur auf diesen Wert fällt. |
| **Heizen aus über (°C)** | Heizen endet, sobald die Außentemperatur auf diesen Wert steigt (Hysterese). |
| **Kühlen ein über (°C)** | Kühlen wird empfohlen, sobald die Außentemperatur auf diesen Wert steigt. |
| **Kühlen aus unter (°C)** | Kühlen endet, sobald die Außentemperatur auf diesen Wert fällt (Hysterese). |
| **Frostschutz ein unter (°C)** | Frostschutz erzwingt Heizen, sobald die Außentemperatur auf diesen Wert fällt — auch während der Sommersperre. |
| **Frostschutz aus über (°C)** | Frostschutz endet, sobald die Außentemperatur auf diesen Wert steigt (Hysterese gegen Takten). |
| **Heizsperre ab Monat** | In den Sperrmonaten (einschließlich) wird Heizen nur noch vom Frostschutz erzwungen, sonst nie empfohlen (Sommersperre). |
| **Heizsperre bis Monat** | Letzter Monat (einschließlich) der Sommersperre. Ein Start nach dem Ende läuft über den Jahreswechsel. |
| **Kurve: Vorlauf-Soll bei 0 °C (°C)** | Vorlauf-Soll bei 0 °C Außentemperatur. |
| **Kurve: Steigung (K je K)** | Absenkung des Vorlauf-Solls je Grad Außentemperatur. |
| **Minimaler Vorlauf (°C)** | Das Vorlauf-Soll fällt beim Heizen nie unter diesen Wert. |
| **Minimaler Vorlauf bei Kälte (°C)** | Minimales Vorlauf-Soll, wenn es draußen kälter als 5 °C ist. |
| **Maximaler Vorlauf (°C)** | Das Vorlauf-Soll übersteigt diesen Wert nie. |
| **Kühl-Vorlauf (°C)** | Fester Vorlauf beim Kühlen. |
| **Steuer-Entity (climate) für Auto-Modus** | climate-Entität, auf der HEMS im Auto-Modus den Vorlauf-Sollwert setzt. Ohne sie wird der Sollwert nur empfohlen, nicht gestellt. |
| **Schalter Flüsterbetrieb (optional)** | Optionaler Schalter/Input_boolean, den HEMS bei knappem Überschuss einschaltet, um die Wärmepumpe im Silent-Modus laufen zu lassen. |
| **Saison-Richtung input_select (optional)** | Optionaler Input_select/Select, mit dem HEMS eine Wärmepumpe zwischen Heiz- und Kühlrichtung umschaltet, falls dein Gerät einen expliziten Saison-Umschalter braucht. |

### Schaltbare Last

| Feld | Beschreibung |
|---|---|
| **Name** | Eine kurze Bezeichnung für diese Last (nur zur Anzeige, z. B. in Empfehlung und Lastfluss-Karte). |
| **Schalter/Climate-Entität** | Schalter- (oder Climate-)Entität, die HEMS abhängig vom Überschuss ein- und ausschaltet. |
| **Leistungs-Entität (W, optional)** | Sensor mit der aktuellen Leistungsaufnahme in Watt. HEMS lernt daraus die erwartete Leistung, während die Last läuft, und nutzt sie, um zu entscheiden, ob der Überschuss sie deckt. |
| **Mindestlaufzeit (min)** | Ist das Gerät einmal an, bleibt es mindestens so lange eingeschaltet. |
| **Mindestpause (min)** | Nach dem Ausschalten bleibt das Gerät mindestens so lange aus. |
| **Max. Sperrdauer pro Tag (min)** | Länger als diese Dauer pro Tag wird das Gerät nie blockiert. |
| **Priorität** | 1 = höchste Priorität. Bei knappem Überschuss werden Lasten mit höherer Priorität zuerst versorgt. |
| **Heizungsgekoppelt (Wärmepumpe, Heizstab)** | Nur für Lasten, deren Verbrauch der Außentemperatur folgt (Wärmepumpe, Heizstab). Nur diese fließen in das Heizgradstunden-Modell für die Bedarfsprognose ein. Überschussgesteuerte Lasten wie Pool oder Luftentfeuchter bleiben ausgeschaltet. |

### Modulierbare Last

| Feld | Beschreibung |
|---|---|
| **Name** | Eine kurze Bezeichnung für diese Last (nur zur Anzeige, z. B. der Wallbox-Name). |
| **Strom-Sollwert-Entität (A)** | Number-Entität, über die der Strom-Sollwert gesetzt wird. |
| **Schalter-Entität (optional)** | Optionaler Schalter, über den HEMS das Gerät zusätzlich komplett ein- und ausschalten kann (z. B. Ladefreigabe der Wallbox). |
| **Leistungs-Entität (W, optional)** | Sensor mit der aktuellen Leistungsaufnahme in Watt. Wird für die Lastfluss-Anzeige genutzt und um den tatsächlichen Bedarf der Last zu lernen. |
| **Minimalstrom (A)** | Unterhalb dieses Stroms kann das Gerät nicht arbeiten (Wallbox-Minimum meist 6 A). |
| **Maximalstrom (A)** | Das Gerät wird nie über diesen Strom hinaus angesteuert. |
| **Phasen** | Anzahl der angeschlossenen Phasen; wird zur Umrechnung zwischen Strom (A) und Leistung (W) genutzt. |
| **Mindestlaufzeit (min)** | Einmal gestartet, läuft das Gerät mindestens so lange, bevor HEMS es stoppt (z. B. E-Auto mindestens 10 Minuten laden). |
| **Mindestpause (min)** | Nachdem HEMS das Gerät gestoppt hat, bleibt es mindestens so lange aus, bevor es wieder starten kann. |
| **Priorität** | 1 = höchste Priorität. Bei knappem Überschuss werden Lasten mit höherer Priorität zuerst versorgt. |

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
- **Konfiguration** — vollständiger Editor direkt im Panel: **Grundeinstellungen**
  (Zähler, Grundlasten, Wetter, Prioritätsmodus) sowie Rollen mit ihren Geräten
  auflisten, hinzufügen, bearbeiten, entfernen. Die Formularfelder und die
  Klartext-Labels der Auswahllisten werden aus den **bestehenden**
  Config-Flow-Schemas und Übersetzungen abgeleitet (kein zweiter Feld-Katalog,
  keine Drift); die Entitätsauswahl ist ein eigener Picker aus `hass.states`
  (kein fragiles HA-internes Element). Gespeichert wird über WebSocket-Befehle
  (`hems/config/*`, Schreibzugriffe admin-pflichtig), die `entry.options`
  schreiben und die Integration neu laden.

Der native Options-Flow (Einstellungen → Geräte & Dienste → HEMS →
Konfigurieren) bleibt als gleichwertiger Weg erhalten.

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
verschoben, Totband gegen Dauerkorrekturen. **Laden** verteilt parallel
proportional zur freien Kapazität — mehrere Akkus laden gleichzeitig (niedrigere
C-Rate je Akku, SoC-Ausgleich), außer der Überschuss reicht nur für weniger
Einheiten über dem Mindest-Setpoint (dann Rückfall auf die leersten zuerst).
**Entladen** wird greedy zugeteilt (ein Akku zur Zeit, mit Auswahl-Hysterese
gegen Umschaltverschleiß). Speicher ohne SoC-Wert fallen aus der Zuteilung.

Ein als **Kaltreserve** markierter Speicher entlädt erst mit, wenn der
mittlere SoC der übrigen unter 40 % fällt, und scheidet oberhalb von 45 %
wieder aus (Hysterese); geladen wird er immer mit. Im Modus `beobachten` wird
die Empfehlung nur angezeigt; im Modus `auto` schreibt HEMS die Zuteilung auf
die Lade-/Entlade-Sollwerte der Speicher (siehe [Auto-Modus](#auto-modus-aktuierung)).

### Ladevorrang Akku ↔ Wallbox

Bei PV-Überschuss teilt HEMS die Ladehoheit nach dem Prioritätsmodus
(`priority_mode` aus der Einrichtung) auf:

- **ev_first**: die Wallbox bedient sich zuerst am Überschuss, der Akku bekommt
  den Rest.
- **battery_first**: der Akku bekommt die Oberhand auf den Überschuss **oberhalb
  des Wallbox-Minimums**. Ein bereits ladendes Auto behält sein Minimum (wird
  nie abgeregelt), zusätzlicher Überschuss geht aber zuerst in den Akku. Ist der
  Akku am Tagesdeckel (~78 %), reserviert er nichts mehr und die Wallbox bekommt
  den vollen Überschuss.
- **auto**: bei knappem Tagesertrag (`knapp`) wie battery_first, sonst wie
  ev_first — dieselbe Logik, nach der die Empfehlung schon den Akku vor das Auto
  stellt.

Umgesetzt in `strategies/coordination.py`: der reservierte Überschuss wird dem
Lasten-Regler vorenthalten; den Rest holt sich die Speicher-Regelung über ihr
normales Saldo-Residuum. Die Regelmathematik beider Regler bleibt unverändert.

### Schaltbare Lasten (überschussgesteuert)

Schaltbare Lasten (nur an/aus, z. B. eine Umwälzpumpe) schaltet HEMS im
Auto-Modus überschussgesteuert: ein, solange der Überschuss ihre **erwartete
Leistung** deckt, aus, wenn er fehlt. Beliebig viele Lasten sind möglich; jede
hat ihre eigenen Zeiten, ihre eigene Priorität und ihre eigene gelernte
Leistung.

Die erwartete Leistung wird je Last aus ihrer `power_entity` gelernt und über
Neustarts hinweg persistiert (`power_memory.py`). Ohne Leistungsmessung greift
dauerhaft ein konservativer Fallback von 2000 W (lieber später einschalten als
Netzbezug provozieren) — eine kleine Last wird dann praktisch nie zugeschaltet;
der Config-Check warnt davor.

Gelernt wird nicht jeder Messwert, sondern nach drei Regeln:

- **Anlaufkarenz (5 min):** Direkt nach dem Einschalten ist der Verbraucher noch
  nicht auf Leistung. Die Karenz läuft nach einem HA-Neustart neu an, weil
  `last_changed` dann auf den Neustart zeigt.
- **Boden:** Unterhalb 20 W gilt eine Last als „an, aber zieht nichts".
  Heizungsgekoppelte Lasten haben einen eigenen Boden von 500 W — bei einer
  Wärmepumpe ziehen Regelung, Umwälzpumpe und Ventile ein paar hundert Watt,
  lange bevor der Kompressor auf Leistung ist.
- **Asymmetrie:** nach oben sofort, nach unten nur zu 25 % pro Messung. Eine
  unterschätzte Last wird zu früh eingeschaltet und provoziert Netzbezug; eine
  Teillastphase soll den gelernten Wert deshalb nicht auf ihren Momentanwert
  ziehen.

**Heizungsgekoppelt (`heat_coupled`):** nur Lasten, deren Verbrauch der
Außentemperatur folgt (Wärmepumpe, Heizstab), fließen in das
Heizgradstunden-Modell für die Bedarfsprognose ein und werden aus dem gelernten
Lastprofil herausgerechnet. Eine überschussgesteuerte Last (Pool,
Luftentfeuchter) hat keinen Temperaturbezug — sie würde die Regression
verzerren (zu hohe Basisleistung → überschätztes Nachtdefizit) und bleibt
deshalb ohne das Flag im normalen Lastprofil.

Prioritätsreihenfolge, wenn der Überschuss nicht für alle reicht:

1. **Modulierbare Lasten drosseln herunter** (geben ihr Headroom auf, behalten
   aber ihr Minimum) — sie sind der elastische Puffer.
2. **Schaltbare Lasten** werden abgeschaltet, niedrigste Priorität (`priority`,
   klein = wichtiger) zuerst.
3. Der **Akku pausiert** zuletzt — er lädt weiter, solange gedrosselt oder
   abgeschaltet werden kann.

Anti-Takt: `min_on` hält eine Last an, `min_off` hält sie aus, `max_block`
erzwingt ein Einschalten, wenn HEMS sie zu lange ausgehalten hat. Umgesetzt in
`strategies/switchable.py`; die Empfehlung steht als `schaltbare`-Attribut an
`sensor.hems_empfehlung`, geschaltet wird im Modus `auto`. Die Lastfluss-Karte
zeigt jede Last als eigene Zeile mit Priorität, Ist-/Erwartungsleistung und der
Begründung der Empfehlung (Attribut `schaltlasten` an `sensor.hems_lastfluss`).

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

### Akku-Schonung: Ladedeckel über den Tag

Unabhängig vom Ziel begrenzt ein zeitabhängiger **Ladedeckel** die Live-Ladung,
um die Akkus zu schonen (kalendarische Alterung ist bei hohem SoC am größten).
Tagsüber wird nur bis `STORAGE_DAY_HOLD_SOC` (Standard 78 %) geladen; erst in den
letzten `STORAGE_FULL_CHARGE_LEAD_H` Stunden (Standard 3 h) vor Sonnenuntergang
steigt der Deckel per Rampe auf 100 %, sodass der Speicher ~zum Sonnenuntergang
voll für die Nacht ist und möglichst wenig Zeit bei 100 % verbringt. Der Deckel
begrenzt nur das Laden — liegt der SoC schon darüber, wird nicht zwangsentladen.

Der Deckel wird sofort auf 100 % aufgehoben, sobald Nachtdeckung vor Schonung
geht: Ziel verlangt Vollladung (`nulleinspeisung`/`vollladen`), morgen wird es
knapp (`morgen_knapp`), oder der erwartete Restertrag heute reicht nicht mehr,
um später von 78 % auf 100 % nachzuladen (dann wird sofort voll geladen, statt
zu leer in die Nacht zu gehen). Der aktuelle Deckel steht als
`lade_deckel_soc` im Plan und begrenzt auch die SoC-Prognose der Plankarte.

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
Empfehlung, und **isoliert Fehler je Gerät**. Reihenfolge WW → WP → Akku → E-Auto → Schaltlasten.

Steuer-Entitäten je Rolle (alle optional, im Options-Flow zu setzen):

| Rolle | Empfehlung | Steuer-Entitäten | Service |
|---|---|---|---|
| Warmwasser | `ww_soll_c` + Status | `control_entity` (water_heater) | on/off + `set_temperature` |
| Wärmepumpe | `heizung.modus`/`vlt`/`leise` | `control_entity` (climate), `silent_switch_entity`, `season_select_entity` | `set_hvac_mode` + `set_temperature` + Silent + Saison |
| Speicher | `regelung` (Zuteilung je Einheit) | `charge_setpoint_entity`, `discharge_setpoint_entity`, optional `mode_entity` + `mode_charge/discharge_option` | `number.set_value` (+ `select_option`) |
| E-Auto / mod. Last | `ev_regelung` (Sollstrom je Last) | `current_entity`, `switch_entity` (Rolle „Modulierbare Last") | `number.set_value` + on/off |
| Schaltbare Last | `schaltbare` (an/aus) | `switch_entity` (Rolle „Schaltbare Last") | on/off |

Im Auto-Modus stellt HEMS den **PV-Überschuss-Ladestrom** der Wallbox selbst: der
Lasten-Regler (`strategies/loads.py`) bestimmt je modulierbarer Last den
Sollstrom aus dem Überschuss, der Actuator schreibt ihn auf `current_entity` und
schaltet `switch_entity`. Die Zwangsladung (`switch.hems_e_auto_zwangsladung`)
überschreibt das mit vollen Ampere. Fehlt Saldo- oder Leistungsmessung, gibt der
Regler keine Empfehlung ab und lässt die Last unangetastet (Fail-safe).

Eine Einschränkung für das Scharfschalten:

1. **Warmwasser-Nacht-Aus braucht das Sperrfenster.** HEMS meldet WW nur während
   des konfigurierten Sperrfensters (`block_start`/`block_end`) als „aus", sonst
   „basis" (Grundtemperatur). Ohne gesetztes Fenster hält der Auto-Modus WW rund
   um die Uhr an. Das Sperrfenster ist eine feste Uhrzeit, kann die saisonale
   Tag/Nacht-Umschaltung der alten Automation also nur annähern.

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
  falsche Domain, Richtungs-Select ohne Optionswerte, oder
  `mode_charge_option`/`mode_discharge_option` passt nicht exakt zu einer
  echten Option des Richtungs-Selects (Freitext-Falle — Groß-/
  Kleinschreibung zählt).
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
(Zwang)". Der Zustand wird als Attribut `ev_zwang` an `sensor.hems_empfehlung`
gespiegelt.

**Der Zwang garantiert, _dass_ geladen wird — nicht, _wie schnell_.** Jede
modulierbare Last läuft: Sie fällt nicht durch das Schmitt-Band, die Rotation
zwischen gleichrangigen Lasten oder die Mindestpause. Ihr Sollstrom folgt aber
weiterhin dem Überschuss und sinkt bei Defizit bis auf die Untergrenze (`min_a`),
statt stur volle Ampere aus dem Netz zu ziehen. Liegt reichlich Überschuss an,
geht sie bis `max_a` hoch.

Wie ohne Zwang startet eine ausgeschaltete Wallbox erst am Minimum und bekommt
mehr, sobald sie im Folgezyklus echte Nachfrage nachweist — eine Wallbox ohne
angestecktes Auto zieht sonst nur eine Phantomlast durch die Bilanz.

Ohne Saldo oder Leistungsmessung gibt es keinen Überschuss zu verteilen: dann
volle Ampere (Fail-safe „jetzt laden").

Damit der Hausakku dabei nicht still ins Auto leerläuft, rechnet die Saldo-
Regelung die aktuelle Wallbox-Leistung (`wallbox_w`) aus dem Saldo heraus, den
sie ausregelt: Der Akku hält seinen SoC, das Zwangs-Delta kommt aus dem Netz
("Akku schonen"). Liefert die PV gerade Überschuss, lädt der Akku daraus wie
gewohnt weiter — er wird nur nicht zusätzlich für die Wallbox entladen. Im
Modus `auto` wird die Zwangsladung tatsächlich geschaltet (Sollstrom auf
`current_entity`, `switch_entity` an); das reguläre Überschussladen bleibt
dagegen bei der bestehenden Automation (siehe [Auto-Modus](#auto-modus-aktuierung)).
