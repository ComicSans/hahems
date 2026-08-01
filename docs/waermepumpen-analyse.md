# Wärmepumpen-Analyse

Was die Analyse misst, unter welchen Namen sie es veröffentlicht, und welche
Regeln dabei bindend sind.

**Dieses Dokument ist eine Schnittstellenbeschreibung, keine Bedienanleitung.**
Automationen und Dashboards hängen an den Rollennamen hier. Eine Rolle zu
entfernen, umzubenennen oder umzudeuten bricht sie, und niemand merkt es beim
Update — deshalb hält `tests/waermepumpe/test_rollen.py` Code und Dokument
zusammen.

## Herkunft

Bis August 2026 war das die eigenständige Integration `wp-optimization`, und
statt dieses Dokuments gab es `kontrakt-v1.md`: einen versionierten Vertrag
zwischen zwei Repositories. Der Vertrag hatte ein Problem, das er selbst nicht
sehen konnte — niemand prüfte ihn.

Beim Zusammenführen kam heraus:

- Produzent und Konsument lieferten beide `takte`, der Vertrag forderte
  `takte_periode`.
- `verwerfungsgrund`, `durchfluss_geschaetzt` und
  `hinweis_temperaturen_identisch` standen im Code beider Seiten und in keinem
  Vertrag.
- `cop_periode` stand im Vertrag und in keinem Code.
- Beide Seiten meldeten `kontrakt_version = 1`.

Die Namen unten sind deshalb aus dem Code rekonstruiert und nicht aus dem alten
Vertrag übernommen: wo beide Seiten dasselbe taten, hatte das Dokument unrecht.
`kontrakt_version` gibt es nicht mehr — eine Versionsnummer zwischen zwei
Modulen desselben Pakets sagt nichts, was der Test nicht besser sagt.

## Grundregeln

1. **Rollen, keine Entity-IDs.** Nutzende benennen Entities um. Die Kennung in
   der Entity-Registry lautet `<eintrag-id>_<rolle-id>_<rolle>` und ist
   stabil; die `entity_id` ist es nicht.
2. **Tragende Werte sind Zustände, keine Attribute.** Attribute sind nicht in
   der Registry verankert und brechen still — eine Karte mit `state_attr(...)`
   wird schlicht leer, ohne Fehlermeldung.
3. **Die Analyse schreibt nie an die Anlage.** Kein Aktuierungspfad, keine
   Steuer-Entity. Gestellt wird über die Rolle Heizkreis. Geprüft von
   `tests/waermepumpe/test_architektur.py`, seit die Analyse neben einem
   Aktuator liegt, der wirklich schaltet.
4. **Einheiten sind festgelegt** und werden nie umgestellt: thermische
   Leistung in W, Energie in kWh, Temperaturen in °C, Spreizung in K, COP
   dimensionslos, Wärmeverlust in W/K.
5. **Jeder Zahlenwert kommt mit seiner Datenbasis** — siehe unten.

## Messeingänge

Quelle beliebig: eine Modbus-Anbindung, eine herstellereigene Integration,
eigene Zähler. Die Analyse kennt die Quelle nicht.

| Feld | Pflicht | Einheit | Anmerkung |
|---|---|---|---|
| `vorlauf_temp` | ja | °C | gemessen, nicht aus dem Rücklauf gerechnet |
| `ruecklauf_temp` | ja | °C | |
| `durchfluss` | ja | l/h | andere Einheiten werden umgerechnet |
| `leistung_elektrisch` | ja | W | möglichst nur die Wärmepumpe, ohne Fremdlast |
| `aussentemperatur` | ja | °C | |
| `verdichter_frequenz` | nein | Hz | ohne sie wird die Taktung aus der Leistung geschätzt |
| `betriebsart` | nein | Text | trennt Heizen, Warmwasser und Abtauen |
| `preset` | ja | – | Schlüssel einer Datei in `waermepumpe/presets/` |
| `standby_w` | nein | W | 0 = Wert aus dem Preset |

Ohne `betriebsart` vermischen sich Heizen und Warmwasser in einer Kennzahl.
Zulässig, wertet aber die Datenbasis ab.

**Einheiten werden gelesen, nie geraten.** Maßgeblich ist
`unit_of_measurement` der verdrahteten Entity. Fehlt sie oder ist sie
unbekannt, ist das ein Konfigurationsfehler, der angezeigt wird — keine
Annahme. Ein stillschweigend angenommenes l/min statt l/h verfälscht jeden COP
um den Faktor 60, dieselbe Fehlerklasse wie eine Verwechslung von W und kW.

Thermische Leistung: `Durchfluss [l/h] × Spreizung [K] × 1,163 = Watt`.
Nicht Kilowatt. Glykol senkt den Faktor um einige Prozent und gehört ins
Preset.

