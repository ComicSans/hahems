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

Generated from `standards.json` in the mcp-server repository by
`scripts/install-claude-project-mcp.mjs`. Do not edit inside the markers — the
next install overwrites it. Change the rule centrally and reinstall.

Each rule is the binding form. Why a rule exists — the incident behind it, the
measurement, what was tried before — is deliberately not here: ask for it with
`project_standards` and `rule: "<id>"` before weakening or re-opening one.

### Working with the user

- **Result first, details on request** — Status in one sentence, then at most three bullet points. Reasons, alternatives and risks only when asked or when a decision depends on them. No line numbers or file names in prose, no tables or subheadings for intermediate states, nothing repeated that already stands in a task. Never an em dash — use a hyphen, in every text including commits, code comments and store copy. `collab.answers`
- **Be critical, and say so in one sentence** — Name contradictions, mistakes and missing information instead of working around them — briefly. Do not guess: ask while Tobias is reachable. Offline or in queue work, decide with the most plausible assumption, record it, present it later. `collab.not-a-yes-man`
- **Assume several sessions run in the same workspace** — Never assume a clean working tree or exclusive access to a device, a build or a file. Be frugal with memory and compute. `collab.parallel-sessions`
- **Neutral, gender-inclusive language and accessibility throughout** — Gender-inclusive wording in every text; accessibility is a requirement in every change, not a later pass. `collab.language`
- **Match the model to the job** — Agents run on Opus or Sonnet, whichever does the work reliably. An advisor always uses the stronger model available — Fable or Opus. `collab.models`

### Git

- **Work happens on `main`** — No feature branches. Commit to `main` directly, in small steps that keep it green. `git.trunk`
- **The main branch is called `main`** — Not `master`, not `develop`. The installer only reports a deviation and prints the commands — renaming touches the remote and any open work, so it stays manual. `git.branch-name`
- **Claim files before editing them** — Claim via `memory_claim_files`, release when done. Rebase before pushing, never force-push `main`, never commit files you did not change. `git.parallel`
- **A branch rename needs a tokensave follow-up** — Run `tokensave branch add main` and re-sync afterwards. `git.tokensave-rename`

### Tooling

- **MCP servers come from the installer** — `.mcp.json` is generated from `mcp.config.json` by `scripts/install-claude-project-mcp.mjs`. Edit the config and reinstall; never hand-edit `.mcp.json`. `tooling.mcp`
- **Code exploration goes through tokensave** — Its MCP tools, not file reads and not Explore agents. `tokensave init && tokensave install` in every repository. A PreToolUse hook enforces this. `tooling.tokensave`
- **iOS builds, tests, simulators and devices go through `simulator-broker`** — Never `xcodebuild`, `simctl` or `devicectl` directly. Shell scripts wrap their command in `node simulator-broker/src/cli.mjs run --project <name> -- <command>`; screenshot and preview-video scripts are the usual offenders. `tooling.builds`
- **Throwaway work goes in the session scratchpad, named so housekeeping finds it** — Working copies, measurement checkouts, build output and coverage runs belong in the session scratchpad, never in the repository and never loose in `/tmp`. Name build output `build/`, `Build/` or `DerivedData/` — `sim_housekeeping` recognises it by name; a directory called `dd` or `out` is never cleared. `tooling.scratch`
- **Task state lives in agent-memory** — Never in `todo.md` or another markdown file. Writing a read-only export is fine; reading state back out of it is not. `tooling.state`
- **One active queue per project** — Everything a project has to do goes into that one queue. `order` sorts within a priority band; only `dependsOn` is a hard gate, and it only resolves inside the task's own queue. Fold extra queues back in with `memory_queue_move` and retitle the target to match what is now in it. `tooling.one-queue-per-project`
- **Questions for Tobias go to the queue `entscheidungen-tobias`** — Anything blocked on a decision by Tobias goes there (agent-memory, project `tobias`), not into the project backlog. The entry carries three things and no copy of the task text: what is to be decided, the options with their consequences, what stands still until then — plus a pointer to the task of origin, which stays where it is. `tooling.decisions-queue`
- **CLAUDE.md is the only instruction file** — No AGENTS.md, no `.cursorrules`, no `.cursor/`, no `.opencode/`. Claude Code does not read them, so anything put there is invisible. `tooling.one-instruction-file`

