"""Zeitverlauf-Test der schaltbaren Last: Anti-Takt und Bedarf über den Tag.

Führt eine schaltbare Last mit rückgekoppeltem An/Aus-Zustand und mitlaufenden
min_on/min_off-Timern durch den anonymisierten Tagesverlauf — prüft end-to-end,
dass die Anti-Takt-Logik das Pendeln dämpft (kein Flattern) und die Last dem
Überschuss folgt (tagsüber an, nachts aus).
"""
from __future__ import annotations

from simulate import simulate, switch_count


def _on_segmente(steps):
    """Längen zusammenhängender An-Phasen (in Schritten)."""
    seg, run, prev = [], 0, False
    for s in steps:
        if s.sw_an and not prev:
            run = 1
        elif s.sw_an:
            run += 1
        elif prev:
            seg.append(run)
        prev = s.sw_an
    if prev:
        seg.append(run)
    return seg


def test_kein_flattern_min_on_wird_gehalten():
    st = simulate("eigenverbrauch", switchable_w=1000, sw_min_on=20, sw_min_off=10)
    seg = _on_segmente(st)
    assert seg, "Last lief nie an"
    # min_on = 20 min bei 15-Min-Raster => jede An-Phase mindestens 2 Schritte.
    assert min(seg) >= 2, f"An-Phase zu kurz (Flattern): {seg}"
    # Keine Dauer-Umschaltung jeden Schritt.
    assert switch_count(st) < 24


def test_folgt_dem_ueberschuss():
    st = simulate("eigenverbrauch", switchable_w=1000)
    # Mittags (Überschuss) an ...
    assert any(s.sw_an for s in st if 11 * 60 <= s.minute < 13 * 60)
    # ... nachts (kein Überschuss) aus.
    assert not any(s.sw_an for s in st if s.minute < 4 * 60 or s.minute >= 22 * 60)


def test_hoehere_leistung_schaltet_seltener():
    klein = simulate("eigenverbrauch", switchable_w=1000)
    gross = simulate("eigenverbrauch", switchable_w=3000)
    an_klein = sum(1 for s in klein if s.sw_an)
    an_gross = sum(1 for s in gross if s.sw_an)
    # Eine große Last braucht mehr Überschuss -> läuft weniger Schritte.
    assert an_gross < an_klein
    assert switch_count(gross) <= switch_count(klein)