## Veröffentlichte Rollen

| Rolle | Einheit | Zustandsklasse | Bedeutung |
|---|---|---|---|
| `cop_momentan` | – | measurement | verworfen bei zu kleiner Spreizung |
| `cop_soll` | – | measurement | Erwartung aus dem Preset bei aktuellen Bedingungen |
| `cop_soll_unsicherheit` | % | measurement | Modellfehler des Presets |
| `cop_abweichung` | % | measurement | Ist gegen Soll, negativ heißt schlechter |
| `waermeleistung` | W | measurement | thermisch |
| `waermemenge` | kWh | total_increasing | integriert über die Zeit |
| `spreizung` | K | measurement | |
| `durchfluss_ziel_prozent` | % | measurement | Zielvolumenstrom als Anteil des heutigen |
| `durchfluss_abweichung_prozent` | % | measurement | Abweichung vom Ziel, positiv = zu viel |
| `waermeverlust_koeffizient` | W/K | measurement | aus Regression |
| `takte` | – | total_increasing | Verdichterstarts, monoton zählend |
| `laufzeit_summe` | h | total_increasing | Verdichterlaufzeit, monoton zählend |
| `laufzeit_mittel` | min | measurement | abgeleiteter Anzeigewert |
| `empfehlung_fusspunkt` | °C | measurement | Heizkurve |
| `empfehlung_steilheit` | – | measurement | Heizkurve |
| `empfehlung_vorlauf_min` | °C | measurement | Heizkurve |
| `datenbasis` | Text | – | Güte der Messung |
| `datenbasis_empfehlung` | Text | – | Länge der Beobachtung |
| `verwerfungsgrund` | Text | – | warum der letzte Messwert verworfen wurde |

### Warum die Zustandsklassen nicht beliebig sind

`takte` und `laufzeit_summe` sind der Grund für die Unterscheidung: das
Stundenmittel einer Startzahl ist bedeutungslos. Sie laufen als monoton
wachsende Zähler, und jede Aussage über einen Zeitraum entsteht aus der
**Differenz** der Zählerstände, nie aus einem Mittelwert. `laufzeit_mittel` ist
ein Anzeigewert; für den Langzeitverlauf hinter `hinweis_taktung_hoch` zählen
die Zähler.

Die Zähler überleben Neustarts (`Store` für Takte, `RestoreEntity` für die
Wärmemenge). Ein `total_increasing`-Zähler, der bei jedem Neustart auf null
fällt, ist schlimmer als keiner: die Langzeitstatistik deutet den Rücksprung
als neuen Zyklus und addiert den alten Stand dazu.

## Hinweise

Je Hinweisart ein eigener `binary_sensor` mit stabiler Kennung, nicht eine
Liste in einem Attribut — nur so bleiben sie in der Registry verankert und in
Automationen adressierbar.

- `hinweis_spreizung_niedrig` — Umwälzpumpe fördert mehr als nötig
- `hinweis_spreizung_hoch` — Durchfluss zu gering
- `hinweis_taktung_hoch`
- `hinweis_vorlauf_zu_hoch`
- `hinweis_effizienz_unter_erwartung`
- `hinweis_temperaturen_identisch` — Messproblem, kein Anlagenproblem
- `durchfluss_geschaetzt` — der Volumenstrom kommt aus dem Preset

Jeder Hinweis hat Ein- und Ausschaltschwelle, nie eine einzelne, und wird über
Tage gemittelt statt je Zyklus ausgewertet. Ein Hinweis, der im Abfragetakt
kippt, ist kein Hinweis, sondern Flackern.

Ohne Analyse ist ihr Zustand `None` und nicht `off`: ein Hinweis, der „alles in
Ordnung" meldet, während gar nichts gemessen wird, wäre eine Falschaussage.

### Zielwerte

Zu den beiden Spreizungshinweisen gehört eine Zahl, sonst bleibt offen, um wie
viel. Sie folgt daraus, dass bei gegebener Wärmeleistung Volumenstrom und
Spreizung umgekehrt proportional sind (`Q = V̇ · ΔT · c`):

```
V̇_ziel / V̇_ist = ΔT_ist / ΔT_ziel
```

`ΔT_ziel` ist die Auslegungsspreizung aus dem Preset. Beispiel: gemessene 4 K
gegen 5 K Ziel ergeben 80 % — der Volumenstrom liegt 25 % zu hoch.

**Es ist eine Aussage über den Volumenstrom, nicht über die Pumpenstufe.** Eine
Umwälzpumpe fördert nicht linear zu ihrer Prozentanzeige, und ihre Kennlinie
ist hier nicht bekannt. „Volumenstrom auf 80 %" ist eine Zielgröße; „Pumpe auf
Stufe 80 %" wäre geraten.

