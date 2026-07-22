"""Heizkreis-Domäne: witterungsgeführte Modus- und Vorlaufempfehlung."""
from __future__ import annotations

from ..const import (
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
    """
    h = inp.heating
    result = HeatingResult(name=h.name, sommer_sperre=h.heat_locked)
    t = h.outdoor_temp_c
    if t is None:
        result.modus = "unbekannt"
        return result
    result.t_aussen_c = t

    res.flags.wp_heizen = (
        False
        if h.heat_locked
        else _latch(inp.flags.wp_heizen, t, on=h.heat_on_c, off=h.heat_off_c)
    )
    res.flags.wp_kuehlen = _latch(
        inp.flags.wp_kuehlen, t, on=h.cool_on_c, off=h.cool_off_c
    )
    # Frostschutz-Latch unabhängig vom Sperr-Zustand: greift auch, wenn die
    # Sommersperre wp_heizen hart auf False zwingt.
    res.flags.wp_frost = _latch(
        inp.flags.wp_frost, t, on=h.frost_on_c, off=h.frost_off_c
    )

    if res.flags.wp_heizen or res.flags.wp_frost:
        # Frostschutz erzwingt Heizen nur, wenn der reguläre Heizbetrieb (evtl.
        # per Sommersperre) aus ist; sonst heizt die Anlage witterungsgeführt.
        frost_only = res.flags.wp_frost and not res.flags.wp_heizen
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
        res.flags.wp_leise = _latch(
            inp.flags.wp_leise,
            result.vlt_ziel_c,
            on=SILENT_VLT_ON_C,
            off=SILENT_VLT_OFF_C,
        )
        result.leise_empfohlen = res.flags.wp_leise
    elif res.flags.wp_kuehlen:
        result.modus = "kuehlen"
        result.vlt_ziel_c = h.cool_vlt_c
    else:
        result.modus = "aus"
    return result
