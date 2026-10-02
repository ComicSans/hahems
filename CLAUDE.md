# CLAUDE.md

Regeln für Claude Code in diesem Repo. Was HEMS ist und wie man es einrichtet,
steht in `README.md`; Release über HACS in `RELEASING.md`.

## Was das ist

HEMS: Home-Assistant-Custom-Integration (`custom_components/hems`), verteilt über
HACS. Das Repo ist öffentlich (Ausnahme REMOTE in `~/GitHub/local-ci/ausnahmen.tsv`):
nichts Internes, keine Zugangsdaten, keine echten Hausdaten in Tests oder Doku.

## Regeln

- Tests: `scripts/test.sh` (pytest in `.venv`, über die Lauf-Warteschlange); der
  pre-push-Hook von local-ci ruft es auf.
- Release: Tag und `manifest.json`-Version stimmen überein (`RELEASING.md`);
  veröffentlicht wird nur nach Tobias' Ja.
- Global gilt `~/.claude/CLAUDE.md`.
