# Kontrakt zum Energiemanagement — Version 1

Eigenständiges, versioniertes Dokument. Es beschreibt, wie ein
Energiemanagement mit dieser Integration spricht. Ein konkretes
Energiemanagement kommt darin nicht vor — der Kontrakt gilt für jedes.

**Kontraktversion: 1.** Jede Änderung, die eine Rolle entfernt, umbenennt oder
ihre Bedeutung verschiebt, erhöht die Version.

## Grundregeln

1. **Rollen, keine Entity-IDs.** Nutzende benennen Entities um; ein Kontrakt
   über `entity_id` bricht beim ersten Umbenennen. Der Kontrakt definiert
   Rollen, die konsumierende Seite verdrahtet Entities darauf.
2. **Tragende Werte sind Zustände, keine Attribute.** Attribute sind nicht in
   der Registry verankert und brechen still — eine Lovelace-Karte mit
   `state_attr(...)` wird schlicht leer. Attribute nur für Anzeigebeiwerk.
3. **Diese Integration schreibt nie an die Anlage.** Kein Aktuierungspfad,
   keine Steuer-Entities, kein Auto-Modus. Empfehlungen werden
   veröffentlicht, nicht umgesetzt. Damit bleibt „steuern" vollständig beim
   Energiemanagement, und zwei Integrationen können sich nie um denselben
   Sollwert streiten.
4. **Einheiten sind festgelegt** und werden nie umgestellt: thermische
   Leistung in W, Energie in kWh, Temperaturen in °C, Spreizung in K, COP
   dimensionslos, Wärmeverlust in W/K.
5. **Jeder Zahlenwert kommt mit seiner Datenbasis** — siehe Abschnitt F.

## A — Messeingänge

Quelle beliebig: eine Modbus-Anbindung, eine herstellereigene Integration,
eigene Zähler. Diese Integration kennt die Quelle nicht.

| Rolle | Pflicht | Einheit | Anmerkung |
|---|---|---|---|
| `vorlauf_temp` | ja | °C | gemessen, nicht gerechnet |
| `ruecklauf_temp` | ja | °C | |
| `durchfluss` | ja | l/h | andere Einheiten werden umgerechnet |
| `leistung_elektrisch` | ja | W | möglichst nur Wärmepumpe, ohne Fremdlast |
| `aussentemperatur` | ja | °C | |
| `verdichter_frequenz` | nein | Hz | ohne sie wird die Taktung aus der Leistung geschätzt |
| `betriebsart` | nein | Text | trennt Heizen, Warmwasser und Abtauen |

Ohne `betriebsart` vermischen sich Heizen und Warmwasser in einer Kennzahl.
Zulässig, wertet aber die Datenbasis ab.

**Einheiten werden gelesen, nie geraten.** Maßgeblich ist
`unit_of_measurement` der verdrahteten Entity. Fehlt sie oder ist sie
unbekannt, ist das ein Konfigurationsfehler, der den Nutzenden angezeigt wird
— keine Annahme. Ein stillschweigend angenommenes l/min statt l/h verfälscht
jeden COP um den Faktor 60, dieselbe Fehlerklasse wie eine Verwechslung von W
und kW.

Thermische Leistung: `Durchfluss [l/h] × Spreizung [K] × 1,163 = Watt`.
Nicht Kilowatt. Glykol senkt den Faktor um einige Prozent und gehört ins
Preset.

## B — Ausgaben an das Energiemanagement

| Rolle | Einheit | Bedeutung |
|---|---|---|
| `cop_momentan` | – | verworfen bei zu kleiner Spreizung |
| `cop_periode` | – | Arbeitszahl über den konfigurierten Zeitraum |
| `waermeleistung` | W | thermisch |
| `waermemenge` | kWh | |
| `spreizung` | K | |
| `cop_soll` | – | Erwartung aus dem Preset bei aktuellen Bedingungen |
| `cop_soll_unsicherheit` | % | Modellfehler des Presets |
| `cop_abweichung` | % | Ist gegen Soll, negativ heißt schlechter |
| `takte_periode` | – | Verdichterstarts, monoton zählend |
| `laufzeit_summe` | h | Verdichterlaufzeit, monoton zählend |
| `laufzeit_mittel` | min | abgeleiteter Anzeigewert |
| `durchfluss_ziel_prozent` | % | Zielvolumenstrom als Anteil des heutigen |
| `durchfluss_abweichung_prozent` | % | Abweichung vom Ziel, positiv = zu viel |
| `waermeverlust_koeffizient` | W/K | aus Regression |
| `empfehlung_fusspunkt` | °C | Heizkurve |
| `empfehlung_steilheit` | – | Heizkurve |
| `empfehlung_vorlauf_min` | °C | Heizkurve |
| `datenbasis` | Text | Güte der Messung, siehe F |
| `datenbasis_empfehlung` | Text | Länge der Beobachtung, siehe F |
| `kontrakt_version` | – | ganzzahlig, hier 1 |

### Zustandsklassen

Die Wahl ist nicht beliebig:

