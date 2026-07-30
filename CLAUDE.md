# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**HEMS** — a Home Energy Management System as a Home Assistant custom
integration. It forecasts and plans across PV, battery storage, hot water, heat
pump and wallbox, and is **device-agnostic**: everything is configured as a
*role* through the UI, so no entity ID ever appears in the code.

Distributed through HACS, folder-based from `custom_components/hems/`.
Concept and phase plan: [CONCEPT.md](CONCEPT.md). User-facing docs: [README.md](README.md)
as the entry point, reference material under `docs/` (`konfiguration.md`,
`regelverhalten.md`, `diagnose.md`).

## This integration can switch real hardware

Two modes. `beobachten` (the default) only displays recommendations. `auto`
additionally actuates configured control entities — battery, heat pump, hot
water, wallbox. When touching `actuator.py`, the coordinator's write paths or
anything downstream of a mode check, keep in mind that a mistake there moves
physical loads in a real house. Changes to actuation belong behind tests before
they reach a running instance.

## Build & Test

```sh
pytest                          # whole suite, addopts = -q
pytest tests/test_compute_plan.py
python tests/simulate.py        # scenario simulation against the planner
```

No Home Assistant instance is needed: the domain logic imports nothing from HA.
`tests/factories.py` builds plan inputs, `tests/data/` holds fixtures.

## Architecture

The dividing line that matters is **HA-free domain logic vs. the HA layer**:

```
custom_components/hems/
  planner.py        compute_plan — pure function, orchestrates the strategies
  strategies/       the actual domain rules, all HA-free
    battery · coordination · demand · forecast · heating · loads
    switchable · water · types
  models.py         dataclasses
  const.py          defaults and goal/priority constants

  coordinator.py    the HA layer — polling, state, entity wiring (~1250 lines)
  sensor.py · binary_sensor.py · switch.py · select.py   entities
  config_flow.py · config_ws.py · config_check.py        setup and validation
  actuator.py       writes to control entities (auto mode only)
  frontend/         custom panel
```

Rules that hold this together:

- **`planner.py` and everything under `strategies/` must stay free of Home
  Assistant imports.** That is what makes them testable at all — the moment one
  of them imports `homeassistant`, it drops out of the test suite.
- **`strategies/types.py` imports only from `..const` and the standard
  library**, never from another strategy module. It is the shared home of the
  runtime types precisely so no import cycle can form.
- Input preparation and display formatting live in `planner.py` rather than the
  coordinator, so they stay testable — the coordinator imports HA.

`docs/architektur-review.md` records why the split looks the way it does,
including what was deliberately left alone.

## Key patterns

**Every yes/no decision has two thresholds, never one.** `_latch` in
`strategies/types.py` is a Schmitt trigger, and the constants come in
on/off pairs (`DEFAULT_BOOST_SOC_ON` / `..._OFF`). A single threshold makes the
system chatter around it — switching a heat pump on and off every poll cycle.
When adding a decision, add both thresholds.

**Roles, not entities.** Devices are configured through the config flow and
referenced by role. An entity ID hardcoded in the domain logic breaks the
central promise of the integration.

## Gotchas

**Renaming a sensor breaks users' dashboards, and attributes break silently.**
An `entity_id` at least leaves an unavailable entity behind in the registry as a
visible sign. **Attributes are not anchored in the registry** — they change the
moment the update lands, and a Lovelace card using `state_attr(...)` simply goes
blank. Both kinds of rename have happened (0.6.0, 1.0.5) and both are recorded
in `CHANGELOG.md`, which exists only for changes that force users to touch their
own dashboards. Add any further rename there, with what to do about it.

**The HA layer is the largest untested surface.** `coordinator.py` is the
biggest file and carries the widest blast radius; its behaviour is verified
against live hardware, not in CI. Prefer moving logic down into `strategies/`
over growing it.

## Releasing

The release tag and the version in `manifest.json` must match — the
`release.yml` workflow enforces it. SemVer in the manifest without a leading
`v`, tag with one. Full procedure: [RELEASING.md](RELEASING.md).

`Validate` (hassfest + HACS action) runs on every push and PR.

## Agent workflow

- Task queues in agent-memory are the only workflow state; never `todo.md`.
- Code exploration goes through the tokensave MCP tools rather than file reads.

<!-- msc:standards:start -->

## Workspace standards

Generated from `standards.json` in the mcp-server repository by
`scripts/install-claude-project-mcp.mjs`. Do not edit inside the markers — the
next install overwrites it. Change the rule centrally and reinstall.

### Working with the user

