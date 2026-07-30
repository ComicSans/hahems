"""Das Frontend muss als ES-Modul gültig sein.

Anlass ist ein echter Ausfall: eine doppelt angelegte Funktion auf oberster
Ebene liess das Panel in der Seitenleiste weiss bleiben. Home Assistant laedt
die Datei per `loadModule`, und in einem ES-Modul ist eine Doppeldeklaration
ein SyntaxError — anders als in einem klassischen Skript. `node --check` ohne
Modul-Endung prueft aber als Skript und meldet genau das nicht.

Deshalb wird hier mit der Endung `.mjs` geprueft. Ohne Node im Pfad wird der
Test uebersprungen statt falsche Sicherheit zu geben.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "custom_components" / "hems" / "frontend"
DATEIEN = sorted(FRONTEND.glob("*.js"))

# Deklarationen auf Spaltenposition 0 — alles Eingerueckte liegt in einem
# Block oder einer Klasse und darf gleich heissen.
DEKLARATION = re.compile(r"^(?:function|const|let|class)\s+([A-Za-z_$][\w$]*)")


def test_frontend_dateien_gefunden():
    """Ohne diesen Test koennten die uebrigen leer durchlaufen."""
    assert DATEIEN, f"keine JS-Dateien in {FRONTEND}"


@pytest.mark.parametrize("datei", DATEIEN, ids=lambda p: p.name)
def test_keine_doppelten_deklarationen(datei: Path):
    namen = Counter(
        m.group(1)
        for zeile in datei.read_text(encoding="utf-8").splitlines()
        if (m := DEKLARATION.match(zeile))
    )
    doppelt = {n: z for n, z in namen.items() if z > 1}
    assert not doppelt, f"{datei.name}: doppelt deklariert {doppelt}"


@pytest.mark.parametrize("datei", DATEIEN, ids=lambda p: p.name)
def test_gueltig_als_es_modul(datei: Path, tmp_path: Path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node nicht verfügbar")
    kopie = tmp_path / f"{datei.stem}.mjs"
    kopie.write_bytes(datei.read_bytes())
    ergebnis = subprocess.run(
        [node, "--check", str(kopie)], capture_output=True, text=True
    )
    assert ergebnis.returncode == 0, f"{datei.name}:\n{ergebnis.stderr}"
