"""Verbrauchs-/Bedarfsmodell: erwartete Last je Zeitpunkt und Fensterenergie.

Gelerntes Lastprofil mit Grundlast-Fallback — die Grundlage für Nachtdefizit,
Restüberschuss und die SoC-Prognose. Wärmeerzeuger stecken implizit im Profil;
HEMS modelliert sie nicht getrennt.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .types import PlanInput


def _daytype(t: datetime) -> int:
    """0 = Werktag (Mo–Fr), 1 = Wochenende (Sa/So). UTC, wie das Profil."""
    return 1 if t.weekday() >= 5 else 0


def _expected_load_w(inp: PlanInput, t: datetime) -> float:
    """Erwartete Last zur Stunde von t: gelerntes Profil (Tagtyp + Stunde),
    sonst gleiche Stunde im anderen Tagtyp, sonst Nachtlast."""
    prof = inp.load_profile_w
    if prof:
        key = (_daytype(t), t.hour)
        if key in prof:
            return prof[key]
        same_hour = [w for (_d, h), w in prof.items() if h == t.hour]
        if same_hour:
            return sum(same_hour) / len(same_hour)
    return inp.night_load_w


def _profile_covers(inp: PlanInput, start: datetime, end: datetime) -> bool:
    """True, wenn das Profil jede Stunde des Fensters (in einem Tagtyp) kennt."""
    prof = inp.load_profile_w
    if not prof:
        return False
    return all(
        (0, t.hour) in prof or (1, t.hour) in prof
        for t, _nxt in _hour_slots(start, end)
    )


def _hour_slots(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    slots = []
    t = start
    while t < end:
        nxt = min(t + timedelta(hours=1), end)
        slots.append((t, nxt))
        t = nxt
    return slots


def _window_load_kwh(inp: PlanInput, start: datetime, end: datetime) -> float:
    """Erwartete Verbrauchsenergie im Fenster aus dem Lastprofil."""
    return sum(
        _expected_load_w(inp, t) * (nxt - t).total_seconds() / 3600 / 1000
        for t, nxt in _hour_slots(start, end)
    )
