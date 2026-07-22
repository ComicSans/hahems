"""Verbrauchs-/Bedarfsmodell: erwartete Last je Zeitpunkt und Fensterenergie.

Gelerntes Lastprofil, Grundlast-Fallback und Wärmepumpenmodell — die Grundlage
für Nachtdefizit, Restüberschuss und die SoC-Prognose.
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


def _forecast_temp_at(inp: PlanInput, t: datetime) -> float | None:
    """Außentemperatur der Stunde von t: Vorhersage, sonst aktueller Wert."""
    if inp.temp_forecast_c:
        temp = inp.temp_forecast_c.get(
            t.replace(minute=0, second=0, microsecond=0)
        )
        if temp is not None:
            return temp
    return inp.heating.outdoor_temp_c if inp.heating is not None else None


def _wp_expected_w(inp: PlanInput, t: datetime) -> float:
    """Erwartete WP-Leistung zur Stunde von t aus dem Verbrauchsmodell.

    Ohne Modell 0 (die WP steckt dann implizit im Lastprofil). Während der
    Sommersperre und ohne Temperaturwert zählt nur die Basisleistung
    (Warmwasser/Standby), sonst kommt der Heizgradstunden-Term dazu.
    """
    m = inp.wp_model
    if m is None:
        return 0.0
    watt = m.base_w
    heat_locked = inp.heating is not None and inp.heating.heat_locked
    temp = _forecast_temp_at(inp, t)
    if temp is not None and not heat_locked:
        watt += m.k_w_per_k * max(0.0, m.limit_c - temp)
    return min(watt, m.max_w) if m.max_w else watt


def _total_load_w(inp: PlanInput, t: datetime) -> float:
    """Gesamtlast der Stunde: Profil (WP-bereinigt) plus WP-Modell."""
    return _expected_load_w(inp, t) + _wp_expected_w(inp, t)


def _wp_window_kwh(inp: PlanInput, start: datetime, end: datetime) -> float:
    """Erwartete WP-Energie im Fenster, stundenweise aus dem Modell."""
    return sum(
        _wp_expected_w(inp, t) * (nxt - t).total_seconds() / 3600 / 1000
        for t, nxt in _hour_slots(start, end)
    )


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
    """Erwartete Verbrauchsenergie im Fenster: Profil plus WP-Modell."""
    return sum(
        _total_load_w(inp, t) * (nxt - t).total_seconds() / 3600 / 1000
        for t, nxt in _hour_slots(start, end)
    )
