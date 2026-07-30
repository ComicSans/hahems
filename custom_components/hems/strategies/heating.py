"""Heizkreis-Domäne: witterungsgeführte Modus- und Vorlaufempfehlung."""
from __future__ import annotations

from datetime import timedelta

from ..const import (
    ANTITAKT_RELEASE_MIN,
    HEATING_COLD_THRESHOLD_C,
    HEATING_DEMAND_SHIFT_K,
    SILENT_VLT_OFF_C,
    SILENT_VLT_ON_C,
)
from .types import HeatingResult, PlanInput, PlanResult, _latch


def _heating_plan(inp: PlanInput, res: PlanResult) -> HeatingResult:
    """Heizkreis: Modus über Außentemperatur-Hysterese, Vorlauf aus der Kurve.

    Heizen unterliegt der Sommersperre; Kühlen greift oberhalb der eigenen
    Schwellen. Im Heizbetrieb hebt die Wärmeanforderung der Räume die
    witterungsgeführte Kurve an; ohne Anforderung fällt der Vorlauf auf das
    Minimum (Absenkbetrieb). Der Vorlauf bleibt zwischen Minimum und Maximum.

    Frostschutz übersteuert die Sommersperre: Fällt die Außentemperatur unter
    die Frostschwelle (mit eigener Hysterese), wird Heizen erzwungen, damit der
    Heizkreis in den Sperrmonaten bei Spätfrost nicht einfriert. Der Vorlauf
    bleibt dabei auf dem Minimum — Ziel ist Umwälzung, nicht Komfort.

    Die Warmwasser-Rückmeldung (optionale Rolle) wird nur durchgereicht: die
    Empfehlung bleibt, was sie ist, gestellt wird in diesem Fenster nichts;
    das entscheidet ``plan_heating_control``.
    """
    h = inp.heating
    result = HeatingResult(
        name=h.name, sommer_sperre=h.heat_locked, ww_bereitung=h.dhw_active
    )
    t = h.outdoor_temp_c
    if t is None:
        result.modus = "unbekannt"
        _taktschutz(inp, res, result)
        return result
    result.t_aussen_c = t

    res.flags.waermepumpe_heizen = (
        False
        if h.heat_locked
        else _latch(inp.flags.waermepumpe_heizen, t, on=h.heat_on_c, off=h.heat_off_c)
    )
    res.flags.waermepumpe_kuehlen = _latch(
        inp.flags.waermepumpe_kuehlen, t, on=h.cool_on_c, off=h.cool_off_c
    )
    # Frostschutz-Latch unabhängig vom Sperr-Zustand: greift auch, wenn die
    # Sommersperre waermepumpe_heizen hart auf False zwingt.
    res.flags.waermepumpe_frost = _latch(
        inp.flags.waermepumpe_frost, t, on=h.frost_on_c, off=h.frost_off_c
    )

    if res.flags.waermepumpe_heizen or res.flags.waermepumpe_frost:
        # Frostschutz erzwingt Heizen nur, wenn der reguläre Heizbetrieb (evtl.
        # per Sommersperre) aus ist; sonst heizt die Anlage witterungsgeführt.
        frost_only = res.flags.waermepumpe_frost and not res.flags.waermepumpe_heizen
        result.modus = "heizen"
        result.frostschutz = frost_only
        vlt_min = (
            h.vlt_min_cold_c if t < HEATING_COLD_THRESHOLD_C else h.vlt_min_c
        )
        if frost_only or (h.demand_pct is not None and h.demand_pct < 1):
            vlt = vlt_min
        else:
            vlt = h.curve_base_c - t * h.curve_slope
            if h.demand_pct is not None:
                vlt += h.demand_pct / 100 * HEATING_DEMAND_SHIFT_K
            vlt = max(vlt_min, min(vlt, h.vlt_max_c))
        result.vlt_ziel_c = float(round(vlt))
        res.flags.waermepumpe_leise = _latch(
            inp.flags.waermepumpe_leise,
            result.vlt_ziel_c,
            on=SILENT_VLT_ON_C,
            off=SILENT_VLT_OFF_C,
        )
        result.leise_empfohlen = res.flags.waermepumpe_leise
    elif res.flags.waermepumpe_kuehlen:
        result.modus = "kuehlen"
        result.vlt_ziel_c = h.cool_vlt_c
    else:
        result.modus = "aus"
    _taktschutz(inp, res, result)
    return result