- **Ask when something is unclear, and be critical** — No yes-man. Feedback has to be thought through and substantiated. Point out contradictions, mistakes and missing information rather than working around them. Do not assume — ask when something is unclear or ambiguous. Where the user is unreachable (offline session, queue work), decide with the most plausible assumption, document it, and present it at the next opportunity.
- **Short, clear answers** — Short sentences, keywords over prose, no line numbers or file names in the reply. Think solution-first and pragmatically. Include the unknown unknowns in your reasoning.
- **Assume several sessions run in the same workspace** — Never assume a clean working tree or exclusive access to a device, a build or a file. Be frugal with memory and compute.
- **Neutral, gender-inclusive language and accessibility throughout** — Use gender-inclusive wording in every text, and treat accessibility and a11y as a requirement in every change, not as a later pass.
- **Match the model to the job** — Use Opus or Sonnet for agents where that model can do the work reliably. An advisor always uses a stronger model where one exists — Fable or Opus. Use fitting agents with fitting models where that helps, and check that every agent runs on an appropriate one.

### Git

- **Work happens on `main`** — No feature branches. Commit to `main` directly, in small steps that keep it green. Long-lived side branches hid work from every other session and from tokensave, which answers queries from the branch it tracks.
- **The main branch is called `main`** — Not `master`, not `develop`. Renaming is a manual step — it touches the remote and any open work — so the installer only reports the deviation and prints the commands.
- **Trunk-based means claiming before editing** — Several sessions share this workspace and now share one branch. Claim the files you are about to change via `memory_claim_files` and release them when done. Rebase before pushing; never force-push `main`. Do not commit files you did not change.
- **A branch rename needs a tokensave follow-up** — tokensave records a parent branch per repository. After renaming, run `tokensave branch add main` and re-sync, or code queries keep answering from a branch that no longer exists — silently, with stale results.

### Tooling

- **MCP servers come from the installer** — `.mcp.json` is generated by `scripts/install-claude-project-mcp.mjs` from `mcp.config.json`. Every project gets `msc-agent-memory`, `msc-sourcemap` and `msc-simulator-broker`. Do not hand-edit `.mcp.json`; edit `mcp.config.json` and reinstall.
- **Code exploration goes through tokensave** — `tokensave init && tokensave install` in every repository. Exploration uses its MCP tools, not file reads and not Explore agents.
- **iOS builds, tests, simulators and devices go through `simulator-broker`** — Never `xcodebuild`, `simctl` or `devicectl` directly — DerivedData is shared machine-wide, so a direct run corrupts what another session is building. Shell scripts wrap their command in `node simulator-broker/src/cli.mjs run --project <name> -- <command>`. This applies to screenshot and preview-video scripts too, which are the usual offenders.
- **Throwaway work goes in the session scratchpad, and its build output is named so** — Working copies, measurement checkouts, build output and coverage runs belong under the session's scratchpad, never in the repository and never loose in `/tmp`. The broker cleans that scratchpad up on its own — `sim_housekeeping` removes build output after twelve hours and whole session folders after fourteen days — but it recognises build output by name. Call the directory `build/`, `Build/` or `DerivedData/`, or give it one of the usual suffixes (`.xcframework`, `.app`, `.a`); a directory called `dd` or `out` is not recognised and survives as long as the session folder does.

What prompted the rule: on 2026-07-25 seven complete Godot working copies of 1.6 GB each sat in scratchpads, created for before-and-after measurements and never removed. The measurements had long since been written into the tasks; only the copies were left.

Two consequences worth knowing. A build directory inside the repository — `match-app/build`, 282 MB — is never touched, because the mechanism does not delete inside a working tree; keep it out of the repository in the first place. And a backup is never removed: anything matching `protectedGlobs` in `simulator-broker/config/housekeeping.json` (`*.bundle`, `*.patch`, `*.diff`, `*.sql`, `*.sqlite`) keeps its whole session folder alive. That is deliberate, and it means a large backup has to be cleared by hand once it is no longer needed.
- **Task state lives in agent-memory** — Never in `todo.md` or another markdown file. Writing a read-only export is fine; reading state back out of it is not.
- **One active queue per project — order comes from `order` and `dependsOn`** — Everything a project has to do goes into that one queue. What comes first is expressed in the data: `order` for the sequence, `dependsOn` for what genuinely cannot start earlier. Note that `memory_queue_next` weighs priority before `order` — `order` sorts within a priority band, and only `dependsOn` is a hard gate.

That weighting was reviewed on 2026-07-26 and deliberately kept, so nobody has to re-open it. It bites hardest right after a merge: a `p0` from the last group is handed out before a `p1` from the first, and the group sequence the merge just established is only a wish. That is the right trade — a `p0` is a `p0` no matter which queue it came from — but it means `order` alone never holds a group together. What must not start earlier belongs in `dependsOn`; what is merely nice to do first is a hint, not a guarantee.

