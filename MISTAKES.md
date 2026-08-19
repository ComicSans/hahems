# MISTAKES.md

Mistakes in this project that cost time. Every session reads this file before it
plans or changes anything, and writes its own mistakes here before it reports
done. The rules: `project_standards rule:"learning.mistakes-log"` and
`rule:"learning.mistakes-read"`.

The format, deliberately narrow:

- One `###` entry per mistake, newest on top.
- The heading is the trigger in one line — it is the only part that reaches the
  next session at startup.
- Three fields, none optional: **What happened**, **Trigger**, **Fix**.
- No entry without a trigger and a fix. Without the trigger the next session does
  not know what to watch for.
- An entry that turns out wrong or obsolete is corrected or deleted with one line
  saying why. A wrong entry costs more than a missing one.

<!-- Template, copy and fill in, then leave this comment in place:

### YYYY-MM-DD Short trigger in one line

- **What happened:** [what went wrong, one sentence]
- **Trigger:** [what caused it and how it could have been spotted first]
- **Fix:** [what resolved it, with the file or command]

-->

### 2026-08-19 Zwei Auslöser sperren nur, wenn sie unabhängig sind — hier teilten sie die Ursache

- **What happened:** Abends zog das Haus 800 W aus dem Netz, während die drei
  Hyper 2000 bei 99 % standen und in HA einwandfrei erreichbar waren.
  `sensor.hems_speicher_regelung` meldete wieder `pausiert` mit
  `abgemeldet: [L1, L2, L3]` — dasselbe Bild wie am 17.08., obwohl dessen Fix
  in Version 2.5.2 lief. `sensor.hems_entladeplan` stand derweil auf 534 W.
  Die erste Diagnose („Zendure-Integration ausgefallen") war erneut falsch.
- **Trigger:** Ein voller Akku bei laufendem Überschuss. Der Fix vom 17.08.
  verlangt für die Verriegelung zwei Auslöser — die SoC-Entität schweigt UND
  der Speicher folgt einem Befehl ≠ 0 nicht. Beim vollen Akku haben beide
  dieselbe Ursache: Er ruht, also meldet der push-Sensor nichts, und er nimmt
  keine Ladung mehr an, also „folgt" er dem Ladebefehl nicht, den ihm die
  Zuteilung aus der Restkapazität bis zum Deckel trotzdem schickt. Die zweite
  Bedingung sichert die erste damit nicht ab, sie begleitet sie.
  Zu sehen war es an den Zeitstempeln: Jeder Speicher verriegelte auf die
  Minute genau 15 Minuten nach seiner letzten Meldung (L1 13:18 → 13:33,
  L3 14:03 → 14:18, L2 14:55 → 15:11 UTC), und im selben Zyklus lief noch ein
  Schreibvorgang auf sein `input_limit`. Ein echter Integrationsausfall
  verriegelt alle drei zugleich, nicht gestaffelt nach ihrer eigenen Ruhezeit.
- **Fix:** `ladeauftrag_in_frist_erfuellbar` in `actuation.py` — beim Laden
  quittiert der Actuator nicht mehr, wenn die freie Kapazität bis zur
  Ladegrenze kleiner ist als das, was die zugeteilte Leistung in
  `SPEICHER_QUITTUNG_FRIST` liefern würde. Wer fertig wird, bevor die Frist
  abläuft, darf danach schweigen. Bewusst gegen die Zuteilung gerechnet und
  nicht gegen einen SoC-Abstand: 36 Wh Rest sind bei 800 W nach drei Minuten
  weg, bei 60 W nicht. Der Entlade-Zweig bleibt unangetastet — dort ist „voll"
  gerade die Bedingung, unter der geliefert werden muss (15.08.).
  `tests/test_speicher_selbstsperre.py` deckt beide Richtungen ab.
- **Sofortmaßnahme im Betrieb:** Die Verriegelung lebt als `_speicher_stumm` im
  Coordinator, ein Reload des Config Entry leert sie. Danach fiel der Netzbezug
  binnen einer Minute von 736 W auf 12 W.

### 2026-08-17 `last_reported` als Lebenszeichen — bei push-Integrationen ist es keins

- **What happened:** Am Morgen lief der Hausverbrauch (~800 W) komplett über
  das Netz, obwohl die Speicher bei 92 % standen. `sensor.hems_speicher_regelung`
  meldete `pausiert`, `soll_w = 0`, `abgemeldet: [L1, L2, L3]` — obwohl alle
  drei Hyper-2000 in HA `available` waren und einwandfrei liefen. Erste
  Diagnose („Zendure-Integration ausgefallen, Reload nötig") war falsch.
- **Trigger:** Ein voller, ruhender Speicher. Die Zendure-Integration setzt
  `_attr_should_poll = False` und ruft `schedule_update_ha_state()` nur bei
  Wertänderung — `last_reported` verhält sich damit wie `last_changed`. Ein
  Akku, der nichts tut, ändert keinen Wert und gilt nach
  `STORAGE_STALE_MIN = 15` min als abgemeldet. Daraus wird eine Selbstsperre:
  abgemeldet → HEMS pausiert → Akku bleibt ruhig → nie wieder eine Änderung.
  Zu sehen war es an der *Streuung* der Alter: 223 Zendure-Entitäten verteilt
  über ~15 verschiedene `last_reported`-Stufen (121 bis 746 min). Ein echter
  Ausfall hinterlässt einen einzigen Zeitpunkt, nicht fünfzehn.
- **Fix:** `HemsCoordinator._stumm` verlangt beide Hälften — die SoC-Entität
  schweigt UND der Speicher ist einem Befehl ≠ 0 nicht gefolgt. Die zweite
  Hälfte liefert die Quittung des Actuators (`_quittung_speicher`,
  `plan.speicher_nicht_uebernommen`), die den WERT des Leistungssensors liest
  statt dessen Alter und deshalb unabhängig davon ist, wann eine Integration
  schreibt. Ohne Leistungssensor verriegelt nie etwas.
  `tests/test_speicher_selbstsperre.py` prüft die Übergänge.
  Siehe Korrektur am Eintrag vom 15.08.2026.
- **Beinahe-Folgefehler:** Der erste Anlauf las `nicht_gefolgt` in jedem Zyklus
  neu und verriegelte nicht. Das hätte den 15.08. getaktet wiederholt: Ein
  abgemeldeter Speicher bekommt 0 W, `_apply_battery` leitet
  `laden_soll`/`entladen_soll` aus der Zuteilung ab, `_quittung_speicher` steigt
  bei 0 W sofort aus — die Abmeldung löscht also ihren eigenen Beweis, und der
  Ausfall kehrt alle 5 Minuten in die Zuteilung zurück. **Merke:** Wird ein
  Befund aus einer Reaktion abgeleitet, die der Befund selbst unterbindet,
  gehört er verriegelt, und das Entriegeln an ein Signal, das unabhängig davon
  weiterläuft (hier: eine frische Meldung). Ein Test über einen einzigen Zyklus
  sieht davon nichts — `speicher_stumm_latch` steht HA-frei in
  `strategies/types.py`, damit die Übergänge über mehrere Zyklen prüfbar sind.

### 2026-08-16 Ein neuer Betriebsmodus neben `auto` — die zweite Prüfung wird vergessen

- **What happened:** Beim Einbau von `invers-auto` war zuerst nur
  `self.mode == MODE_AUTO` in `_async_update_data` gewidert. Die Zwillings-
  prüfung `self._prev_mode == MODE_AUTO` zwei Zeilen tiefer wäre stehen
  geblieben — dann gibt `release_battery` den Akku beim Wechsel invers-auto →
  beobachten/aus nicht mehr frei, und er läuft mit der zuletzt kommandierten
  Rate blind weiter. Genau der Fall, für den `release_battery` existiert. Vor
  dem Commit gefunden, gekostet hat es nichts.
- **Trigger:** Jeder neue Wert neben `MODE_AUTO`. Der Betriebsmodus wird an
  mehr als einer Stelle geprüft, und die zweite (Verlassen, Freigabe) ist
  stumm — kein Test schlägt an, kein Log meldet etwas.
- **Fix:** Die Modi, in denen geschrieben wird, stehen als `MODES_ACTUATING` in
  `const.py`; alle Prüfungen laufen über die Liste. `tests/test_invers_modus.py
  ::test_beide_modus_pruefungen_laufen_ueber_die_liste` liest das über den
  Syntaxbaum und verbietet einen direkten Vergleich gegen `MODE_AUTO`.

### 2026-08-15 Snapshot-Feld in `changelog.py` gebaut, aber nicht in `_DECISION_FIELDS` eingetragen

- **What happened:** `akku_quittung` wurde am 14.08.2026 in `decision_snapshot`
  gefüllt und war nie im Entscheidungs-Log zu sehen. `diff_snapshots` läuft
  ausschließlich über `_DECISION_FIELDS`; ein Schlüssel, der dort fehlt, wird
  gebaut und stillschweigend verworfen. Der Test prüfte nur, dass der Name
  irgendwo in `changelog.py` vorkommt — und war grün.
- **Trigger:** Ein neues `snap[...] = (...)` in `decision_snapshot`. Zu sehen
  wäre es am HEMS-Log gewesen: Der Eintrag taucht dort nie auf, auch wenn der
  Fall eintritt.
- **Fix:** Schlüssel zusätzlich in `_DECISION_FIELDS` eintragen. Der Test liest
  die Tabelle jetzt über den Syntaxbaum statt per Textsuche
  (`tests/test_speicher_abgemeldet.py::_feldtabelle`).

### 2026-08-15 Eingefrorene Entität ist nicht `unavailable` — Diagnose braucht `last_reported`

- **What happened:** Ein ausgefallener Speicher lieferte weiter gültige
  Zustände, nur keine neuen. Weder `_state()` (prüft `unavailable`/`unknown`)
  noch die Historie (zeigt nur Änderungen) machten das sichtbar; die Ursache
  war erst über `last_reported` zu sehen.
- **Trigger:** Ein Gerät „tut nichts", obwohl HEMS regelt, und die Sensoren
  sehen plausibel aus. Erster Griff: `last_reported` aller beteiligten
  Entitäten vergleichen, nicht `state` oder `last_changed`.
- **Korrektur 17.08.2026:** Der Fix trägt nur bei *pollenden* Quellen. Die
  Zendure-Integration setzt `_attr_should_poll = False` und schreibt den State
  ausschließlich bei Wertänderung (`sensor.py`: `if new_value !=
  self._attr_native_value: … schedule_update_ha_state()`). Dort verhält sich
  `last_reported` wie `last_changed`, und `_abgemeldet` misst „Wert steht
  still" statt „Gerät ist stumm" — siehe Eintrag vom 17.08.2026.
- **Fix:** `HemsCoordinator._abgemeldet` prüft `last_reported` gegen
  `STORAGE_STALE_MIN`. Achtung bei der Umkehrung: Ein eingefrorenes
  `last_reported` an einer *Stell*-Entität beweist NICHT, dass HEMS nicht
  schreibt — Integrationen, die den Zustand erst nach Geräte-Echo setzen,
  bewegen ihn beim Schreiben gar nicht.