- **`measurement`** für alles, dessen Stundenmittel eine Aussage hat — COP,
  Leistung, Spreizung, Abweichung, Wärmeverlustkoeffizient, Empfehlungen.
- **`total_increasing`** für `waermemenge`, `takte_periode` und
  `laufzeit_summe`.

Takte und Laufzeit sind der Grund für die Unterscheidung: das Stundenmittel
einer Startzahl ist bedeutungslos. Sie laufen als monoton wachsende Zähler,
und jede Aussage über einen Zeitraum entsteht aus der **Differenz** der
Zählerstände, nie aus einem Mittelwert. `laufzeit_mittel` ist ein
Anzeigewert; für den Langzeitverlauf hinter `hinweis_taktung_hoch` zählen die
Zähler.

### Hinweise

Je Hinweisart ein eigener `binary_sensor` mit stabiler Kennung, nicht eine
Liste in einem Attribut — nur so bleiben sie in der Registry verankert und in
Automationen adressierbar.

- `hinweis_spreizung_niedrig` — Umwälzpumpe fördert mehr als nötig
- `hinweis_spreizung_hoch` — Durchfluss zu gering
- `hinweis_taktung_hoch`
- `hinweis_vorlauf_zu_hoch`
- `hinweis_effizienz_unter_erwartung`

Jeder Hinweis hat Ein- und Ausschaltschwelle, nie eine einzelne, und wird
über Tage gemittelt statt je Zyklus ausgewertet.

### Zielwerte

Zu den beiden Spreizungshinweisen gehört eine Zahl, sonst bleibt offen, um wie
viel. Sie folgt daraus, dass bei gegebener Wärmeleistung Volumenstrom und
Spreizung umgekehrt proportional sind (`Q = V̇ · ΔT · c`):

```
V̇_ziel / V̇_ist = ΔT_ist / ΔT_ziel
```

`ΔT_ziel` ist die Auslegungsspreizung aus dem Preset, dieselbe, aus der der
Nennvolumenstrom abgeleitet wurde. Beispiel: gemessene 4 K gegen 5 K Ziel
ergeben 80 % — der Volumenstrom liegt 25 % zu hoch.

**Es ist eine Aussage über den Volumenstrom, nicht über die Pumpenstufe.** Eine
Umwälzpumpe fördert nicht linear zu ihrer Prozentanzeige, und ihre Kennlinie
ist hier nicht bekannt. „Volumenstrom auf 80 %" ist eine Zielgröße; „Pumpe auf
Stufe 80 %" wäre geraten.

Grundlage ist die über Tage gemittelte Spreizung, nie der Momentanwert. Und
der Zielwert entfällt, solange `hinweis_temperaturen_identisch` ansteht: aus
zwei Sensoren auf derselben Quelle wäre jede Zielangabe aus einem Messfehler
abgeleitet.

## B2 — Automatische Erkennung

Eine konsumierende Seite soll diese Integration finden können, ohne dass
jemand Entities von Hand verdrahtet. Dafür gilt:

- **Die Kennung einer Entity ist `<eintrag-id>_<rolle>`.** Der Teil hinter dem
  letzten Unterstrich ist genau der Rollenname aus Abschnitt B. Diese Kennung
  ist stabil und liegt in der Entity-Registry — anders als die `entity_id`,
  die Nutzende jederzeit umbenennen.
- **Alle Entities einer Einrichtung hängen an einem Gerät** mit der Kennung
  `(wp_optimization, <eintrag-id>)`.
- **Vorhandensein prüft man an der Rolle `kontrakt_version`.** Sie ist als
  Diagnose-Entity standardmäßig abgeschaltet, aber immer in der Registry.

Damit ist die Erkennung: Einträge der Domäne `wp_optimization` suchen, deren
Entities aus der Registry holen, und über das Kennungs-Suffix den Rollen
zuordnen. Kein Konfigurationsschritt, und trotzdem kein Verlass auf
umbenennbare `entity_id`.

Der Weg über Rollen aus Abschnitt A bleibt daneben bestehen: er ist für alles
gedacht, was **nicht** aus dieser Integration kommt.

## C — Eingaben vom Energiemanagement

Ohne diese Richtung sind die Kennzahlen verfälscht: Wenn das Energiemanagement
die Wärmepumpe wegen PV-Überschuss anwirft oder aus einer Lastspitze
heraushält, ist das kein normaler Betrieb und darf die Erwartungsbasis nicht
prägen. Beide Rollen sind optional; fehlen sie, läuft die Integration
eigenständig weiter und weist die Einschränkung in der Datenbasis aus.

| Rolle | Bedeutung |
|---|---|
| `steuerung_aktiv` | Energiemanagement übersteuert die Wärmepumpe gerade |
| `steuerung_grund` | `normal` / `pv_ueberschuss` / `lastspitze` / `sperre` |

Die Werteliste ist **offen nach oben**. Ein unbekannter Grund wird wie
`normal` behandelt **und wertet die Datenbasis ab** — er wird weder still
verworfen noch führt er zu einem Fehler. Sonst bräche ein Energiemanagement,
das später einen fünften Grund einführt, diese Seite lautlos, und genau diese
Klasse stillen Versagens soll der Kontrakt verhindern.

