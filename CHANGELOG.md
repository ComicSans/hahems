# Änderungen mit Anpassungsbedarf

Nur Umbenennungen und Umstellungen, die nach einem Update eine manuelle
Anpassung erfordern. Die vollständige Historie steht in den
[Releases](https://github.com/ComicSans/hahems/releases).

## 2.0.0 — HEMS konzentriert sich auf das Akku-Management

Die Rollen **Heizkreis** und **Wärmepumpen-Analyse** sind entfallen. HEMS
plant und schaltet Speicher, Warmwasser sowie schaltbare und modulierbare
Lasten; einen witterungsgeführten Heizkreis steuert es nicht mehr, und die
Effizienzmessung einer Wärmepumpe entfällt ersatzlos.

Damit gehen: der Reiter **Effizienz** im Panel, `sensor.hems_heizkreis`,
`binary_sensor.hems_warmepumpen_storung`, alle Analyse-Entitäten samt ihrer
Geräte, die Heizkurvenübernahme, der Taktschutz und die Taupunkt-Untergrenze
im Kühlbetrieb.

Eine Wärmepumpe bleibt als **schaltbare Last** vollständig regelbar — mit
Priorität, Mindestlauf- und Mindestpausenzeit und Überschussregelung. Die
Markierung „heizungsgekoppelt" gibt es weiter: Sie weist die Last im Lastfluss
als Wärmeerzeuger aus und hebt den Boden beim Lernen ihrer Leistungsaufnahme.

**Was sich an der Bedarfsprognose ändert:** Bisher wurde die Wärmepumpe aus
dem Lastprofil herausgerechnet und über ein Heizgradstunden-Modell gegen die
Wettervorhersage neu aufgeschlagen. Dieses Modell ist mit der Rolle Heizkreis
gegangen, denn es hing an deren Außentemperatur und Heizgrenze. Ihr Verbrauch
steckt jetzt wieder implizit im gelernten Lastprofil — er zählt also weiterhin
voll mit, folgt aber dem Mittel der letzten Wochen statt dem Wetter. Spürbar
ist das vor allem im Winter und in den saisonalen Übergängen: Nachtdefizit und
SoC-Prognose laufen einem Kälteeinbruch einige Tage hinterher, bis das Profil
nachgezogen hat.

**Zu tun:** nichts. Beim ersten Start nach dem Update entfernt HEMS die
Einträge beider Rollen aus der Konfiguration (Schema-Version 3). Die dazu
angelegten Entitäten verschwinden mit dem nächsten Neustart aus der Registry;
Dashboards und Automationen, die auf `sensor.hems_heizkreis`,
`binary_sensor.hems_warmepumpen_storung` oder eine Analyse-Entität verweisen,
laufen danach ins Leere und wollen von Hand aufgeräumt werden. Ihre Historie
bleibt in der Langzeitstatistik erhalten.

Wer den Heizkreis weiter über HEMS fahren will, bleibt auf 1.6.x.

### Warmwasser schaltet höchstens alle 30 Minuten

Bisher galt eine Mindestlaufzeit von 15 Minuten — und nur vor dem
*Abschalten*. Einschalten war ungebremst, ein Gerät konnte also unmittelbar
nach dem Abschalten wieder anlaufen; gegen Takten half die Regel damit genau
zur Hälfte. Jetzt braucht jede Ein-/Aus-Kante 30 Minuten Abstand zur vorigen.

Der Sollwert hängt ausdrücklich nicht daran: Ein laufendes Gerät folgt dem
Überschuss weiter im Minutentakt, der Wechsel zwischen Basis- und
Komfort-Sollwert ist ungebremst. Gemessen wird über `last_changed` des
Steuer-Entitys, also die letzte echte Schaltkante — gleich, wer sie ausgelöst
hat. Nach einem Neustart von Home Assistant ist der erste Schaltvorgang frei,
weil `last_changed` dann frisch gesetzt ist und ein kalter Speicher sonst eine
halbe Stunde kalt bliebe.

Auch Sperrzeit und Legionellenschutz warten auf den Abstand. Beide sind
kalendergesteuert und laufen über Stunden; eine Verzögerung von höchstens 30
Minuten fällt dort nicht ins Gewicht, und eine Ausnahme wäre ein zweiter
Regelpfad für einen Fall, der kein Takten verursachen kann.

**Zu tun:** nichts.

## 1.6.7 — Optionale Entity-Felder lassen sich im Panel wieder leeren

Eine einmal gesetzte optionale Rolle — etwa **Betriebsart** unter
Wärmepumpen-Analyse — ließ sich im Panel nicht mehr entfernen: Der
Entity-Picker des Frontends stellt den vorigen Wert wieder her, sobald man
seinen Text löscht und wegklickt. „Kein Wert" war damit gar nicht
ausdrückbar.

Optionale Entity-Felder haben jetzt einen eigenen Knopf zum Leeren. Am
Speichern ändert sich nichts: Fehlt der Schlüssel, wird das Gerät ohne ihn
gespeichert — das konnte das Backend die ganze Zeit, es kam nur nichts an.

**Zu tun:** nichts. Im Konfigurationsdialog von Home Assistant (Einstellungen →
Geräte & Dienste) gilt das Frontend-Verhalten weiterhin; dort ist das Panel
der verlässliche Weg.

## 1.6.6 — HEMS sagt jetzt, wenn die Anlage den Modus nicht übernimmt

Gemessen am 01.08.2026 an einer LG Therma V: HEMS empfahl Kühlen, schrieb den
Betriebsmodus, und die Anlage blieb auf „Aus". Zu sehen war davon nichts —
Sensor und Log zeigten weiter nur die Empfehlung, und die Drossel gegen
Bus-Spam wiederholte den Aufruf still alle fünf Minuten. Ein Befehl, der nicht
ankommt, sah damit genauso aus wie einer, der wirkt.

HEMS merkt sich jetzt, welchen Modus es zuletzt selbst geschrieben hat — nur
bei einem Aufruf, der die Drossel passiert hat —, und prüft nach zwei Minuten,
ob der Ist-Modus ihn zeigt. Wenn nicht: Attribut `modus_nicht_uebernommen` auf
`sensor.hems_heizkreis`, eine Zeile im Entscheidungs-Log und eine Warnung im
HA-Log.

Dieselbe Buchführung schließt eine zweite Lücke, die bisher niemand sehen
konnte: Meldet eine Anlage nach einem HEMS-„aus" weiter den alten Modus, stimmen
Ziel und Ist beim Wiedereinschalten überein — der Befehl wäre nie gestellt
worden. Weicht der zuletzt geschriebene Modus vom neuen Ziel ab, wird deshalb
einmal aktiv geschrieben.

**Zu tun:** nichts. Wer die Meldung sieht, sucht die Ursache an der Anlage —
HEMS schreibt weiter dagegen, kann den Befehl aber nicht erzwingen.

## 1.6.5 — Warmwasserladung schlägt den Heizkreis-Modus

Die Rolle Wärmepumpen-Analyse kannte nur **eine** Betriebsart-Entität. Viele
Anlagen führen Heizkreis-Modus und Warmwasserbereitung aber unabhängig: Der
Heizkreis steht auf Kühlen, und parallel läuft eine Speicherladung mit
Vorrang — der Modus zeigt dabei weiter den Heizkreis.

Neues optionales Feld **Warmwasserbereitung läuft**. Steht die Rückmeldung an,
zählt die Analyse den Betrieb als Warmwasser, was auch immer der Modus meldet.

**Zu tun:** Wer eine solche Rückmeldung hat, sollte sie eintragen. Ohne sie
zählt im Winter jede Speicherladung als Heizbetrieb — hoher Vorlauf, große
Spreizung, ganz anderer Arbeitspunkt — und verfälscht genau die Kennzahl, die
die Betriebsart schützen soll. An einer LG Therma V über Modbus ist das
`di09_warmwasserbereitung`.

## 1.6.4 — Die Analyse sagt jetzt, wenn ihr der Volumenstrom fehlt

Ohne Durchfluss-Sensor und ohne Nennvolumenstrom verwarf die Analyse jede
Messung mit `kein_durchfluss` — dauerhaft, und weder Datenbasis noch ein
Hinweis zeigten darauf. Jetzt steht der Grund beim Start als Warnung in
`binary_sensor.hems_konfiguration`, samt dem, was trotzdem weiterrechnet.

**Zu tun:** nichts. Wer die Warnung sieht, entscheidet: Zähler verdrahten,
Nennvolumenstrom eintragen — oder bewusst auf den COP verzichten. Bei einer
modulierenden Umwälzpumpe ist Letzteres die richtige Wahl: ein fester Nennwert
wäre dort um ein Vielfaches daneben, und der COP hängt linear daran.

## 1.6.3 — Nennvolumenstrom für Anlagen ohne Zähler

Ohne Volumenstromzähler sollte die Analyse auf den Nennvolumenstrom des
Presets zurückfallen. **Die sechs generischen Presets führen aber keinen** —
er hängt an Umwälzpumpe und Hydraulik, nicht am Gerätemodell. Wer ein
generisches Profil gewählt hat und keinen Zähler besitzt, bekam deshalb
dauerhaft `kein_durchfluss`: keine Wärmeleistung, kein COP, keine Wärmemenge,
und nichts an der Anzeige sagte, woran es lag.

Die Rolle Wärmepumpen-Analyse hat dafür jetzt das Feld **Nennvolumenstrom
ohne Zähler (l/h)**, neben dem Standby-Sockel. Er ist ablesbar an der Pumpe
(m³/h × 1000) oder aus einem bekannten Betriebspunkt zu rechnen:
`Wärmeleistung [W] ÷ (Spreizung [K] × 1,163)`.

**Zu tun:** Wer eines der vier LG-Profile nutzt oder einen Zähler verdrahtet
hat, muss nichts tun. Alle anderen tragen den Wert nach, sonst bleibt der COP
dauerhaft leer.

## 1.6.2 — Kein Fehlalarm mehr nach dem Neustart

Nach einem Neustart meldete `binary_sensor.hems_konfiguration` jede fremde
Entität als „existiert nicht" — an einer Anlage einundzwanzig auf einmal, quer
über Speicher, Wärmepumpe und Steckdosen. Keine davon fehlte wirklich: Die
Prüfung fragt `hass.states` ab, und die füllt sich erst, während die
Integrationen der Reihe nach laden. Wenige Sekunden später war der Sensor
wieder grün.

Geprüft wird jetzt erst, wenn Home Assistant fertig hochgefahren ist. Solange
steht im Attribut `hinweise`, dass die Prüfung noch aussteht, und
`bereit_fuer_auto` ist **falsch** statt wahr — ungeprüft ist nicht dasselbe wie
fehlerfrei.

**Zu tun:** nichts. Wer eine Automation auf `bereit_fuer_auto` triggern lässt,
sollte wissen, dass es in den ersten Sekunden nach einem Neustart nun falsch
statt wahr meldet.

## 1.6.1 — Der Durchfluss ist wieder optional

In 1.6.0 war das Feld **Durchfluss** der Rolle Wärmepumpen-Analyse Pflicht.
Wer keinen Volumenstromzähler hat — an vielen Anlagen ist er über die
Anbindung gar nicht erreichbar — kam am Formular nicht vorbei.

Er ist jetzt wieder optional, wie in `wp-optimization`. Fehlt er, rechnet die
Analyse mit dem Nennvolumenstrom aus der Gerätekennlinie, meldet „Durchfluss
geschätzt" und weist den COP nie als belastbar aus.

**Zu tun:** nichts, außer aktualisieren. Wer 1.6.0 bereits eingerichtet hat,
behält seine Konfiguration.

## 1.6.0 — WP-Optimierung ist in HEMS aufgegangen

Die eigenständige Integration `wp-optimization` gibt es nicht mehr. Ihre
Effizienzanalyse ist jetzt die HEMS-Rolle **Wärmepumpen-Analyse**
(Konfigurieren → Wärmepumpen-Analyse hinzufügen).

**Zu tun:**

1. `wp-optimization` in Home Assistant entfernen (Einstellungen → Geräte &
   Dienste) und in HACS deinstallieren.
2. In HEMS die Rolle Wärmepumpen-Analyse anlegen und dieselben fünf Entitäten
   verdrahten: Vorlauf, Rücklauf, Durchfluss, elektrische Leistung,
   Außentemperatur. Preset wie zuvor.
3. Automationen und Karten auf die neuen `entity_id`s umstellen. Aus
   `sensor.wp_optimierung_cop_momentan` wird `sensor.hems_<name>_cop_momentan`
   mit dem Namen, den die Rolle bekommt.

**Die Zählerstände beginnen neu.** `takte`, `laufzeit_summe` und `waermemenge`
sind neue Entitäten und starten bei null. Die alten Werte bleiben in der
Langzeitstatistik der alten Entitäten erhalten, solange die nicht gelöscht
werden — zusammenführen lassen sie sich nicht.

Was sich fachlich geändert hat: Die Analyse läuft jetzt im eigenen 30-s-Takt
statt im Minutentakt des Planers, und HEMS meldet ihr die eigene
Taktschutz-Pause. Der Reiter **Effizienz** im Panel erscheint wie bisher, sobald
eine Analyse konfiguriert ist.

## 1.0.5 — Sensor-Attribute ausgeschrieben

Attribute mit den Präfixen `wp_` und `ww_` heißen jetzt ausgeschrieben
`waermepumpe_` und `warmwasser_` (z. B. `wp_modus` → `waermepumpe_modus`,
`ww_soll_c` → `warmwasser_soll_c`).

**Zu tun:** Wer diese Attribute direkt in Lovelace-Karten oder Templates
referenziert (`state_attr(...)`), muss die Namen manuell anpassen. Attribute
sind — anders als eine `entity_id` — nicht in der Entity-Registry verankert und
ändern sich sofort mit dem Update; eine Karte, die den alten Namen liest, wird
still leer.

Die `entity_id`s selbst sind unberührt: Sie waren schon vorher ausgeschrieben
(`sensor.hems_warmwasser_soll`) und hängen nicht an diesen Präfixen.

## 0.6.0 — `hems_einspeiseplan` heißt `hems_entladeplan`

`sensor.hems_einspeiseplan` heißt jetzt `sensor.hems_entladeplan`.
„Einspeisung“ meinte fälschlich Netzeinspeisung, gemeint war immer die
Akku-Entladung ins Haus.

**Zu tun:** Wer die Entität in Lovelace-Karten, Templates oder Automationen
referenziert, muss den Namen anpassen. Die alte Entität bleibt sonst als „nicht
verfügbar“ in der Entity-Registry zurück und sollte gelöscht werden.
