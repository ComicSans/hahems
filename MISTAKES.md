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
- **Fix:** `HemsCoordinator._abgemeldet` prüft `last_reported` gegen
  `STORAGE_STALE_MIN`. Achtung bei der Umkehrung: Ein eingefrorenes
  `last_reported` an einer *Stell*-Entität beweist NICHT, dass HEMS nicht
  schreibt — Integrationen, die den Zustand erst nach Geräte-Echo setzen,
  bewegen ihn beim Schreiben gar nicht.
