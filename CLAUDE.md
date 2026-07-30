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
