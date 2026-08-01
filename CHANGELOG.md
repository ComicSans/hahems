# Änderungen mit Anpassungsbedarf

Nur Umbenennungen und Umstellungen, die nach einem Update eine manuelle
Anpassung erfordern. Die vollständige Historie steht in den
[Releases](https://github.com/ComicSans/hahems/releases).

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