Across queue boundaries neither works. `dependsOn` is only ever compared against the items of the task's own queue, so a reference to a task in another queue is never satisfied — the task waits forever, with no error and no diagnostic. The only cross-queue ordering that exists is `memory_queue_next` walking queues by age and stopping at the first with unfinished work; it cannot express anything beyond that. What is left over has to be held in someone's head, and on the night of 2026-07-25 that is exactly what happened: claudio-app had five active queues and a coordinator decided by hand which came first.

A second queue is not forbidden as a thought — it is folded back in. `memory_queue_move` moves tasks with their id, status, timestamps and review history, resolves numeric `dependsOn` to task ids beforehand, and closes a source queue left without open work. After a merge the title and description of the target must describe what is now in it; a merged queue still called "Bugfixes 2026-07-24" is the kind of record that misled three agents in one night.

Tobias is carried as a project of his own (`tobias`), with the queue `entscheidungen-tobias`. Its entries are decisions, not implementation tasks, and they touch every project — which is exactly why they must not lie between implementation tasks. There is no directory behind that project and none is expected.
- **Questions for Tobias go to one queue, not into the project backlog** — Anything that cannot move without a decision by Tobias belongs in the queue `entscheidungen-tobias` (agent-memory, project `tobias`). Not in the project queue, where it disappears between thirty implementation tasks — that is exactly what happened before: the decision points sat in fourteen queues and had to be collected by hand.

The project task stays where it is and describes the work. The entry in the decisions queue carries only three things: what is to be decided, which options exist with their consequences, and what stands still until then — plus a pointer to the task of origin.

Deliberately no copy of the full task text. Two versions of the same thing drift apart within days, and the drift is invisible until someone acts on the stale one.
- **CLAUDE.md is the only instruction file** — No AGENTS.md, no `.cursorrules`, no `.cursor/`, no `.opencode/`. Claude Code is the only client in use and does not read them, so anything put there is invisible.

### Linting

- **Every Swift project has a `.swiftlint.yml`, and the run happens** — A config nobody executes protects nothing. The earlier wording asked for the file and a green run, but not for the run to take place or to check anything — and that gap took hold in all four Swift projects independently, each with a different hole. The rule set stays per project, because a config only works as a gate when it fits the code it guards. What it may not be is optional, silent, or empty.
- **The lint runs as an Xcode build phase, via the central gate** — One line at the app target: `/Users/tobias/GitHub/mcp-server/lint/share/swiftlint-gate.sh`, wired by `scripts/install-lint-gate.mjs`. The gate extends PATH (Xcode.app starts build phases without `/opt/homebrew/bin` — this failed a build in match-app), fails hard on missing or crashing SwiftLint, compares the checked file count against the counted one, and compares the active rule set against `.swiftlint-rules.baseline`. Every project needs `.swiftlint-gate.conf` naming its source roots; without it the gate refuses to run rather than falling back on a guess. `ENABLE_USER_SCRIPT_SANDBOXING = NO` belongs at the target only — set on the project it inherits silently into every future target.
- **`disabled_rules`, never `only_rules`** — `only_rules` is subtractive: whatever is not listed is off, silently, including every rule a new SwiftLint version brings. Measured across the four projects it silenced between 44 and 89 rules that were already green, among them real defect catchers — duplicate_conditions, duplicated_key_in_dictionary_literal, block_based_kvo, notification_center_detachment, unused_setter_value, self_in_property_initialization, xctfail_message. Every exclusion carries its hit count and its reason in the config, so it reads as a decision and not as a threshold somebody set once. The gate rejects the `only_rules` shape outright.
- **A lint violation is a failure, not a warning** — Warnings accumulate until nobody reads them. Whatever is configured runs as an error, via `--strict`. Never `--quiet`: it suppresses the summary line the file count is read from, which is exactly how the check was defeated in tsugi-app.
- **Coverage is counted, never guessed** — Every project that guessed a minimum file count had a blind spot behind it: match-app required 20 of 49 files and passed on 30, nonogram-app required 40 of 45 and passed on 41. The gate counts the expected number from the source roots at run time and demands equality — and refuses when the count itself comes back empty, because otherwise both sides are zero and the comparison passes by construction.

### Local CI

