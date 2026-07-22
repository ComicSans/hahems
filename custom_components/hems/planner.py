"""Heuristik-Planner: Orchestrierung der Domänen-Strategien.

Reine Funktion `compute_plan` ohne Home-Assistant-Abhängigkeiten. Die eigentliche
Domänenlogik liegt in `strategies/`; hier werden die Teilpläne zusammengeführt,
die Warmwasser-Empfehlung eingeholt und die Empfehlungsreihenfolge gebildet.

Zeitfenster-Helfer (block_windows/weekly_windows) bleiben hier, weil der
Coordinator sie zum Bauen der Eingabe nutzt — sie sind Eingabe-Aufbereitung,
keine Regel-Domäne.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta, tzinfo

from .const import (
    GOAL_FULL_CHARGE,
    GOAL_ZERO_FEEDIN,
    PRIORITY_BATTERY_FIRST,
    PRIORITY_EV_FIRST,
    STORAGE_DAY_HOLD_SOC,
)
from .strategies.battery import _lade_deckel_soc, _storage_control
from .strategies.demand import _profile_covers, _window_load_kwh, _wp_window_kwh
from .strategies.forecast import _discharge_plan, _pv_curve, _soc_forecast
from .strategies.heating import _heating_plan
from .strategies.loads import _modulated_control
from .strategies.types import PlanInput, PlanResult, _latch
from .strategies.water import water_plan


# Hysterese-Schwellen. Jede Ja/Nein-Entscheidung des Planners hat ein Ein- und
# ein Ausschaltniveau; dazwischen bleibt der vorige Zustand stehen. Ohne das
# kippt die Empfehlung im Minutentakt, sobald ein Messwert um seine Schwelle
# pendelt (Wolke, Kühlschranktakt, WP-Zyklus).
SURPLUS_ON_W = -200.0  # Netzsaldo, ab dem "Überschuss" gilt (negativ = Einspeisung)


SURPLUS_OFF_W = -50.0  # ... und ab dem er wieder als beendet gilt


KNAPP_ON = 1.3  # Restertrag/Speicherbedarf, ab dem "knapp" greift


KNAPP_OFF = 1.7


WEATHER_ON = 0.30  # Wetterfaktor morgen, ab dem "morgen knapp" greift


WEATHER_OFF = 0.40


PV_TOMORROW_ON = 1.0  # PV morgen / Nachtdefizit, ab dem "morgen knapp" greift


PV_TOMORROW_OFF = 1.15


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


def weekly_windows(
    weekday: int | None,
    start: str | None,
    end: str | None,
    horizon_start: datetime,
    horizon_end: datetime,
    tz: tzinfo,
) -> list[tuple[datetime, datetime]]:
    """Wöchentliches Fenster (Wochentag 0 = Montag, lokale Uhrzeiten) in
    konkrete Fenster im Horizont auflösen — z. B. den Legionellenschutz.

    Gleiche Mechanik wie `block_windows`: Ende vor Anfang läuft über
    Mitternacht (der Wochentag bezeichnet den Starttag), Fenster werden auf
    den Horizont beschnitten. Über 48 h ergeben sich 0–2 Fenster.
    """
    start_t = _parse_time(start)
    end_t = _parse_time(end)
    if weekday is None or start_t is None or end_t is None or start_t == end_t:
        return []

    first_day = horizon_start.astimezone(tz).date() - timedelta(days=1)
    windows: list[tuple[datetime, datetime]] = []
    for offset in range(4):
        day = first_day + timedelta(days=offset)
        if day.weekday() != weekday:
            continue
        end_day = day + timedelta(days=1) if end_t <= start_t else day
        win_start = datetime.combine(day, start_t, tzinfo=tz)
        win_end = datetime.combine(end_day, end_t, tzinfo=tz)
        clipped = (max(win_start, horizon_start), min(win_end, horizon_end))
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
    # Trigger-Zustand des Vorlaufs übernehmen und im Ergebnis fortschreiben,
    # ohne die Eingabe zu verändern.
    result.flags = replace(inp.flags)

    # Sonnenfenster und Nachtdefizit. Ist es bereits Nacht, zeigt inp.sunset
    # schon auf morgen Abend (get_astral_event_next liefert den nächsten noch
    # bevorstehenden Sonnenuntergang) — ohne Sonderfall würde "Sonnenfenster"
    # 24 h lang linear runterzählen und dabei den dazwischenliegenden
    # Sonnenaufgang ignorieren, und "Nachtdefizit" das Fenster der
    # übernächsten statt der laufenden Nacht berechnen. Dieselbe
    # Nacht-Erkennung wie in _discharge_plan.
    ist_nacht = inp.next_sunrise is not None and inp.next_sunrise < inp.sunset
    result.sonnenfenster_h = (
        0.0
        if ist_nacht
        else max(0.0, (inp.sunset - inp.now).total_seconds() / 3600)
    )
    result.nachtdefizit_kwh = round(
        _window_load_kwh(inp, inp.now, inp.next_sunrise)
        if ist_nacht
        else _window_load_kwh(inp, inp.sunset, inp.sunrise),
        2,
    )
    # WP-Anteil am Nachtdefizit separat ausweisen (Transparenz).
    result.wp_nacht_kwh = round(
        _wp_window_kwh(inp, inp.now, inp.next_sunrise)
        if ist_nacht
        else _wp_window_kwh(inp, inp.sunset, inp.sunrise),
        2,
    )

    # Folgetag einpreisen: Meldet das Wetter dichte Bewölkung oder deckt die
    # Morgen-Prognose nicht einmal das Nachtdefizit, wird der Speicher heute
    # voll geladen statt nur bis zum Nachtbedarf. Beide Kriterien mit
    # Hysterese, weil sie über das Ziel-SoC echtes Ladeverhalten steuern.
    result.flags.wetter_knapp = _latch(
        inp.flags.wetter_knapp,
        inp.weather_factor_tomorrow,
        on=WEATHER_ON,
        off=WEATHER_OFF,
    )
    result.flags.pv_morgen_knapp = _latch(
        inp.flags.pv_morgen_knapp,
        inp.pv_tomorrow_kwh / result.nachtdefizit_kwh
        if inp.pv_tomorrow_kwh > 0 and result.nachtdefizit_kwh > 0
        else None,
        on=PV_TOMORROW_ON,
        off=PV_TOMORROW_OFF,
    )
    result.morgen_knapp = result.flags.wetter_knapp or result.flags.pv_morgen_knapp

    # Virtueller Gesamtspeicher aus allen Storages
    cap = sum(s.capacity_kwh for s in inp.storages)
    result.speicher_kapazitaet_kwh = round(cap, 2)
    known = [s for s in inp.storages if s.soc is not None]
    speicher_frei_kwh = 0.0
    # Voll laden, wenn morgen wenig kommt ODER das Ziel es verlangt:
    # Nulleinspeisung braucht maximale Aufnahmekapazität gegen Export,
    # Vollladen hält das Ziel ohnehin dauerhaft auf 100 %. Getrennt vom
    # Ladedeckel weiter unten: ziel_voll steuert das Nacht-Ziel (ziel_kwh),
    # der Deckel nur die Tages-Ladeobergrenze.
    ziel_voll = result.morgen_knapp or inp.goal in (
        GOAL_ZERO_FEEDIN,
        GOAL_FULL_CHARGE,
    )
    voll_noetig = ziel_voll
    if known and cap > 0:
        available = sum(s.soc / 100 * s.capacity_kwh for s in known)
        reserve = sum(s.reserve_soc / 100 * s.capacity_kwh for s in inp.storages)
        result.speicher_verfuegbar_kwh = round(available, 2)
        result.speicher_soc = round(available / cap * 100, 1)
        ziel_kwh = (
            cap if ziel_voll else min(cap, result.nachtdefizit_kwh + reserve)
        )
        result.speicher_ziel_soc = round(ziel_kwh / cap * 100, 1)
        result.speicher_bedarf_kwh = round(max(0.0, ziel_kwh - available), 2)
        speicher_frei_kwh = max(0.0, available - ziel_kwh)

    # Erwarteter Restverbrauch bis Sonnenuntergang: aus dem gelernten Profil,
    # sofern es die Tagesstunden abdeckt, sonst die konfigurierte Grundlast.
    # Die WP kommt in beiden Pfaden aus dem Modell obendrauf (im Profilpfad
    # steckt sie bereits in _window_load_kwh).
    if _profile_covers(inp, inp.now, inp.sunset):
        expected_day_kwh = _window_load_kwh(inp, inp.now, inp.sunset)
    else:
        expected_day_kwh = (
            inp.baseline_load_w * result.sonnenfenster_h / 1000
            + (
                _wp_window_kwh(inp, inp.now, inp.sunset)
                if not ist_nacht
                else 0.0
            )
        )
    result.ueberschuss_rest_kwh = round(
        max(0.0, inp.pv_remaining_kwh - expected_day_kwh), 2
    )

    # Ladedeckel jetzt (Akku-Schonung): tagsüber HOLD, zum Abend per Rampe auf
    # 100 %. Aufgehoben, sobald Nachtdeckung vor Schonung geht — Ziel/morgen
    # knapp (ziel_voll), es ist Nacht (kein Überschuss zu erwarten), oder der
    # Restertrag heute reicht nicht mehr, um später von HOLD auf 100 %
    # nachzuladen (dann sofort voll laden, statt zu leer in die Nacht zu gehen).
    if known and cap > 0:
        topup_kwh = (100.0 - STORAGE_DAY_HOLD_SOC) / 100.0 * cap
        heute_knapp = result.ueberschuss_rest_kwh < topup_kwh
        voll_noetig = ziel_voll or heute_knapp or ist_nacht
        result.lade_deckel_soc = round(
            _lade_deckel_soc(inp, voll_noetig, inp.now), 1
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

    # Entladeplan: verfügbare Akku-Energie als stündliche Obergrenzen über
    # die Nacht verteilen. Live folgt die Entladung dem Saldo (Ziel:
    # Nulleinspeisung, kein Netzexport); die Slot-Werte deckeln sie, damit
    # der Akku bis Sonnenaufgang reicht.
    if known and cap > 0:
        _discharge_plan(inp, result, available, reserve, ziel_kwh, cap)

    # Geschätzte PV-Stundenkurve über beide Kalendertage, für die Plankarte
    result.pv_kurve = _pv_curve(inp)

    # Warmwasser-Domäne: Sperre, Legionellen, PV-Boost, Basis/Komfort-Latches
    # und Sollwert. Muss nach der Speicher-SoC-Berechnung (Boost-Kriterium) und
    # vor der Empfehlungs-Priorisierung laufen, die die WW-Flags nur noch liest.
    water_plan(inp, result)

    # Modulierbare Lasten zuerst: Überschuss-Ladeströme bestimmen. Die
    # Reihenfolge ist bewusst — der Speicher-Regler bekommt anschließend den um
    # die neuen Last-Sollwerte bereinigten Saldo, damit die Lasten vor der
    # Akku-Entladung heruntergeregelt werden.
    result.ev_regelung = _modulated_control(inp, result)
    # Für die Empfehlungs-Zeile: lädt mindestens eine Last?
    result.flags.ev_bereit = bool(
        result.ev_regelung
        and any(sp.laden for sp in result.ev_regelung.lasten)
    )
    ev_target_w = None
    if result.ev_regelung is not None and not result.ev_regelung.zwang:
        # Summe der kommandierten Last-Sollleistung. Nicht laufende Lasten
        # zählen 0, obwohl der Actuator sie ggf. noch auf den Mindeststrom hält
        # (Mindestlaufzeit / kein Schalter). Die Differenz korrigiert der
        # nächste Zyklus über die gemessene Last selbst; sie schlägt Richtung
        # Netz aus, nicht in eine Akku-Überentladung — und skaliert mit der Zahl
        # min_on-gehaltener Lasten.
        ev_target_w = result.ev_regelung.soll_summe_w

    # Saldo-Regelung: Zuteilungsempfehlung über alle Speicher.
    result.regelung = _storage_control(inp, result, ev_target_w=ev_target_w)

    # Heizkreis: Modus- und Vorlauf-Empfehlung.
    if inp.heating is not None:
        result.heizung = _heating_plan(inp, result)

    # SoC-Prognose ab jetzt bis zum Horizontende. Bewusst nicht rückwirkend:
    # bekannt ist nur der aktuelle Stand, alles davor wäre erfunden.
    if known and cap > 0:
        result.soc_prognose = _soc_forecast(
            inp, result, available, reserve, cap, voll_noetig
        )

    result.prioritaeten = _priorities(inp, result)
    result.empfehlung = (
        " → ".join(result.prioritaeten) if result.prioritaeten else "Einspeisen"
    )
    return result


def _priorities(inp: PlanInput, res: PlanResult) -> list[str]:
    """Dynamische Reihenfolge für den Überschuss. WW ist Priorität 1, sofern
    keine Sperrzeit läuft; in der Sperrzeit entfallen Basis- und Komfort-
    ladung, der Speicher darf also unter die Basistemperatur auskühlen."""
    prio: list[str] = []

    # Die WW-Flags (Basis/Komfort-Thermostat, Sperre, Legionellen, PV-Boost) hat
    # water_plan bereits gesetzt; hier werden sie nur noch für die Reihenfolge
    # gelesen.
    if res.ww_gesperrt:
        prio.append("WW gesperrt")
    elif res.ww_legionelle_aktiv:
        prio.append(
            f"Legionellenschutz ({inp.thermal_legionella_target:.0f} °C, notfalls Netz)"
        )
    elif res.flags.ww_basis and inp.thermal_temp is not None:
        prio.append(
            f"WW-Basisladung ({inp.thermal_temp:.0f} → {inp.thermal_base:.0f} °C, notfalls Netz)"
        )

    # Der Momentansaldo ist die unruhigste Größe im ganzen Planner; ohne
    # Totband kippt allein hier die Empfehlung im Minutentakt.
    res.flags.surplus = _latch(
        inp.flags.surplus, inp.saldo_w, on=SURPLUS_ON_W, off=SURPLUS_OFF_W
    )
    surplus_now = res.flags.surplus
    # Bei Zwangsladung nicht früh aussteigen: die "E-Auto laden (Zwang)"-
    # Empfehlung soll auch ohne jeden Überschuss erscheinen.
    if not inp.ev_force and not surplus_now and res.ueberschuss_rest_kwh <= 0:
        if res.speicher_bedarf_kwh > 0:
            prio.append(
                f"kein Überschuss; Akku fehlt {res.speicher_bedarf_kwh} kWh bis Ziel-SoC"
            )
        return prio

    # Komfortladung (PV-Boost) nur, wenn der Speicher fast voll ist und
    # kräftig eingespeist wird — sonst gehört der Überschuss zuerst dem Akku.
    ww_comfort_pending = (
        not res.ww_gesperrt
        and not res.ww_legionelle_aktiv
        and inp.thermal_temp is not None
        and res.flags.ww_komfort
        and res.flags.ww_boost_soc
        and res.flags.ww_boost_saldo
    )
    if ww_comfort_pending:
        prio.append(f"WW-Komfort ({inp.thermal_comfort:.0f} °C, PV-Boost)")

    # E-Auto-Bereitschaft: bereits von der Lastregelung (_modulated_control)
    # bestimmt und in res.flags.ev_bereit hinterlegt (in compute_plan gesetzt).
    # Ohne konfigurierte Wallbox gilt das alte Verhalten (irgendein Überschuss
    # genügt).
    if res.ev_regelung is None:
        res.flags.ev_bereit = True

    grund = " – morgen wenig Ertrag" if res.morgen_knapp else ""
    akku = (
        f"Akku laden bis {res.speicher_ziel_soc:.0f} %"
        f" (+{res.speicher_bedarf_kwh} kWh{grund})"
        if res.speicher_bedarf_kwh > 0
        else None
    )
    auto = None
    if inp.ev_force:
        # Zwangsladung: unabhängig von Überschuss und Mindestleistung.
        auto = "E-Auto laden (Zwang, unabhängig vom Überschuss)"
    elif res.flags.ev_bereit:
        # Aktive Lasten mit Sollstrom auflisten (eine oder mehrere).
        aktiv = (
            [sp for sp in res.ev_regelung.lasten if sp.laden]
            if res.ev_regelung is not None
            else []
        )
        if len(aktiv) == 1:
            auto = f"E-Auto {aktiv[0].strom_a:.0f} A mit Überschuss"
        elif len(aktiv) > 1:
            teile = ", ".join(f"{sp.name} {sp.strom_a:.0f} A" for sp in aktiv)
            auto = f"Lasten mit Überschuss ({teile})"
        else:
            auto = "E-Auto mit Überschuss"

    if inp.ev_force and auto is not None:
        # Zwang hat Vorrang vor jeder Überschussverteilung.
        prio.append(auto)
        if akku is not None:
            prio.append(akku)
    elif akku is not None and auto is not None:
        if inp.priority_mode == PRIORITY_BATTERY_FIRST:
            prio.extend([akku, auto])
        elif inp.priority_mode == PRIORITY_EV_FIRST:
            prio.extend([auto, akku])
        else:
            # Automatik: Reicht der Restertrag nicht für Akku UND Auto, bekommt der
            # Akku Vorrang, damit die Nacht gedeckt ist. Bei reichlich Ertrag darf
            # das Auto zuerst, der Akku wird dann trotzdem noch voll. Mit Totband
            # um das Verhältnis, sonst tauschen Akku und Auto laufend die Plätze.
            res.flags.knapp = _latch(
                inp.flags.knapp,
                res.ueberschuss_rest_kwh / res.speicher_bedarf_kwh
                if res.speicher_bedarf_kwh > 0
                else None,
                on=KNAPP_ON,
                off=KNAPP_OFF,
            )
            prio.extend([akku, auto] if res.flags.knapp else [auto, akku])
    elif akku is not None:
        prio.append(akku)
    elif auto is not None:
        prio.append(auto)

    # "Einspeisen" als Rest-Label nur, wenn tatsächlich Überschuss vorliegt;
    # bei reiner Zwangsladung aus dem Netz wäre es irreführend.
    if surplus_now or res.ueberschuss_rest_kwh > 0:
        prio.append("Einspeisen")
    return prio