### Linting

- **Every Swift project has a `.swiftlint.yml` that actually runs** — The rule set stays per project — a config only works as a gate when it fits the code it guards. What it may not be is optional, silent or empty. `lint.exists`
- **The lint runs as an Xcode build phase, via the central gate** — One line at the app target: `/Users/tobias/GitHub/mcp-server/lint/share/swiftlint-gate.sh`, wired by `scripts/install-lint-gate.mjs`. Every project needs `.swiftlint-gate.conf` naming its source roots. `ENABLE_USER_SCRIPT_SANDBOXING = NO` belongs at the target only. `lint.runs`
- **`disabled_rules`, never `only_rules`** — Every exclusion carries its hit count and its reason in the config. The gate rejects the `only_rules` shape outright. `lint.disabled-rules`
- **A lint violation is a failure, not a warning** — Run with `--strict`, never with `--quiet`. `lint.gate`
- **Coverage is counted, never guessed** — The gate counts the expected file number from the source roots at run time and demands equality — and refuses when the count comes back empty. `lint.no-guessed-thresholds`

### Local CI

- **CI runs locally in two stages, triggered by Git** — `pre-commit` lints — seconds. `pre-push` builds and tests through the broker — minutes, but only on push. No cloud CI. `ci.local`
- **Rolled out via `core.hooksPath`, never copied** — Hooks live centrally in `ci/hooks/`; `scripts/install-ci-hooks.mjs` points the project's `core.hooksPath` there. The installer stores the previous value as `localci.chainHooksPath` and the hooks forward every call to it. `ci.central`
- **The hook detects what a project can do** — The test target is read from `project.pbxproj`, not asked via `xcodebuild -list`. A project that cannot meet stage 2 is reported, not waved through. `ci.detects`
- **Stage 2 runs exclusively through the broker** — The hook calls `simulator-broker test` or `build`, never `xcodebuild`, `simctl` or `devicectl`. `ci.broker`
- **The escape hatch is logged, not locked** — `LOCAL_CI_SKIP="reason" git push` passes and writes to the broker usage trail (`sim_usage source=ci`, tool `ci:bypass`). A reason is mandatory — "1" or "true" are rejected. A silent `git commit --no-verify` still shows up: `post-commit` records the missing lint proof as `ci:unverified`. `ci.escape-hatch`

### Marketing profile

- **Anything with users has a `MARKETING.md` at the repository root** — It carries what nothing else knows: what the product is, who it is for, the selling points, the core features and which are new. The website project reads it via `marketing_index` / `marketing_profile`. A repository without users has no profile, and that is not a gap. `marketing.profile`
- **Store keywords are never copied into `MARKETING.md`** — Keywords, subtitle, name, promotional text and description are read live from `fastlane/metadata/<locale>/`, Apple Ads keywords via `asa_find_keywords`. Only terms that exist nowhere else — web and SEO without a store equivalent — belong in the profile, under their own heading. `marketing.no-keyword-copies`
- **Non-App-Store products need the profile most** — A Godot game shipping to itch.io has no store listing doing its marketing and no fastlane metadata to read. Write the positioning out in full. `marketing.non-store`
- **Store screenshots are captured in every shipped language** — The app is launched once per shipped locale per device class: four screens × every shipped locale × every device class. Exception: a screen with no text at all inside the device frame serves every storefront from one capture — but capture English and German anyway and fail the run if they differ byte for byte. `marketing.screenshot-languages`
- **Badge and capture are localized as two layers, not one** — Every shipped locale keeps its own directory under `fastlane/screenshots/<locale>`, with badge and headline composited on that locale's capture. RTL locales need the badge layout mirrored separately — the renderer does not inherit the app's own mirroring. A missing badge copy is a gap to fill, not a locale to drop. `marketing.screenshot-badges`

<!-- msc:standards:end -->
