"""Der Tagesverlauf muss auch während der Zeitumstellung terminieren."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

CARD = Path(__file__).resolve().parents[1] / "custom_components/hems/frontend/hems-plan-card.js"


@pytest.mark.parametrize(
    ("start", "end", "hours"),
    [
        ("2026-10-25T01:30:00+02:00", "2026-10-25T03:30:00+01:00", [23, 0, 1, 2]),
        ("2026-10-25T02:30:00+01:00", "2026-10-25T03:30:00+01:00", [1, 2]),
        ("2026-03-29T01:30:00+01:00", "2026-03-29T04:30:00+02:00", [0, 1, 2]),
        ("2026-10-24T01:30:00+02:00", "2026-10-24T03:30:00+02:00", [23, 0, 1]),
    ],
)
def test_stundenmittel_zeitumstellung(start, end, hours):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node nicht verfügbar")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const [file, start, end, hours] = process.argv.slice(1);
const context = vm.createContext({
  HTMLElement: class {}, window: {customElements: {get: () => true}},
});
vm.runInContext(fs.readFileSync(file, 'utf8'), context);
context.start = Date.parse(start);
context.end = Date.parse(end);
const result = vm.runInContext(
  'hourlyMeans([[start, 400]], end)', context, {timeout: 500},
);
assert.deepEqual(Array.from(result, b => b.von.getUTCHours()), JSON.parse(hours));
assert.ok(result.every(b => b.watt === 400 && b.bis > b.von));
assert.equal(result[0].von.getTime() + 1800000, context.start);
assert.equal(result.at(-1).bis.getTime(), context.end);

// Unterschiedliche Recorder-Intervalle bleiben zeitgewichtet.
const weighted = vm.runInContext(
  'hourlyMeans([[start, 100], [start + 900000, 500]], start + 1800000)',
  context, {timeout: 500},
);
assert.equal(weighted.length, 1);
assert.equal(weighted[0].watt, 300);
"""
    result = subprocess.run(
        [node, "-e", script, str(CARD), start, end, json.dumps(hours)],
        env={**os.environ, "TZ": "Europe/Berlin"},
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert result.returncode == 0, result.stderr
