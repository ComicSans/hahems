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

Generated from `standards.json` (mcp-server) - change it there and reinstall,
never inside the markers. Each line is the binding form. Two things come from
`project_standards`: the incident behind a rule via `rule: "<id>"` - ask before
weakening one - and the setup rules not printed here, on linting, local CI and
store assets. They bind the same; they just are not needed to do the work.

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

- **Code exploration goes through tokensave** - Its MCP tools, not file reads and not Explore agents. `tokensave init && tokensave install` in every repository. A PreToolUse hook enforces this. `tooling.tokensave`
- **iOS builds, tests, simulators and devices go through `simulator-broker`** - Never `xcodebuild`, `simctl` or `devicectl` directly. Shell scripts wrap their command in `simulator-broker/src/cli.mjs run --project <name> -- <command>`; screenshot and preview-video scripts are the usual offenders. `tooling.builds`
- **Throwaway work goes in the session scratchpad, named so housekeeping finds it** - Working copies, build output and coverage runs go in the session scratchpad, never in a repository or loose in `/tmp`. Name build output `build/`, `Build/` or `DerivedData/` - housekeeping finds it by name and never clears a directory called `dd` or `out`. `tooling.scratch`
- **Task state lives in agent-memory** - Never in `todo.md` or another markdown file. Writing a read-only export is fine; reading state back out of it is not. `tooling.state`
- **One active queue per project** - Everything a project has to do goes in that one queue. `order` only sorts within a priority band; `dependsOn` is the only hard gate and resolves inside its own queue. Fold extra queues back in with `memory_queue_move` and retitle the target. `tooling.one-queue-per-project`
- **Questions for Tobias go to the queue `entscheidungen-tobias`** - Anything blocked on a decision by Tobias goes to the queue `entscheidungen-tobias` (project `tobias`), never into the project backlog. Three lines only: what to decide, the options with consequences, what stands still - plus a pointer back. `tooling.decisions-queue`
- **CLAUDE.md is the only instruction file** - No AGENTS.md, no `.cursorrules`, no `.cursor/`, no `.opencode/`. Claude Code does not read them, so anything put there is invisible. `tooling.one-instruction-file`
- **The local CI escape hatch is logged, not locked** - `LOCAL_CI_SKIP="reason" git push` passes and is logged as `ci:bypass`; a reason is mandatory. A silent `--no-verify` commit still surfaces as `ci:unverified`. `ci.escape-hatch`

<!-- msc:standards:end -->