- **Die CI läuft lokal, in zwei Stufen, und wird von Git ausgelöst** — Vorher löste nichts eine Prüfung aus: Das Lint-Gatter greift nur, wenn jemand baut, die Tests nur, wenn jemand sie startet. Claudios Lint stand daraufhin einen Tag lang rot, ohne dass es auffiel, und eine Testsuite wochenlang. `pre-commit` lintet — Sekunden, spürbar blockiert das nichts. `pre-push` baut und testet über den Broker — Minuten, aber nur beim Push. Keine Cloud-CI: gebaut und signiert wird ohnehin auf diesem Rechner, ein Cloud-Läufer müsste Xcode, Simulatorlaufzeiten und Signierprofile nachbauen, um am Ende dasselbe zu prüfen.
- **Ausgerollt über `core.hooksPath`, nie kopiert** — Die Hooks liegen zentral unter `ci/hooks/`; `scripts/install-ci-hooks.mjs` biegt `core.hooksPath` des Projekts darauf. Kopierte Hooks lägen in `.git/hooks`, wären unversioniert, in jedem Projekt anders und nach einem frischen Klon weg. Dieser Pfad ist global bereits von git-lfs und tokensave belegt — der Installer merkt sich den vorherigen Wert als `localci.chainHooksPath` und die Hooks reichen jeden Aufruf dorthin weiter. Ohne das fielen LFS-Übertragung und tokensave-Synchronisierung still aus.
- **Der Hook erkennt, was ein Projekt kann** — Vorausgesetzt wird nichts: `alto-app` ist Godot ohne Xcode, `match-app` hat kein Testziel und kann in Stufe 2 nur bauen und linten, `peggle-app` und `tsugi-app` sind iPhone-only. Das Testziel wird aus der `project.pbxproj` gelesen, nicht per `xcodebuild -list` erfragt — das wäre genau der direkte Aufruf, den der Broker verhindert. Ein Projekt, das Stufe 2 heute nicht erfüllen kann, wird gemeldet und nicht stillschweigend durchgewunken.
- **Stufe 2 läuft ausschließlich über den Broker** — Der Hook ruft `simulator-broker test` beziehungsweise `build` auf, nie `xcodebuild`, `simctl` oder `devicectl`. Ein Hook, der selbst baute, wäre genau die Sorte Skript, für die `broker-guard.sh` gebaut wurde: ohne Sperre am Broker vorbei und mit korrumpiertem geteiltem Cache.
- **Der Notausgang ist protokolliert, nicht verschlossen** — Ein Hook, an dem man nicht vorbeikommt, wird irgendwann mit `--no-verify` umgangen — und dann prüft gar nichts mehr, unsichtbar. `LOCAL_CI_SKIP="Grund" git push` geht vorbei und schreibt einen Eintrag in die Nutzungsspur des Brokers (`sim_usage source=ci`, Werkzeug `ci:bypass`). Ein Grund ist Pflicht; "1" oder "true" werden abgewiesen, damit der Notausgang eine Entscheidung bleibt und kein Schalter in der Shell-Konfiguration. Ein stilles `git commit --no-verify` fällt trotzdem auf: der `post-commit`-Hook läuft auch dann und vermerkt den fehlenden Lint-Beleg als `ci:unverified`.

### Marketing profile

- **Anything with users has a `MARKETING.md` at the repository root** — It carries what nothing else knows: what the product is, who it is for, the unique selling points, the core features and which features are new and why they matter to that audience. The website project reads these centrally via `marketing_index` / `marketing_profile` and derives campaigns from them. A repository without users (tooling, servers) has no profile and that is not a gap.
- **Store keywords are never copied into `MARKETING.md`** — Keywords, subtitle, name, promotional text and description live in `fastlane/metadata/<locale>/` and are read live per locale at query time. Apple Ads targeting keywords are read live via `asa_find_keywords`. A second hand-maintained copy drifts within days and the drift is invisible until a campaign underperforms. Only keywords that exist nowhere else — web and SEO terms without a store equivalent — belong in the profile, under their own heading.
- **Non-App-Store products need the profile most** — A Godot game shipping to itch.io has no store listing doing its marketing. There is no fastlane metadata to read, so the profile is the only source — write the positioning out in full.
- **Store screenshots are captured in two languages only — English, and German for de-DE** — The app itself is launched in exactly two languages for a capture run: English for every locale, German only for de-DE. Four motifs is binding, not the current shape: exactly four screens × 2 languages per shipped device class — 16 raw captures for an iPhone-and-iPad app, not one capture run per shipped locale. Never launch the app in a third language for a screenshot, and never keep raw captures in one: a Japanese or Arabic capture set is work nobody asked for and it drifts out of date silently.
- **Every shipped locale still gets its own rendered screenshots — the badge text is what is localized** — The capture language is not the screenshot language. Each shipped locale keeps its own directory under `fastlane/screenshots/<locale>` and is rendered from the English capture — de-DE from the German one — with the badge and headline text for that locale composited on top (`screen.text[locale]` in the store-assets config, with `localeAliases` for en-GB/en-AU-style aliases). So the directory count follows the shipped locale list, while the capture count stays at two languages. Right-to-left locales (`rightToLeft` in the config) need their badge layout mirrored, and a locale whose badge copy is missing is a gap to fill, not a locale to drop.

<!-- msc:standards:end -->