## D — Presets

Preset-Schlüssel ist ein **Modell, keine Marke**. Die Keymark-Datenbank führt
allein zwölf Zeilen für die LG-Therma-V-Reihe mit vier deutlich verschiedenen
Kennlinien.

Ein Preset ist Datei, kein Code. Das Format ist JSON und nicht YAML, damit
die Analyse mit der Standardbibliothek auskommt und ohne Home Assistant
testbar bleibt:

```json
{
  "schluessel": "lg-therma-v-r32-split-5-7-9",
  "anzeigename": "LG Therma V R32 Split 5/7/9 kW",
  "quelle": "keymark",
  "cop_polynom": { "p1": -89.204479, "p2": -0.109127,
                   "p3": 7.997985,   "p4": 89.396482 },
  "cop_modellfehler_prozent": 16.6,
  "generisch": false,
  "spreizung_min_gueltig_k": 2.0,
  "waermetraeger_faktor": 1.163,
  "gueltig_ab_c": -20.0,
  "gueltig_bis_c": 20.0
}
```

COP-Erwartung: `cop = p1·t_aussen + p2·t_vorlauf + p3 + p4·t_aussen`.
Für `t_vorlauf` wird die **gemessene** Vorlauftemperatur eingesetzt, nicht die
sonst übliche Rekonstruktion aus Rücklauf plus 5 K — genau diese Annahme soll
die Spreizungsdiagnose prüfen. Das Ergebnis wird auf einen plausiblen Bereich
begrenzt, weil das Polynom außerhalb des gefitteten Kennfelds ins Absurde
extrapoliert.

## E — Datenhaltung

Die Langzeitstatistik von Home Assistant trägt fast alles: Sensoren mit
`state_class` behalten dauerhaft Stundenmittel, und zwei Reihen lassen sich
über den Statistik-Zeitstempel wieder zusammenführen. Ein eigener
Langzeitspeicher wäre eine Kopie davon.

1. **Fein, im Abfragetakt, flüchtig.** Ein Ringpuffer über Stunden bis
   maximal sieben Tage. Er trägt nur, was unterhalb der Stunde passiert:
   Verdichterstarts, Laufzeit je Takt und die Gültigkeitsprüfung.
2. **Grob, dauerhaft, geschenkt.** Die geprüften Kennzahlen werden als
   eigene Sensoren veröffentlicht und laufen damit in die Langzeitstatistik.
   Regression, Saison-Arbeitszahl und Erwartungsvergleich lesen von dort
   zurück.

Entscheidend ist die Reihenfolge: **erst prüfen, dann mitteln.** Ein
Stundenmittel, in das ungültige Momentanwerte eingehen, ist unbrauchbar — und
genau deshalb reicht es nicht, rohe Messsensoren der Statistik zu überlassen.

Kein eigener Langzeitspeicher, keine selbstgebaute Aufbewahrungslogik.

## F — Zwei Datenbasen

Vier Stufen, aufsteigend: `keine_daten`, `unzureichend`, `vorlaeufig`,
`belastbar`. Abwertung ist immer erlaubt, Aufwertung nie.

Es gibt sie **zweimal**, weil zwei verschiedene Dinge gemeint sind:

- **`datenbasis`** — wie sauber gerade gemessen wird. Wird abgewertet durch
  fehlende Betriebsart, ein generisches Preset, Bedingungen außerhalb des
  Kennfelds und fremdgesteuerten Betrieb.
- **`datenbasis_empfehlung`** — wie lange schon beobachtet wurde. Steuert die
  Heizkurvenempfehlung.

In einen Wert zusammengeworfen sähe ein tadellos gemessener COP wochenlang
wertlos aus, nur weil die Historie für eine Kurvenempfehlung noch nicht
reicht. Der Datenblattvergleich verlangt als einzige Aussage **beides**: eine
saubere Messkette und genug Historie.

„Noch zu wenig Daten" ist damit über Wochen ein regulärer, erwarteter
Zustand — kein Fehler und keine leere Anzeige.

## G — Übernahme der Empfehlungen

Konfigurierbar, aber **auf der Seite des Energiemanagements**, weil dort
geschrieben wird. Diese Integration veröffentlicht nur.

Zwei getrennte Oberflächen, damit der Schalter nicht doppelt gebaut wird: die
Karte hier zeigt die Empfehlung und ihre Datenbasis, ohne zu behaupten, über
ihre Verwendung zu bestimmen. Die Übernahme ist eine Option des
Energiemanagements je Heizkreis.

Voreinstellung ist Anzeigen. Bei eingeschalteter Übernahme gilt: nur auf
Tagesskala, mit Hysterese, und nur bei belastbarer Datenbasis. Grund ist eine
echte Rückkopplung — die Empfehlung entsteht aus Betrieb, den das
Energiemanagement mit der vorigen Empfehlung selbst erzeugt hat. Ohne Dämpfung
wandert die Kurve.

## Herkunft und Lizenzen

Siehe [ATTRIBUTION.md](../ATTRIBUTION.md).