Grundlage ist die über Tage gemittelte Spreizung, nie der Momentanwert. Und der
Zielwert entfällt, solange `hinweis_temperaturen_identisch` ansteht: aus zwei
Sensoren auf derselben Quelle wäre jede Zielangabe aus einem Messfehler
abgeleitet.

## Eingriffe des Energiemanagements

Wirft HEMS die Wärmepumpe wegen PV-Überschuss an oder hält es sie aus einer
Lastspitze heraus, ist das kein normaler Betrieb und darf die Erwartungsbasis
nicht prägen. Solange die Analyse ein eigenes Repository war, musste das über
zwei verdrahtete Entities laufen (`steuerung_aktiv`, `steuerung_grund`) — und
HEMS veröffentlichte sie nie, die Richtung war also nie verbunden.

Jetzt setzt der Koordinator sie direkt (`_analysen_koppeln`). Gemeldet wird
derzeit nur die **Taktschutz-Pause** als `sperre`. Alles andere läuft als
`normal` durch: HEMS stellt am Heizkreis Modus und Vorlauf, führt aber keine
Historie darüber, ob eine Stellung gerade vom PV-Überschuss getrieben war. Die
Meldung ist damit vollständig für den Fall, in dem HEMS die Anlage aktiv
anhält, und unvollständig für den Fall, in dem es sie anders fährt als sie
selbst gefahren wäre.

Die Werteliste ist offen nach oben. Ein unbekannter Grund wird wie `normal`
behandelt **und wertet die Datenbasis ab** — er wird weder still verworfen noch
führt er zu einem Fehler.

## Presets

Preset-Schlüssel ist ein **Modell, keine Marke**. Die Keymark-Datenbank führt
allein zwölf Zeilen für die LG-Therma-V-Reihe mit vier deutlich verschiedenen
Kennlinien; ein Profil je Marke wäre für drei davon schlicht falsch.

Ein Preset ist Datei, kein Code. Das Format ist JSON und nicht YAML, damit die
Analyse mit der Standardbibliothek auskommt und ohne Home Assistant testbar
bleibt:

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

Herkunft und Lizenz der Kennwerte: [ATTRIBUTION.md](../ATTRIBUTION.md).

## Datenhaltung

Die Langzeitstatistik von Home Assistant trägt fast alles: Sensoren mit
`state_class` behalten dauerhaft Stundenmittel, und zwei Reihen lassen sich
über den Statistik-Zeitstempel wieder zusammenführen. Ein eigener
Langzeitspeicher wäre eine Kopie davon.

1. **Fein, im Abfragetakt, flüchtig.** Ein Ringpuffer über 48 Stunden. Er trägt
   nur, was unterhalb der Stunde passiert: Verdichterstarts, Laufzeit je Takt
   und die Gültigkeitsprüfung.
2. **Grob, dauerhaft, geschenkt.** Die geprüften Kennzahlen laufen als eigene
   Sensoren in die Langzeitstatistik. Regression und Erwartungsvergleich lesen
   von dort zurück — allerdings aus den **Quell**-Sensoren, nicht aus den
   eigenen: sonst könnte die Analyse erst rechnen, wenn sie schon gerechnet
   hat.

Entscheidend ist die Reihenfolge: **erst prüfen, dann mitteln.** Messwerte mit
zu kleiner Spreizung, aus der Abtauung, aus der Warmwasserbereitung oder vom
bloßen Anlaufsockel sind kein Heizbetrieb. Sie werden im Abfragetakt verworfen,
*bevor* gemittelt wird. Ein Stundenmittel, in das sie eingehen, lässt sich
nachträglich nicht mehr retten.

### Eigener Abfragetakt

Die Analyse läuft alle 30 Sekunden, der Planer minütlich. Das ist kein
Versehen: ein Verdichtertakt kann kürzer als zwei Minuten sein, und was
zwischen zwei Abfragen anläuft und wieder ausgeht, fehlt in `takte` für immer.
Ein `total_increasing`-Zähler ist nicht nachtragbar.

## Zwei Datenbasen

Vier Stufen, aufsteigend: `keine_daten`, `unzureichend`, `vorlaeufig`,
`belastbar`. Abwertung ist immer erlaubt, Aufwertung nie.

Es gibt sie **zweimal**, weil zwei verschiedene Dinge gemeint sind:

- **`datenbasis`** — wie sauber gerade gemessen wird. Wird abgewertet durch
  fehlende Betriebsart, ein generisches Preset, Bedingungen außerhalb des
  Kennfelds und fremdgesteuerten Betrieb.
- **`datenbasis_empfehlung`** — wie lange schon beobachtet wurde. Steuert die
  Heizkurvenempfehlung.

