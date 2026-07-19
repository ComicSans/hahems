"""Heuristik-Planner, Phase 1: nur beobachten und empfehlen.

Reine Funktionen ohne Home-Assistant-Abhängigkeiten, damit die Logik
testbar bleibt und in Phase 4 unverändert für die Simulation taugt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, tzinfo

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
    # Gelerntes Lastprofil: (Tagtyp, UTC-Stunde) → mittlere Last in W, mit
    # Tagtyp 0 = Werktag, 1 = Wochenende. Fehlt der passende Eintrag (oder das
    # ganze Profil), greift die gleiche Stunde im anderen Tagtyp, sonst
    # night_load_w als Fallback.
    load_profile_w: dict[tuple[int, int], float] | None = None
    # Darstellungshorizont der Plankarte: lokal 00:00 heute bis 00:00 über-
    # morgen, vom Coordinator als UTC übergeben (der Planner kennt keine
    # Zeitzone). Kurven werden darauf beschnitten.
    horizon_start: datetime | None = None
    horizon_end: datetime | None = None
    # Sonnenzeiten der beiden Kalendertage im Horizont, für die PV-Glocken.
    today_sunrise: datetime | None = None
    today_sunset: datetime | None = None
    tomorrow_sunrise: datetime | None = None
    tomorrow_sunset: datetime | None = None
    # Warmwasser-Sperrfenster im Horizont, bereits über Mitternacht aufgelöst.
    thermal_block_windows: list[tuple[datetime, datetime]] = field(
        default_factory=list
    )


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
class SocPoint:
    """Stützstelle der SoC-Prognose (Zeitpunkt, erwarteter Gesamt-SoC in %)."""

    zeit: datetime
    soc: float


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
    soc_prognose: list[SocPoint] = field(default_factory=list)
    ww_gesperrt: bool = False
    ww_sperrfenster: list[tuple[datetime, datetime]] = field(default_factory=list)
    empfehlung: str = "keine Daten"
    prioritaeten: list[str] = field(default_factory=list)


def block_windows(
    block_start: str | None,
    block_end: str | None,
    horizon_start: datetime,
    horizon_end: datetime,
    tz: tzinfo,
) -> list[tuple[datetime, datetime]]:
    """Sperrzeit ("HH:MM:SS", lokal) in konkrete Fenster im Horizont auflösen.

    Liegt das Ende vor dem Anfang (18:00 → 06:00), läuft das Fenster über
    Mitternacht; über den 48-h-Horizont ergeben sich daraus bis zu drei
    Fenster: der Rest der laufenden Nacht sowie die beiden folgenden. Gleiche
    Zeiten heißt "keine Sperre". Die Fenster sind auf den Horizont beschnitten
    und tragen dieselbe Zeitzone wie dessen Grenzen.
    """
    start_t = _parse_time(block_start)
    end_t = _parse_time(block_end)
    if start_t is None or end_t is None or start_t == end_t:
        return []

    # Einen Tag vor dem Horizont beginnen, damit ein über Mitternacht
    # laufendes Fenster der Vornacht noch hineinragen kann.
    first_day = horizon_start.astimezone(tz).date() - timedelta(days=1)

    windows: list[tuple[datetime, datetime]] = []
    for offset in range(4):
        day = first_day + timedelta(days=offset)
        # Kalendertage statt +24 h, damit Sommerzeitwechsel die Wanduhrzeit
        # der Sperre nicht verschieben.
        end_day = day + timedelta(days=1) if end_t <= start_t else day
        start = datetime.combine(day, start_t, tzinfo=tz)
        end = datetime.combine(end_day, end_t, tzinfo=tz)
        clipped = (max(start, horizon_start), min(end, horizon_end))
        if clipped[1] > clipped[0]:
            windows.append(clipped)
    return windows


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


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

    # Erwarteter Restverbrauch bis Sonnenuntergang: aus dem gelernten Profil,
    # sofern es die Tagesstunden abdeckt, sonst die konfigurierte Grundlast.
    if _profile_covers(inp, inp.now, inp.sunset):
        expected_day_kwh = _window_load_kwh(inp, inp.now, inp.sunset)
    else:
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

    # Geschätzte PV-Stundenkurve über beide Kalendertage, für die Plankarte
    result.pv_kurve = _pv_curve(inp)

    # Warmwasser-Sperre: Fenster durchreichen und prüfen, ob jetzt gesperrt ist.
    result.ww_sperrfenster = list(inp.thermal_block_windows)
    result.ww_gesperrt = any(
        start <= inp.now < end for start, end in inp.thermal_block_windows
    )

    # SoC-Prognose ab jetzt bis zum Horizontende. Bewusst nicht rückwirkend:
    # bekannt ist nur der aktuelle Stand, alles davor wäre erfunden.
    if known and cap > 0:
        result.soc_prognose = _soc_forecast(inp, result, available, reserve, cap)

    result.prioritaeten = _priorities(inp, result)
    result.empfehlung = (
        " → ".join(result.prioritaeten) if result.prioritaeten else "Einspeisen"
    )
    return result


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


def _soc_forecast(
    inp: PlanInput,
    res: PlanResult,
    available_kwh: float,
    reserve_kwh: float,
    cap_kwh: float,
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
        balance_w = _pv_power_at(res.pv_kurve, t) - _expected_load_w(inp, t)
        if balance_w >= 0:
            charge_w = min(balance_w, max_charge_w)
            energy = min(cap_kwh, energy + charge_w * hours / 1000)
        else:
            discharge_w = min(-balance_w, max_discharge_w)
            energy = max(reserve_kwh, energy - discharge_w * hours / 1000)
        points.append(SocPoint(zeit=nxt, soc=round(energy / cap_kwh * 100, 1)))
    return points


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


def _priorities(inp: PlanInput, res: PlanResult) -> list[str]:
    """Dynamische Reihenfolge für den Überschuss. WW ist Priorität 1, sofern
    keine Sperrzeit läuft; in der Sperrzeit entfallen Basis- und Komfort-
    ladung, der Speicher darf also unter die Basistemperatur auskühlen."""
    prio: list[str] = []

    if res.ww_gesperrt:
        prio.append("WW gesperrt")
    elif inp.thermal_temp is not None and inp.thermal_temp < inp.thermal_base:
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
        not res.ww_gesperrt
        and inp.thermal_temp is not None
        and inp.thermal_temp < inp.thermal_comfort
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
