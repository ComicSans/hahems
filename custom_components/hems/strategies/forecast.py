"""Prognose-Domäne: PV-Kurve, Entladeplan und SoC-Vorausschau.

Grobe Vorwärtsrechnungen für die Plankarte und die Entlade-Obergrenzen über die
Nacht. Nutzt das Bedarfsmodell (demand) und den Ladedeckel (battery).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from .battery import _lade_deckel_soc
from .demand import _hour_slots, _total_load_w
from .types import DischargeSlot, PlanInput, PlanResult, PvSlot, SocPoint


def _pv_curve(inp: PlanInput) -> list[PvSlot]:
    """PV-Prognose als Stundenkurve bis zum Ende des Horizonts.

    Die Glockenform wird über den kompletten Kalendertag aufgespannt, damit die
    Leistung zur Tageszeit passt; ausgegeben werden aber nur Slots ab jetzt.
    Für vergangene Stunden liegen keine Messdaten vor, und eine rückwirkend
    geschätzte Kurve wäre erfunden — die Karte lässt den Bereich stattdessen
    leer. Kennt der Coordinator die Sonnenzeiten der Kalendertage nicht, greift
    die Näherung über die nächsten Sonnenereignisse ±24 h.
    """
    curve: list[PvSlot] = []
    if inp.today_sunrise and inp.today_sunset:
        curve += _day_curve(inp.today_sunrise, inp.today_sunset, inp.pv_today_kwh)
        if inp.tomorrow_sunrise and inp.tomorrow_sunset:
            curve += _day_curve(
                inp.tomorrow_sunrise, inp.tomorrow_sunset, inp.pv_tomorrow_kwh
            )
    elif inp.next_sunrise is not None and inp.next_sunrise < inp.sunset:
        curve += _day_curve(inp.next_sunrise, inp.sunset, inp.pv_tomorrow_kwh)
    else:
        curve += _day_curve(inp.sunrise - timedelta(hours=24), inp.sunset, inp.pv_today_kwh)
        curve += _day_curve(
            inp.sunrise, inp.sunset + timedelta(hours=24), inp.pv_tomorrow_kwh
        )

    end = inp.horizon_end
    return [
        s
        for s in curve
        if s.end > inp.now and (end is None or s.start < end)
    ]


def _pv_power_at(curve: list[PvSlot], t: datetime) -> float:
    """PV-Leistung der Stunde, in die t fällt (0 außerhalb der Kurve)."""
    for slot in curve:
        if slot.start <= t < slot.end:
            return slot.watt
    return 0.0


def _day_curve(
    day_start: datetime, day_end: datetime, energy_kwh: float
) -> list[PvSlot]:
    """Energie eines Tages sinusförmig auf Stunden-Slots verteilen."""
    total_s = (day_end - day_start).total_seconds()
    if total_s <= 0 or energy_kwh <= 0:
        return []
    raw: list[tuple[datetime, datetime, float]] = []
    for t, nxt in _hour_slots(day_start, day_end):
        mid = t + (nxt - t) / 2
        shape = math.sin(math.pi * (mid - day_start).total_seconds() / total_s)
        raw.append((t, nxt, max(0.0, shape)))
    weighted = sum(s * (nxt - t).total_seconds() / 3600 for t, nxt, s in raw)
    if weighted <= 0:
        return []
    scale = energy_kwh * 1000 / weighted
    return [PvSlot(start=t, end=nxt, watt=round(s * scale)) for t, nxt, s in raw]


def _discharge_plan(
    inp: PlanInput,
    res: PlanResult,
    available_kwh: float,
    reserve_kwh: float,
    ziel_kwh: float,
    cap_kwh: float,
) -> None:
    """Stunden-Slots für die nächtliche Entladung berechnen.

    Strategie "gleichmäßig strecken": Reicht das Budget nicht für die volle
    Nachtlast, werden alle Slots proportional reduziert, damit der Akku bis
    Sonnenaufgang durchhält; der Rest kommt parallel aus dem Netz.
    """
    max_discharge_w = sum(s.max_discharge_w for s in inp.storages)
    if max_discharge_w <= 0:
        return

    if inp.next_sunrise is not None and inp.next_sunrise < inp.sunset:
        # Es ist bereits Nacht: Fenster läuft ab jetzt bis zum Sonnenaufgang.
        start, end = inp.now, inp.next_sunrise
        start_kwh = available_kwh
    else:
        # Tagsüber: Plan für die kommende Nacht. Erwarteter Stand bei
        # Sonnenuntergang = heutiger Stand plus dem Teil des Restüberschusses,
        # der noch bis zum Ziel-SoC in den Akku passt.
        start, end = inp.sunset, inp.sunrise
        start_kwh = min(
            cap_kwh,
            available_kwh
            + max(0.0, min(ziel_kwh - available_kwh, res.ueberschuss_rest_kwh)),
        )

    if (end - start).total_seconds() <= 0:
        return

    budget_kwh = max(0.0, start_kwh - reserve_kwh)
    res.entlade_budget_kwh = round(budget_kwh, 2)

    # Wunschleistung je Slot aus dem Lastprofil, gedeckelt auf die
    # Entladeleistung; bei knappem Budget alle Slots proportional strecken.
    raw = [
        (t, nxt, min(_total_load_w(inp, t), max_discharge_w))
        for t, nxt in _hour_slots(start, end)
    ]
    need_kwh = sum(w * (nxt - t).total_seconds() / 3600 / 1000 for t, nxt, w in raw)
    factor = 1.0 if need_kwh <= budget_kwh or need_kwh <= 0 else budget_kwh / need_kwh

    remaining = budget_kwh
    slots: list[DischargeSlot] = []
    for t, nxt, w in raw:
        watt = round(w * factor)
        slot_h = (nxt - t).total_seconds() / 3600
        remaining = max(0.0, remaining - watt * slot_h / 1000)
        slots.append(
            DischargeSlot(
                start=t,
                end=nxt,
                watt=watt,
                soc_erwartet=round((reserve_kwh + remaining) / cap_kwh * 100, 1),
            )
        )
        if t <= inp.now < nxt:
            res.entlade_w_jetzt = watt
    res.entladeplan = slots


def _soc_forecast(
    inp: PlanInput,
    res: PlanResult,
    available_kwh: float,
    reserve_kwh: float,
    cap_kwh: float,
    voll_noetig: bool = False,
) -> list[SocPoint]:
    """Stündlicher Vorwärtslauf des Speicherstands ab jetzt.

    Überschuss lädt (begrenzt durch Ladeleistung und Kapazität), Defizit
    entlädt bis zur Reserve; darunter deckt das Netz. Grobe Prognose, die in
    der Karte gestrichelt dargestellt wird.
    """
    end = inp.horizon_end
    if end is None or end <= inp.now or cap_kwh <= 0:
        return []

    max_charge_w = sum(s.max_charge_w for s in inp.storages)
    max_discharge_w = sum(s.max_discharge_w for s in inp.storages)

    energy = available_kwh
    points = [SocPoint(zeit=inp.now, soc=round(energy / cap_kwh * 100, 1))]
    for t, nxt in _hour_slots(inp.now, end):
        hours = (nxt - t).total_seconds() / 3600
        balance_w = _pv_power_at(res.pv_kurve, t) - _total_load_w(inp, t)
        if balance_w >= 0:
            charge_w = min(balance_w, max_charge_w)
            # Ladedeckel mitführen (Akku-Schonung): nie über den Deckel laden,
            # aber einen bereits höheren Stand nicht künstlich absenken.
            deckel_kwh = max(
                energy, _lade_deckel_soc(inp, voll_noetig, t) / 100 * cap_kwh
            )
            energy = min(deckel_kwh, energy + charge_w * hours / 1000)
        else:
            discharge_w = min(-balance_w, max_discharge_w)
            energy = max(reserve_kwh, energy - discharge_w * hours / 1000)
        points.append(SocPoint(zeit=nxt, soc=round(energy / cap_kwh * 100, 1)))
    return points