In einen Wert zusammengeworfen sähe ein tadellos gemessener COP wochenlang
wertlos aus, nur weil die Historie für eine Kurvenempfehlung noch nicht reicht.
Der Datenblattvergleich verlangt als einzige Aussage **beides**: eine saubere
Messkette und genug Historie.

„Noch zu wenig Daten" ist damit über Wochen ein regulärer, erwarteter Zustand —
kein Fehler und keine leere Anzeige.

## Übernahme der Empfehlungen

Option am Heizkreis: **Heizkurve aus der Wärmepumpen-Analyse übernehmen**,
voreingestellt aus. Ist sie an, fährt HEMS nach `empfehlung_fusspunkt`,
`empfehlung_steilheit` und `empfehlung_vorlauf_min` statt nach den
konfigurierten Werten. Die Logik steht in `strategies/kurve.py`.

**Warum das gedämpft sein muss.** Die Empfehlung entsteht aus Betrieb, den HEMS
mit der vorigen Empfehlung selbst erzeugt hat. Das ist eine echte
Rückkopplung: Senkt HEMS die Kurve, misst die Analyse anschließend niedrigere
Vorläufe und schlägt wieder eine niedrigere vor. Ohne Dämpfung wandert die
Kurve, bis das Haus kalt ist — und jeder einzelne Schritt sähe dabei begründet
aus.

Drei Bremsen, die zusammen wirken:

1. **Nur bei `datenbasis_empfehlung == belastbar`.** Nicht `datenbasis` — die
   eine sagt, wie sauber gerade gemessen wird, die andere, wie lange schon
   beobachtet wurde. Für eine Kurve zählt die zweite.
2. **Höchstens einmal in 24 Stunden.** Nach einer Änderung muss das Haus erst
   in den neuen Zustand kommen, bevor die nächste Messung überhaupt etwas
   Neues aussagt.
3. **Erst ab 1,0 K Fußpunkt oder 0,05 Steilheit.** Darunter ändert sich am
   geschriebenen Sollwert nichts — die Aktuierung schreibt auf ganze Grad.

Dazu zwei Randfälle:

- **Fällt die Datenbasis ab, bleibt die zuletzt übernommene Kurve stehen.** Sie
  war belastbar, als sie kam; auf die konfigurierten Werte zurückzuspringen
  wäre eine zweite Änderung ohne neue Erkenntnis, ausgerechnet dann, wenn die
  Messkette gerade ausgefallen ist.
- **Werte außerhalb des Konfigurationsbereichs werden begrenzt, nicht
  verworfen.** Eine Regression über wenige Wochen kann Unsinn liefern, ohne
  dass die Datenbasis das merkt — sie misst die Länge der Beobachtung, nicht
  die Plausibilität des Ergebnisses.

**Nicht übernommen wird `vlt_min_cold_c`**, die Untergrenze bei tiefen
Außentemperaturen. Sie ist eine Komfort- und Sicherheitsentscheidung über den
Absenkbetrieb, keine Aussage über das Wärmeabgabesystem.

**Bei mehreren Analyse-Rollen wird nichts übernommen.** Welche davon den
Heizkreis beschreibt, ist nicht entscheidbar, und raten wäre hier teurer als
nichts zu tun.

**Die Übernahme überlebt Neustarts und Reloads.** Sie muss es: Jede
Optionsänderung lädt die Integration neu, und ohne eigenen Speicher wären
Zeitstempel und Vorwerte weg — die Tagesfrist gälte dann nur zwischen zwei
Reloads, und wer gerade an der Konfiguration schraubt, löst sie am häufigsten
aus. Was dabei **nicht** überlebt, sind die Hinweis-Latches und der
48-Stunden-Ringpuffer der Analyse; die Hinweise bauen sich danach neu auf. Die
Datenbasis der Empfehlung ist davon unberührt, sie hängt an der
Langzeitstatistik.

Was gerade gilt, steht als Attribut an `sensor.hems_heizkreis`:
`kurve_quelle` (`konfiguriert` / `empfehlung` / `wartet`), `kurve_grund` im
Klartext, dazu `kurve_fusspunkt_c` und `kurve_steilheit`.

### Was nicht zurückfließt

Der `waermeverlust_koeffizient` speist die Bedarfsprognose **nicht**. Das sieht
nach einer Lücke aus, ist aber keine: HEMS lernt sein Wärmepumpen-Modell
(`k_w_per_k`) direkt aus der eigenen Langzeitstatistik als **elektrische**
Leistung je Kelvin Heizgradstunde. Der Koeffizient hier ist **thermisch**; die
Umrechnung liefe über einen COP, der mit der Außentemperatur wandert. Eine
direkt gemessene Größe gegen eine abgeleitete zu tauschen, würde die Prognose
verschlechtern.