def _taktschutz(inp: PlanInput, res: PlanResult, result: HeatingResult) -> None:
    """Zwangspause gegen zu häufige Verdichterstarts (nur Kühlbetrieb).

    Gezählt werden die Einschaltflanken der Rolle „Verdichter läuft" in einem
    Fenster fester Länge. Reißt die Zahl die Schwelle, empfiehlt HEMS für die
    Pausendauer „aus"; die Anlage bekommt damit eine lange Ruhephase statt der
    vier Minuten, die ihre eigene Wiederanlaufsperre hergibt. Das senkt die
    STARTRATE — der einzelne Takt wird davon nicht länger.

    Zwei Schwellen, wie bei jeder Ja/Nein-Entscheidung hier: die Startzahl legt
    die Pause ein, `ANTITAKT_RELEASE_MIN` hält sie danach für eine Weile fern.
    Ohne diese zweite Schwelle könnte HEMS den Kühlbetrieb dauerhaft aussperren.

    Nur Kühlen: Für den Heizbetrieb gibt es keine Messung, ob und wie er taktet,
    und eine halbe Stunde Zwangspause im Winter ist eine Komfortentscheidung,
    die niemand belegt hat. Die Zählung läuft trotzdem immer mit, damit das
    Taktverhalten in beiden Betriebsarten sichtbar ist.

    Während einer Warmwasserladung werden keine Starts gezählt: die gehören dem
    Speicher, nicht dem Heizkreis, und würden den Taktschutz grundlos auslösen.
    """
    h = inp.heating
    prev, now = inp.flags, inp.now
    fenster = timedelta(minutes=h.antitakt_window_min)

    # Verdichterzustand fortschreiben. Ohne Rolle (oder bei nicht erreichbarer
    # Entität) hält der letzte bekannte Zustand: eine Lücke im Signal darf
    # keine Flanke erfinden, wenn der Wert zurückkommt.
    an = h.compressor_on
    res.flags.takt_verdichter_an = prev.takt_verdichter_an if an is None else an
    start = an is True and not prev.takt_verdichter_an and not h.dhw_active

    fenster_start = prev.takt_fenster_start
    starts = prev.takt_starts
    if fenster_start is None or now - fenster_start >= fenster:
        fenster_start, starts = now, 0
    if start:
        starts += 1

    pause_bis, frei_seit = prev.takt_pause_bis, prev.takt_frei_seit
    if pause_bis is not None and now >= pause_bis:
        # Pause abgelaufen: freigeben und mit einem frischen Fenster weiterzählen.
        pause_bis, frei_seit = None, now
        fenster_start, starts = now, 0
    elif (
        pause_bis is None
        and result.modus == "kuehlen"
        and h.antitakt_starts > 0
        and starts >= h.antitakt_starts
        and (
            frei_seit is None
            or now - frei_seit >= timedelta(minutes=ANTITAKT_RELEASE_MIN)
        )
    ):
        pause_bis = now + timedelta(minutes=h.antitakt_pause_min)

    res.flags.takt_fenster_start = fenster_start
    res.flags.takt_starts = starts
    res.flags.takt_pause_bis = pause_bis
    res.flags.takt_frei_seit = frei_seit

    result.verdichterstarts = starts
    # Die Pause übersteuert nur die Ausgabe. Der Kühl-Latch läuft weiter mit der
    # Außentemperatur mit — würde er zurückgesetzt, finge die Hysterese nach
    # jeder Pause von vorn an und HEMS bekäme sein eigenes Takten.
    if pause_bis is not None and result.modus == "kuehlen":
        result.modus = "aus"
        result.vlt_ziel_c = None
        result.taktschutz = True
        result.taktschutz_bis = pause_bis
