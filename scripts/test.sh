#!/bin/bash
# Schnelle Tests des Projekts: einheitlicher Einstieg in allen Repos unter
# ~/GitHub (social-video T-101, Tobias 02.10.2026). Läuft über die
# Lauf-Warteschlange von local-ci (ohne Gerät); der pre-push-Hook ruft es auf.
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 64
[ -n "${SIM_LAUF_ID:-}" ] || exec "$HOME/GitHub/local-ci/share/sim-lauf.sh" --projekt hems \
  --zweck "${SIM_LAUF_ZWECK:-test}" --geraet keins -- "$PWD/scripts/test.sh" "$@"
set -euo pipefail
[ -x .venv/bin/pytest ] || { uv venv -q && uv pip install -q -r requirements_test.txt; }
.venv/bin/pytest -q "$@"
