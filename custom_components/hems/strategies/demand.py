"""Verbrauchs-/Bedarfsmodell: erwartete Last je Zeitpunkt und Fensterenergie.

Gelerntes Lastprofil mit Grundlast-Fallback — die Grundlage für Nachtdefizit,
Restüberschuss und die SoC-Prognose. Wärmeerzeuger stecken implizit im Profil;
HEMS modelliert sie nicht getrennt.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .types import PlanInput


def _lokal(inp: PlanInput, t: datetime) -> datetime:
    """t als lokale Wanduhrzeit — das Profil ist nach Ortszeit gelernt.

    Bis 22.09.2026 galten Stunde und Tagtyp in UTC. Der Haushalt lebt aber
    nach der Wanduhr: In UTC begann das Wochenende in Deutschland ein bis zwei
    Stunden zu früh, und nach jeder Zeitumstellung rutschte das ganze Profil
    um eine Stunde gegen die Gewohnheiten, die es beschreibt. Der Offset kommt
    wie bei den Ladefenstern vom Coordinator (`utc_offset_h`).
    """
    return t + timedelta(hours=inp.utc_offset_h)


def _daytype(t: datetime) -> int:
    """0 = Werktag (Mo–Fr), 1 = Wochenende (Sa/So) — von einer lokalen Zeit."""
    return 1 if t.weekday() >= 5 else 0


def _expected_load_w(inp: PlanInput, t: datetime) -> float:
    """Erwartete Last zur Stunde von t: gelerntes Profil (Tagtyp + Stunde),
    sonst gleiche Stunde im anderen Tagtyp, sonst Nachtlast."""
    prof = inp.load_profile_w
    if prof:
        lokal = _lokal(inp, t)
        key = (_daytype(lokal), lokal.hour)
        if key in prof:
            return prof[key]
        same_hour = [w for (_d, h), w in prof.items() if h == lokal.hour]
        if same_hour:
            return sum(same_hour) / len(same_hour)
    return inp.night_load_w


def _profile_covers(inp: PlanInput, start: datetime, end: datetime) -> bool:
    """True, wenn das Profil jede Stunde des Fensters (in einem Tagtyp) kennt."""
    prof = inp.load_profile_w
    if not prof:
        return False
    stunden = (_lokal(inp, t).hour for t, _nxt in _hour_slots(start, end))
    return all((0, h) in prof or (1, h) in prof for h in stunden)


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
