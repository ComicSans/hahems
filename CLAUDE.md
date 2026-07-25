# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**HEMS** — a Home Energy Management System as a Home Assistant custom
integration. It forecasts and plans across PV, battery storage, hot water, heat
pump and wallbox, and is **device-agnostic**: everything is configured as a
*role* through the UI, so no entity ID ever appears in the code.

Distributed through HACS, folder-based from `custom_components/hems/`.
Concept and phase plan: [CONCEPT.md](CONCEPT.md). User-facing docs: [README.md](README.md).

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
blank. Both kinds of rename have happened (0.6.0, 1.0.5) and both needed a
breaking-change note at the top of the README. Do the same for any further
rename.

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
