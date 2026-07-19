"""Heuristik-Planner, Phase 1: nur beobachten und empfehlen.

Reine Funktionen ohne Home-Assistant-Abhängigkeiten, damit die Logik
testbar bleibt und in Phase 4 unverändert für die Simulation taugt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .const import PRIORITY_AUTO, PRIORITY_BATTERY_FIRST, PRIORITY_EV_FIRST


@dataclass
class StorageState:
    name: str
    soc: float | None
    capacity_kwh: float
    reserve_soc: float
    max_charge_w: float
    max_discharge_w: float


@dataclass
class PlanInput:
    now: datetime
    sunset: datetime
    sunrise: datetime
    pv_today_kwh: float
    pv_remaining_kwh: float
    pv_tomorrow_kwh: float
    pv_power_now_w: float | None
    saldo_w: float | None  # positiv = Netzbezug
    storages: list[StorageState]
    night_load_w: float
    baseline_load_w: float
    thermal_temp: float | None
    thermal_base: float
    thermal_comfort: float
    priority_mode: str = PRIORITY_AUTO
    weather_factor_tomorrow: float | None = None  # 0 = trüb, 1 = klar
    free_kwh: float = 0.0  # Energiebedarf für "Kapazität frei"
    free_h: float = 1.0  # Dauer, über die der Bedarf gedeckt sein soll
    # Nächster Sonnenaufgang ab jetzt. Nachts liegt er vor dem nächsten
    # Sonnenuntergang und markiert das Ende des laufenden Nachtfensters.
    next_sunrise: datetime | None = None
    # Gelerntes Lastprofil: UTC-Stunde → mittlere Last in W. Fehlt eine
    # Stunde (oder das ganze Profil), gilt night_load_w als Fallback.
    load_profile_w: dict[int, float] | None = None


@dataclass
class PvSlot:
    """Ein Stunden-Slot der geschätzten PV-Leistungskurve."""

    start: datetime
    end: datetime
    watt: float


@dataclass
class DischargeSlot:
    """Ein Stunden-Slot des Einspeiseplans (watt = geplante Obergrenze)."""

    start: datetime
    end: datetime
    watt: float
    soc_erwartet: float | None = None  # erwarteter Gesamt-SoC am Slot-Ende (%)


@dataclass
class PlanResult:
    nachtdefizit_kwh: float = 0.0
    ueberschuss_rest_kwh: float = 0.0
    speicher_soc: float | None = None
    speicher_verfuegbar_kwh: float = 0.0
    speicher_kapazitaet_kwh: float = 0.0
    speicher_ziel_soc: float | None = None
    speicher_bedarf_kwh: float = 0.0
    sonnenfenster_h: float = 0.0
    morgen_knapp: bool = False
    kapazitaet_frei: bool = False
    kapazitaet_frei_kwh: float = 0.0
    einspeise_budget_kwh: float = 0.0
    einspeise_w_jetzt: float | None = None
    einspeiseplan: list[DischargeSlot] = field(default_factory=list)
    pv_kurve: list[PvSlot] = field(default_factory=list)
    empfehlung: str = "keine Daten"
    prioritaeten: list[str] = field(default_factory=list)


def compute_plan(inp: PlanInput) -> PlanResult:
    result = PlanResult()

    # Sonnenfenster und Nachtdefizit
    result.sonnenfenster_h = max(
        0.0, (inp.sunset - inp.now).total_seconds() / 3600
    )
    result.nachtdefizit_kwh = round(
        _window_load_kwh(inp, inp.sunset, inp.sunrise), 2
    )

    # Folgetag einpreisen: Meldet das Wetter dichte Bewölkung oder deckt die
    # Morgen-Prognose nicht einmal das Nachtdefizit, wird der Speicher heute
    # voll geladen statt nur bis zum Nachtbedarf.
    result.morgen_knapp = (
        inp.weather_factor_tomorrow is not None
        and inp.weather_factor_tomorrow < 0.35
    ) or (0 < inp.pv_tomorrow_kwh < result.nachtdefizit_kwh)

    # Virtueller Gesamtspeicher aus allen Storages
    cap = sum(s.capacity_kwh for s in inp.storages)
    result.speicher_kapazitaet_kwh = round(cap, 2)
    known = [s for s in inp.storages if s.soc is not None]
    speicher_frei_kwh = 0.0
    if known and cap > 0:
        available = sum(s.soc / 100 * s.capacity_kwh for s in known)
        reserve = sum(s.reserve_soc / 100 * s.capacity_kwh for s in inp.storages)
        result.speicher_verfuegbar_kwh = round(available, 2)
        result.speicher_soc = round(available / cap * 100, 1)
        ziel_kwh = (
            cap if result.morgen_knapp else min(cap, result.nachtdefizit_kwh + reserve)
        )
        result.speicher_ziel_soc = round(ziel_kwh / cap * 100, 1)
        result.speicher_bedarf_kwh = round(max(0.0, ziel_kwh - available), 2)
        speicher_frei_kwh = max(0.0, available - ziel_kwh)

    # Erwarteter Restverbrauch des Tages (v1: Grundlast; lernendes Profil folgt)
    expected_day_kwh = inp.baseline_load_w * result.sonnenfenster_h / 1000
    result.ueberschuss_rest_kwh = round(
        max(0.0, inp.pv_remaining_kwh - expected_day_kwh), 2
    )

    # Kapazität frei: Kann ein zusätzlicher Verbraucher free_kwh über free_h
    # ziehen, ohne Reserve und Nachtdeckung anzutasten? Anrechenbar sind der
    # Speicherstand oberhalb des Ziel-SoC und der Anteil des PV-Rest-
    # überschusses, der in die Dauer fällt.
    if result.sonnenfenster_h > 0:
        pv_anteil = min(inp.free_h / result.sonnenfenster_h, 1.0)
    else:
        pv_anteil = 0.0
    result.kapazitaet_frei_kwh = round(
        speicher_frei_kwh + result.ueberschuss_rest_kwh * pv_anteil, 2
    )
    result.kapazitaet_frei = (
        inp.free_kwh > 0 and result.kapazitaet_frei_kwh >= inp.free_kwh
    )

    # Einspeiseplan: verfügbare Akku-Energie als stündliche Obergrenzen über
    # die Nacht verteilen. Live folgt die Einspeisung dem Saldo (Nulleinspeisung);
    # die Slot-Werte deckeln sie, damit der Akku bis Sonnenaufgang reicht.
    if known and cap > 0:
        _discharge_plan(inp, result, available, reserve, ziel_kwh, cap)

    # Geschätzte PV-Stundenkurve für heute (Rest) und morgen, für die Plankarte
    result.pv_kurve = _pv_curve(inp)

    result.prioritaeten = _priorities(inp, result)
    result.empfehlung = (
        " → ".join(result.prioritaeten) if result.prioritaeten else "Einspeisen"
    )
    return result


def _expected_load_w(inp: PlanInput, t: datetime) -> float:
    """Erwartete Last zur Stunde von t: gelerntes Profil, sonst Nachtlast."""
    if inp.load_profile_w and t.hour in inp.load_profile_w:
        return inp.load_profile_w[t.hour]
    return inp.night_load_w


def _hour_slots(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    slots = []
    t = start
    while t < end:
        nxt = min(t + timedelta(hours=1), end)
        slots.append((t, nxt))
        t = nxt
    return slots


def _window_load_kwh(inp: PlanInput, start: datetime, end: datetime) -> float:
    """Erwartete Verbrauchsenergie im Fenster, stundenweise aus dem Profil."""
    return sum(
        _expected_load_w(inp, t) * (nxt - t).total_seconds() / 3600 / 1000
        for t, nxt in _hour_slots(start, end)
    )


def _discharge_plan(
    inp: PlanInput,
    res: PlanResult,
    available_kwh: float,
    reserve_kwh: float,
    ziel_kwh: float,
    cap_kwh: float,
) -> None:
    """Stunden-Slots für die nächtliche Einspeisung berechnen.

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
    res.einspeise_budget_kwh = round(budget_kwh, 2)

    # Wunschleistung je Slot aus dem Lastprofil, gedeckelt auf die
    # Entladeleistung; bei knappem Budget alle Slots proportional strecken.
    raw = [
        (t, nxt, min(_expected_load_w(inp, t), max_discharge_w))
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
            res.einspeise_w_jetzt = watt
    res.einspeiseplan = slots


def _pv_curve(inp: PlanInput) -> list[PvSlot]:
    """PV-Prognose als Stundenkurve: Tagesenergie sinusförmig über das
    Sonnenfenster verteilt (grobe Glocke, als "geschätzt" gekennzeichnet)."""
    curve: list[PvSlot] = []
    night = inp.next_sunrise is not None and inp.next_sunrise < inp.sunset
    if night:
        # Nur der kommende Tag: nächster Aufgang → nächster Untergang. Vor
        # Mitternacht (UTC-Datumsgrenze als Näherung) liefert der Morgen-Wert
        # die Energie, danach meist schon die Rest-Prognose.
        energy = (
            inp.pv_tomorrow_kwh
            if inp.now.date() != inp.next_sunrise.date()
            else (inp.pv_remaining_kwh or inp.pv_tomorrow_kwh)
        )
        curve += _day_curve(inp.next_sunrise, inp.sunset, energy, inp.now)
    else:
        # Rest von heute (heutiger Aufgang ≈ morgiger minus 24 h) …
        curve += _day_curve(
            inp.sunrise - timedelta(hours=24), inp.sunset, inp.pv_remaining_kwh, inp.now
        )
        # … und der komplette morgige Tag (Untergang ≈ heutiger plus 24 h)
        curve += _day_curve(
            inp.sunrise, inp.sunset + timedelta(hours=24), inp.pv_tomorrow_kwh, inp.now
        )
    return curve


def _day_curve(
    day_start: datetime, day_end: datetime, energy_kwh: float, now: datetime
) -> list[PvSlot]:
    """Energie eines Tages sinusförmig auf Stunden-Slots ab `now` verteilen."""
    total_s = (day_end - day_start).total_seconds()
    if total_s <= 0 or energy_kwh <= 0:
        return []
    start = max(day_start, now)
    raw: list[tuple[datetime, datetime, float]] = []
    for t, nxt in _hour_slots(start, day_end):
        mid = t + (nxt - t) / 2
        shape = math.sin(math.pi * (mid - day_start).total_seconds() / total_s)
        raw.append((t, nxt, max(0.0, shape)))
    weighted = sum(s * (nxt - t).total_seconds() / 3600 for t, nxt, s in raw)
    if weighted <= 0:
        return []
    scale = energy_kwh * 1000 / weighted
    return [PvSlot(start=t, end=nxt, watt=round(s * scale)) for t, nxt, s in raw]


def _priorities(inp: PlanInput, res: PlanResult) -> list[str]:
    """Dynamische Reihenfolge für den Überschuss. WW ist immer Priorität 1."""
    prio: list[str] = []

    if inp.thermal_temp is not None and inp.thermal_temp < inp.thermal_base:
        prio.append(
            f"WW-Basisladung ({inp.thermal_temp:.0f} → {inp.thermal_base:.0f} °C, notfalls Netz)"
        )

    surplus_now = inp.saldo_w is not None and inp.saldo_w < -100
    if not surplus_now and res.ueberschuss_rest_kwh <= 0:
        if res.speicher_bedarf_kwh > 0:
            prio.append(
                f"kein Überschuss; Akku fehlt {res.speicher_bedarf_kwh} kWh bis Ziel-SoC"
            )
        return prio

    ww_comfort_pending = (
        inp.thermal_temp is not None and inp.thermal_temp < inp.thermal_comfort
    )
    if ww_comfort_pending:
        prio.append(f"WW-Komfort ({inp.thermal_comfort:.0f} °C)")

    grund = " – morgen wenig Ertrag" if res.morgen_knapp else ""
    akku = (
        f"Akku laden bis {res.speicher_ziel_soc:.0f} %"
        f" (+{res.speicher_bedarf_kwh} kWh{grund})"
        if res.speicher_bedarf_kwh > 0
        else None
    )
    auto = "E-Auto mit Überschuss"
    if akku is None:
        prio.append(auto)
    elif inp.priority_mode == PRIORITY_BATTERY_FIRST:
        prio.extend([akku, auto])
    elif inp.priority_mode == PRIORITY_EV_FIRST:
        prio.extend([auto, akku])
    else:
        # Automatik: Reicht der Restertrag nicht für Akku UND Auto, bekommt der
        # Akku Vorrang, damit die Nacht gedeckt ist. Bei reichlich Ertrag darf
        # das Auto zuerst, der Akku wird dann trotzdem noch voll.
        knapp = res.ueberschuss_rest_kwh < res.speicher_bedarf_kwh * 1.5
        prio.extend([akku, auto] if knapp else [auto, akku])

    prio.append("Einspeisen")
    return prio
