# Das Lint-Gate existiert nur als Regel, nicht als Skript

**Priorität:** P2 — kostet keine Funktion, aber jeder Agent löst es anders.

## Befund (07.09.2026)

`.claude/workflow.md` sagt unter „Gates":

> Jede Prüfung, die ein Skript sein kann, ist ein Skript unter `scripts/`, und
> jedes Skript hängt an einem Hook. Ein Gate, das niemand aufruft, ist
> Dekoration.
>
> Das Lint-Gate ist **Layer 1 des Reviews** und läuft, bevor ein Coder fertig
> meldet: deterministisch, in Sekunden, für null Token, und es fängt, was ein
> Modell nur manchmal fängt.

In `scripts/` liegt genau eine Datei: `mirror-setup.sh`. Es gibt kein
Lint-Skript und keinen Hook, der eines aufriefe.

Beobachtet an drei Codern hintereinander (Subtasks A, B und C der Aufgabe
„Speicher-Selbstsperre über den Lade-Pfad"): Jeder suchte zuerst nach
`scripts/lint.sh`, fand nichts, wich auf ein direktes `ruff check` aus und
meldete das als Abweichung. Zwei von ihnen prüften die Findings von Hand gegen
die HEAD-Baseline, um vorbestehende nicht als eigene zu melden — dieselbe
Arbeit, dreimal neu erfunden, jedes Mal auf Token bezahlt.

Die Baseline ist nicht leer: `ruff check` meldet auf HEAD sechs vorbestehende
Findings (Import-Sortierung in `coordinator.py` und `strategies/battery.py`,
zwei `BLE001` blinde Excepts, ein `RUF100`, ein `C408 dict()`). Solange die
stehen, kann kein Skript einfach auf „exit != 0 = rot" gehen; es braucht
entweder eine bereinigte Baseline oder einen Vergleich gegen sie.

## Was zu entscheiden ist

- Die sechs Findings bereinigen und das Gate hart machen, oder eine
  Baseline-Datei führen und nur neue Findings rot werten. Ersteres ist
  ehrlicher und macht das Skript trivial; zweiteres ist schneller zu haben.
- Welcher Hook das Skript aufruft. Ein Gate ohne Hook ist laut Arbeitsmodell
  Dekoration, und genau als solche hat es sich hier verhalten.

## Abnahme

- [ ] `scripts/` enthält ein Lint-Skript, das ohne Argumente läuft und dessen
      Exit-Code die Aussage trägt.
- [ ] Ein Hook ruft es auf, bevor ein Coder fertig meldet.
- [ ] Ein Coder, der es benutzt, muss die Findings nicht mehr selbst gegen die
      Baseline gegenprüfen — das Skript tut es oder die Baseline ist leer.
- [ ] Die sechs vorbestehenden Findings sind bereinigt oder ausdrücklich als
      Baseline vermerkt.
