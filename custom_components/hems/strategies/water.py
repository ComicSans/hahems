"""Warmwasser-Domäne: empfohlener WW-Sollwert und die zugehörigen Flags.

Priorität: Sperrzeit (aus) > Legionellenschutz > PV-Boost (Komfort) > Basis.
`water_plan` setzt alle WW-Flags (Sperre, Legionellen, PV-Boost, Basis/Komfort-
Thermostat) und den Sollwert direkt im Ergebnis. Muss vor der Empfehlungs-
Priorisierung laufen — die liest die Basis/Komfort-Flags nur noch.
"""
from __future__ import annotations

from .types import PlanInput, PlanResult, _latch

# Totband unter dem WW-Sollwert, bevor Bedarf gemeldet wird (Thermostat-
# Hysterese): Bedarf greift erst, wenn die Temperatur das Band unterschritten
# hat, und fällt erst beim Erreichen des Sollwerts wieder weg.
THERMAL_HYST_K = 2.0


def water_plan(inp: PlanInput, res: PlanResult) -> None:
    """WW-Flags und -Sollwert im Ergebnis setzen (mutiert `res`).

    Reihenfolge im Plan: nach der Speicher-SoC-Berechnung (für das
    Boost-SoC-Kriterium), vor der Empfehlungs-Priorisierung, die die
    Basis/Komfort-Flags liest.
    """
    # Sperre: Fenster durchreichen und prüfen, ob jetzt gesperrt ist.
    res.ww_sperrfenster = list(inp.thermal_block_windows)
    res.ww_gesperrt = any(
        start <= inp.now < end for start, end in inp.thermal_block_windows
    )

    # Legionellenschutz: wöchentliches Fenster mit erhöhtem Sollwert,
    # unabhängig vom Überschuss (Hygiene geht vor, notfalls aus dem Netz).
    res.ww_legionellen_fenster = list(inp.thermal_legionella_windows)
    res.ww_legionelle_aktiv = any(
        start <= inp.now < end for start, end in inp.thermal_legionella_windows
    )

    # PV-Boost-Kriterien: Speicher fast voll UND kräftige Einspeisung.
    # Ohne konfigurierte Speicher entfällt das SoC-Kriterium.
    if inp.storages:
        res.flags.ww_boost_soc = _latch(
            inp.flags.ww_boost_soc,
            res.speicher_soc,
            on=inp.thermal_boost_soc_on,
            off=inp.thermal_boost_soc_off,
        )
    else:
        res.flags.ww_boost_soc = True
    res.flags.ww_boost_saldo = _latch(
        inp.flags.ww_boost_saldo,
        inp.saldo_w,
        on=inp.thermal_boost_saldo_on_w,
        off=inp.thermal_boost_saldo_off_w,
    )

    # Thermostat-Latches: Bedarf wird erst gemeldet, wenn die Temperatur das
    # Totband unter dem Sollwert durchschritten hat, und erst beim Erreichen des
    # Sollwerts wieder fallengelassen.
    res.flags.ww_basis = _latch(
        inp.flags.ww_basis,
        inp.thermal_temp,
        on=inp.thermal_base - THERMAL_HYST_K,
        off=inp.thermal_base,
    )
    res.flags.ww_komfort = _latch(
        inp.flags.ww_komfort,
        inp.thermal_temp,
        on=inp.thermal_comfort - THERMAL_HYST_K,
        off=inp.thermal_comfort,
    )

    # Empfohlener Sollwert nach Priorität: Sperrzeit (aus) > Legionellenschutz >
    # PV-Boost > Basis. Ohne WW-Gerät bleibt ww_soll_c None, Status leer.
    if not inp.thermal_present:
        return
    if res.ww_gesperrt:
        res.ww_soll_c = None
        res.ww_status = "aus"
    elif res.ww_legionelle_aktiv:
        res.ww_soll_c = inp.thermal_legionella_target
        res.ww_status = "legionellenschutz"
    elif (
        res.flags.ww_komfort
        and res.flags.ww_boost_soc
        and res.flags.ww_boost_saldo
    ):
        res.ww_soll_c = inp.thermal_comfort
        res.ww_status = "pv_boost"
    else:
        res.ww_soll_c = inp.thermal_base
        res.ww_status = "basis"
