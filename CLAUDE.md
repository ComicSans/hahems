# CLAUDE.md

Hinweise für Claude Code (claude.ai/code) zur Arbeit in diesem Repository.

## Was das ist

**WP-Optimierung** — eine Home-Assistant-Integration, die die Effizienz einer
Wärmepumpe misst und Verbesserungen benennt. Geräteunabhängig: sie nimmt
Sensorwerte entgegen und kennt weder Protokolle noch Register.

Einer von drei Bausteinen, die sich ergänzen und einzeln funktionieren:
Energiemanagement (HEMS) misst, denkt und steuert; die Modbus-Anbindung
verbindet; dieses Repository optimiert. Verbunden über
[docs/kontrakt-v1.md](docs/kontrakt-v1.md).

## Diese Integration schreibt nie an die Anlage

Kein Aktuierungspfad, keine Steuer-Entities, kein Auto-Modus. Empfehlungen
werden veröffentlicht, umgesetzt werden sie vom Energiemanagement.

Das ist keine vorläufige Einschränkung, sondern die Trennlinie zwischen den
Repositories. Wer hier eine Schreiboperation einbaut, hebt sie auf — und
schafft die Möglichkeit, dass zwei Integrationen gleichzeitig denselben
Sollwert einer realen Heizung stellen.

## Bauen und Testen

```sh
./.venv-test/bin/python -m pytest
./.venv-test/bin/python -m pytest tests/test_thermal.py
```

Es braucht keine Home-Assistant-Instanz.

## Aufbau

Die Trennlinie, auf die es ankommt, ist **HA-freie Fachlogik gegen
HA-Schicht**:

```
custom_components/wp_optimization/
  analysis/         Fachlogik, frei von Home Assistant
    types.py        Laufzeittypen und der Schmitt-Trigger
    thermal.py      thermische Leistung, COP, Gültigkeitsprüfung
    presets.py      Kennlinien laden, Erwartung berechnen
    cycling.py      Verdichterstarts und Laufzeit
    curve.py        Wärmeverlust und Heizkurvenvorschlag
    hints.py        Hinweise mit Hysterese
    evaluate.py     Auswertelauf, reine Funktion
  presets/          Gerätekennlinien als JSON
  const.py          Konstanten der HA-Schicht
  __init__.py       HA-Schicht (derzeit Platzhalter)
```

Regeln, die das zusammenhalten:

- **Kein Modul unter `analysis/` importiert `homeassistant`.** Sobald eines
  es tut, fällt es aus der Testsuite heraus.
- **`analysis/types.py` importiert nur aus der Standardbibliothek**, nie aus
  einem anderen Analysemodul. Es ist die gemeinsame Heimat der Laufzeittypen,
  damit kein Importzyklus entstehen kann.
- Presets sind JSON und nicht YAML, damit die Fachlogik ohne Fremdpakete
  auskommt.

## Wichtige Muster

**Erst prüfen, dann mitteln.** Messwerte mit zu kleiner Spreizung, aus
Abtauung, Warmwasserbereitung oder vom Anlaufsockel werden im Abfragetakt
verworfen — *bevor* gemittelt wird. Ein Stundenmittel, in das sie eingehen,
ist unbrauchbar und nachträglich nicht mehr zu retten.

**Jede Ja-Nein-Entscheidung hat zwei Schwellen, nie eine.** `latch` in
`analysis/types.py` ist ein Schmitt-Trigger, und die Konstanten kommen paarweise
(`SPREIZUNG_NIEDRIG_AN` / `..._AUS`). Eine einzelne Schwelle lässt die
Entscheidung um sich herum flattern. Wer eine Entscheidung ergänzt, ergänzt
beide Schwellen.

**Zwei Datenbasen, nicht eine.** Die Güte der Messung und die Länge der
Beobachtung sind verschiedene Aussagen und werden getrennt geführt. In einen
Wert zusammengeworfen sähe ein sauber gemessener COP wochenlang wertlos aus.

**Presets sind modellscharf, nicht markenscharf.** Allein die Therma-V-Reihe
hat vier verschiedene Kennlinien.

**Qualitative Hinweise, keine Scheingenauigkeit.** „Umwälzpumpe drosseln"
statt „80 Prozent würden reichen" — die Pumpenkennlinie ist nicht bekannt.

## Fallstricke

**Einheiten werden gelesen, nie geraten.** Ein angenommenes l/min statt l/h
verfälscht jeden COP um Faktor 60. Fehlende oder unbekannte Einheit ist ein
Konfigurationsfehler, der angezeigt wird.

