# Änderungen mit Anpassungsbedarf

Nur Umbenennungen und Umstellungen, die nach einem Update eine manuelle
Anpassung erfordern. Die vollständige Historie steht in den
[Releases](https://github.com/ComicSans/hahems/releases).

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