**Ein Umbenennen bricht Dashboards, Attribute brechen still.** Tragende Werte
sind Zustände, keine Attribute — Attribute sind nicht in der Registry
verankert, und eine Karte mit `state_attr(...)` wird schlicht leer.

**Der Kontrakt ist öffentliche Schnittstelle.** Eine Rolle zu entfernen,
umzubenennen oder umzudeuten erhöht die Kontraktversion.

## Barrierefreiheit

Bei einer diagrammlastigen Oberfläche kein Nachgang: kein Zustand allein über
Farbe, jedes Diagramm mit Textfassung seiner Kernaussage, Tastaturbedienung,
Kontraste nach WCAG AA auch im dunklen Erscheinungsbild.

## Offen

Die HA-Schicht: Konfigurationsdialog, Entities, Koordinator mit Ringpuffer,
Lovelace-Karte. `config_flow` steht im Manifest bewusst auf `false`, solange
es keinen Dialog gibt.

Zwei Punkte, die dabei leicht untergehen: Die Taktzähler kommen als
`TaktZustand` aus jedem Auswertelauf heraus und müssen über Neustarts hinweg
gehalten werden — ein Zähler, der bei jedem Neustart auf null fällt, ist als
`total_increasing` schlimmer als keiner. Und `Tagesbild` wird derzeit von
nichts befüllt; die Verdichtung über Tage ist Aufgabe der HA-Schicht, die
dafür aus der Langzeitstatistik zurückliest.

<!-- msc:standards:start -->

## Workspace standards

Generated from `standards.json` (mcp-server) - change it there and reinstall,
never inside the markers. `project_standards` serves the incident behind a rule
(`rule: "<id>"`, ask before weakening one) and the setup rules not printed here;
they bind the same.

### Working with the user

- **Result first, details on request** - Status in one sentence, then at most three bullet points. Reasons and alternatives only on request. No tables or subheadings for intermediate states, nothing repeated that already stands in a task, no em dash anywhere - hyphen instead. `collab.answers`
- **Be critical, and say so in one sentence** - Name contradictions, mistakes and missing information in one sentence rather than working around them. Never guess: ask while Tobias is reachable, decide autonomously offline and present the assumption later. `collab.not-a-yes-man`
- **Assume several sessions run in the same workspace** - Never assume a clean working tree or exclusive access to a device, a build or a file. Be frugal with memory and compute. `collab.parallel-sessions`
- **Neutral, gender-inclusive language and accessibility throughout** - Gender-inclusive wording and accessibility are requirements in every change, not a later pass. `collab.language`
- **Match the model to the job** - Agents run on Opus or Sonnet, whichever does the work reliably. An advisor always uses the stronger model available - Fable or Opus. `collab.models`

### Git

- **Work happens on `main`** - No feature branches. Commit to `main` directly, in small steps that keep it green. `git.trunk`
- **Claim files before editing them** - Claim via `memory_claim_files`, release when done. Rebase before pushing, never force-push `main`, never commit files you did not change. `git.parallel`

### Tooling

- **Code exploration goes through tokensave** - Its MCP tools, not file reads and not Explore agents; a PreToolUse hook enforces this. `tooling.tokensave`
- **iOS builds, tests, simulators and devices go through `simulator-broker`** - Never `xcodebuild`, `simctl` or `devicectl` directly - scripts and physical devices go through `simulator-broker/src/cli.mjs run --project <name> -- <command>`. `tooling.builds`
- **Throwaway work goes in the session scratchpad, named so housekeeping finds it** - Working copies, build output and coverage runs go in the session scratchpad, never in a repository or loose in `/tmp`; name build output `build/`, `Build/` or `DerivedData/`. `tooling.scratch`
- **Task state lives in agent-memory** - Never in `todo.md` or another markdown file. Writing a read-only export is fine; reading state back out of it is not. `tooling.state`
- **One active queue per project** - Everything a project has to do goes in that one queue; `dependsOn` is the only hard gate and resolves inside its own queue. `tooling.one-queue-per-project`
- **Questions for Tobias go to the queue `entscheidungen-tobias`** - Anything blocked on a decision by Tobias goes to the queue `entscheidungen-tobias` (project `tobias`), never into the project backlog - three lines: the decision, the options with consequences, what stands still. `tooling.decisions-queue`

<!-- msc:standards:end -->
