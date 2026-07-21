"""Heuristik-Planner, Phase 1: nur beobachten und empfehlen.

Reine Funktionen ohne Home-Assistant-Abhängigkeiten, damit die Logik
testbar bleibt und in Phase 4 unverändert für die Simulation taugt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta, tzinfo

from .const import (
    CONTROL_DEADBAND_W,
    CONTROL_GAIN_CHARGE,
    CONTROL_GAIN_DISCHARGE,
    CONTROL_MIN_SETPOINT_W,
    CONTROL_TARGET_OFFSET_W,
    CONTROL_ZERO_FEEDIN_OFFSET_W,
    DEFAULT_BOOST_SALDO_OFF_W,
    DEFAULT_BOOST_SALDO_ON_W,
    DEFAULT_BOOST_SOC_OFF,
    DEFAULT_BOOST_SOC_ON,
    DEFAULT_LEGIONELLA_TARGET,
    EV_DEMAND_FLOOR_W,
    EV_DEMAND_GRACE_S,
    EV_SURPLUS_MARGIN_W,
    EV_VOLTAGE_PER_PHASE_V,
    GOAL_FULL_CHARGE,
    GOAL_SELF_CONSUMPTION,
    GOAL_ZERO_FEEDIN,
    HEATING_COLD_THRESHOLD_C,
    HEATING_DEMAND_SHIFT_K,
    PRIORITY_AUTO,
    PRIORITY_BATTERY_FIRST,
    PRIORITY_EV_FIRST,
    RESERVE_SOC_OFF,
    RESERVE_SOC_ON,
    SILENT_VLT_OFF_C,
    SILENT_VLT_ON_C,
)

# Hysterese-Schwellen. Jede Ja/Nein-Entscheidung des Planners hat ein Ein- und
# ein Ausschaltniveau; dazwischen bleibt der vorige Zustand stehen. Ohne das
# kippt die Empfehlung im Minutentakt, sobald ein Messwert um seine Schwelle
# pendelt (Wolke, Kühlschranktakt, WP-Zyklus).
SURPLUS_ON_W = -200.0  # Netzsaldo, ab dem "Überschuss" gilt (negativ = Einspeisung)
SURPLUS_OFF_W = -50.0  # ... und ab dem er wieder als beendet gilt
KNAPP_ON = 1.3  # Restertrag/Speicherbedarf, ab dem "knapp" greift
KNAPP_OFF = 1.7
THERMAL_HYST_K = 2.0  # Totband unter dem WW-Sollwert, bevor Bedarf gemeldet wird
WEATHER_ON = 0.30  # Wetterfaktor morgen, ab dem "morgen knapp" greift
WEATHER_OFF = 0.40
# Rotations-Malus (kWh) für eine beobachtet-leere Last: groß genug, um jede
# realistische Tagesenergie zu überbieten, damit eine leere Last in der
# Rangfolge stets hinter jede nicht-leere fällt — aber endlich, damit sie ohne
# Konkurrenz (einzige Last, oder echte Restkapazität) weiterläuft.
EV_LEER_PENALTY_KWH = 1_000_000.0
PV_TOMORROW_ON = 1.0  # PV morgen / Nachtdefizit, ab dem "morgen knapp" greift
PV_TOMORROW_OFF = 1.15


@dataclass
class StorageState:
    name: str
    soc: float | None
    capacity_kwh: float
    reserve_soc: float
    max_charge_w: float
    max_discharge_w: float
    # Gemessene Ist-Leistung (positiv = Entladen ins Haus); macht die
    # Saldo-Regelung selbstkorrigierend, ohne Abschalt-Mess-Zyklus.
    power_w: float | None = None
    cold_reserve: bool = False


@dataclass
class HeatingState:
    """Eingaben für die Heizkreis-Empfehlung (witterungsgeführt)."""

    name: str
    outdoor_temp_c: float | None
    demand_pct: float | None  # Wärmeanforderung der Räume, 0–100 %
    heat_locked: bool  # Sommersperre aktiv (Kalendermonat, vom Aufrufer)
    heat_on_c: float
    heat_off_c: float
    cool_on_c: float
    cool_off_c: float
    curve_base_c: float
    curve_slope: float
    vlt_min_c: float
    vlt_min_cold_c: float
    vlt_max_c: float
    cool_vlt_c: float


@dataclass
class WpModel:
    """Verbrauchsmodell der Wärmepumpe: P = base_w + k × (Heizgrenze − T).

    Vom Coordinator aus der Langzeitstatistik gelernt (oder Richtwert-
    Fallback). base_w deckt Warmwasser und Standby oberhalb der Heizgrenze,
    der k-Term den witterungsabhängigen Heizanteil. max_w deckelt auf die
    historisch beobachtete Spitzenleistung.
    """

    base_w: float
    k_w_per_k: float
    limit_c: float  # Heizgrenze (heat_off_c des Heizkreises)
    max_w: float | None = None


@dataclass
class ModulatedState:
    """Zustand einer modulierbaren Last (Wallbox …) für die Überschussregelung.

    min_w = min_a × phases × EV_VOLTAGE_PER_PHASE_V; darunter kann die Last real
    gar nicht laufen. Der Regler verteilt den Überschuss über alle Lasten
    innerhalb ihres Schwankungsbereichs [min, max] und regelt sie bei Defizit
    vor dem Akku herunter.

    Fairness/Rotation (Regime 2, wenn der Überschuss nicht für alle Minima
    reicht): `energie_heute_kwh` ist der Fairness-Schlüssel (wer wenig geladen
    hat, kommt zuerst); `ist_an`/`an_seit_s` liefern Ist-Schaltlage und
    Mindestlaufzeit-Schutz; `nachfrage` (gemessene Leistung über Schwelle)
    trennt „lädt gerade" von „an, aber kein Auto" — nur nachfragende Lasten
    konkurrieren um knappe Kapazität. Alle Laufzeit-Felder füllt der
    Coordinator; der Planner entscheidet daraus rein funktional.
    """

    name: str
    min_a: float
    phases: int
    id: str = ""  # eindeutiger Schlüssel (Join über alle Ebenen, nicht der Name)
    max_a: float = 16.0
    priority: int = 1
    min_on_min: int = 10
    hat_schalter: bool = True
    power_w: float | None = None
    energie_heute_kwh: float = 0.0
    ist_an: bool = False
    an_seit_s: float | None = None
    nachfrage: bool = False
    # Vom Coordinator gesetzt: an, aber nach der Anlaufzeit ohne nennenswerte
    # Leistung (kein/volles Auto) — wird in der Rotationsrangfolge nach hinten
    # gestellt (Cooldown), damit sie einer real ladenden Last weicht.
    leer: bool = False

    @property
    def min_w(self) -> float:
        return self.min_a * self.phases * EV_VOLTAGE_PER_PHASE_V

    @property
    def max_w(self) -> float:
        return self.max_a * self.phases * EV_VOLTAGE_PER_PHASE_V


@dataclass
class StorageSetpoint:
    """Empfohlener Sollwert eines Speichers (watt >= 0, Richtung = modus)."""

    name: str
    watt: float


@dataclass
class ControlResult:
    """Empfehlung der Saldo-Regelung über alle Speicher.

    Proportionalregler auf den Netzsaldo: fehler_w = Saldo + Ziel-Offset,
    soll_w = gemessene Speicherleistung + fehler_w × Gain (asymmetrisch:
    schnell gegen Bezug, gemächlich beim Laden). Positive soll_w heißt
    entladen, negative laden; im Totband ruht die Regelung.
    """

    modus: str  # "entladen" | "laden" | "pausiert"
    fehler_w: float
    soll_w: float
    zuteilung: list[StorageSetpoint] = field(default_factory=list)
    reserve_aktiv: bool = False
    reserve_namen: list[str] = field(default_factory=list)


@dataclass
class ModulatedSetpoint:
    """Empfehlung für eine einzelne modulierbare Last."""

    name: str
    laden: bool
    strom_a: float | None
    id: str = ""  # eindeutiger Schlüssel (Join zum Gerät im Actuator)
    soll_w: float = 0.0
    grund: str = ""  # Kurzbegründung (Transparenz), z. B. "min_on gehalten"


@dataclass
class EvControlResult:
    """Empfehlung der Wallbox-/Lastregelung — HEMS besitzt den Überschussstrom.

    Der Überschuss VOR dem Akku (`ueberschuss_w`) wird über alle modulierbaren
    Lasten innerhalb ihres Schwankungsbereichs verteilt; sinkt er ins Defizit,
    werden sie heruntergeregelt, bevor der Akku entlädt. Reicht er nicht für
    alle Minima, entscheidet Priorität (grob) und Energie-Fairness (Rotation
    innerhalb gleichrangiger Lasten), welche laufen. `soll_summe_w` ist die
    Summe der Sollleistung (Kopplung an den Speicher-Regler). `zwang` markiert
    die Sofortladung (volle Ampere, unabhängig vom Überschuss).
    """

    lasten: list[ModulatedSetpoint]
    ueberschuss_w: float
    soll_summe_w: float = 0.0
    zwang: bool = False


@dataclass
class HeatingResult:
    """Empfehlung für den Heizkreis: Modus plus Vorlauf-Sollwert."""

    name: str
    modus: str = "unbekannt"  # "heizen" | "kuehlen" | "aus" | "unbekannt"
    vlt_ziel_c: float | None = None
    t_aussen_c: float | None = None
    sommer_sperre: bool = False
    leise_empfohlen: bool | None = None  # Flüsterbetrieb reicht (niedriger Vorlauf)


@dataclass
class PlanFlags:
    """Zustand der Schmitt-Trigger zwischen zwei Planläufen.

    Der Planner bleibt eine reine Funktion: Der Aufrufer reicht die Flags des
    letzten Laufs in `PlanInput` hinein und übernimmt die neuen aus
    `PlanResult`. Startwerte sind bewusst konservativ (kein Überschuss, Akku
    vor Auto), damit der erste Lauf nach einem Neustart nicht zu optimistisch
    ausfällt.
    """

    surplus: bool = False
    knapp: bool = True
    ww_basis: bool = False
    ww_komfort: bool = False
    wetter_knapp: bool = False
    pv_morgen_knapp: bool = False
    # PV-Boost-Kriterien fürs Warmwasser: Speicher fast voll bzw. kräftige
    # Einspeisung, jeweils mit eigener Ein-/Aus-Schwelle.
    ww_boost_soc: bool = False
    ww_boost_saldo: bool = False
    # Kaltreserve der Saldo-Regelung: Reserve-Speicher entladen mit, solange
    # der mittlere SoC der übrigen unten ist.
    kaltreserve: bool = False
    # Heizkreis-Modus (Außentemperatur-Hysterese) und Flüster-Empfehlung.
    wp_heizen: bool = False
    wp_kuehlen: bool = False
    wp_leise: bool = False
    # E-Auto: Überschuss reicht (mit Marge) für die Wallbox-Mindestleistung.
    # Start konservativ False, damit der erste Lauf nach einem Neustart nicht
    # sofort "E-Auto laden" meldet, ohne den Momentanüberschuss zu kennen.
    ev_bereit: bool = False


def _latch(prev: bool, value: float | None, on: float, off: float) -> bool:
    """Schmitt-Trigger: True erst ab `on`, False erst wieder ab `off`.

    `on < off` heißt "aktiv, solange der Wert klein ist" (z. B. Temperatur
    unter Sollwert), `on > off` die umgekehrte Richtung. Zwischen beiden
    Schwellen – und wenn der Messwert fehlt – bleibt `prev` stehen.
    """
    if value is None:
        return prev
    if on < off:
        if value <= on:
            return True
        if value >= off:
            return False
    else:
        if value >= on:
            return True
        if value <= off:
            return False
    return prev


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
    # Ob überhaupt ein Warmwasser-Gerät konfiguriert ist; ohne eines bleiben
    # ww_soll_c/ww_status leer, statt den Default-Sollwert zu melden.
    thermal_present: bool = True
    priority_mode: str = PRIORITY_AUTO
    # Optimierungsziel (Laufzeit): steuert Ladeziel-SoC und Regler-Offset.
    goal: str = GOAL_SELF_CONSUMPTION
    # E-Auto-Zwangsladung: lädt unabhängig von Überschuss und Wallbox-
    # Mindestleistung. Die Wallbox-Last wird dann aus dem Saldo herausgerechnet,
    # den die Speicher-Regelung sieht, damit der Hausakku nicht still ins Auto
    # leerläuft ("Akku schonen"); das Zwangs-Delta kommt aus dem Netz.
    ev_force: bool = False
    # Aktuelle Wallbox-Leistung (W, Bezug), nur für die Saldo-Bereinigung bei
    # Zwangsladung; sonst ungenutzt.
    wallbox_w: float | None = None
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
    # Legionellenschutz-Fenster im Horizont (wöchentlich, vom Aufrufer über
    # weekly_windows aufgelöst) samt Zieltemperatur.
    thermal_legionella_windows: list[tuple[datetime, datetime]] = field(
        default_factory=list
    )
    thermal_legionella_target: float = DEFAULT_LEGIONELLA_TARGET
    # PV-Boost-Schwellen fürs Warmwasser (Hysterese: Ein-/Aus-Niveau).
    thermal_boost_soc_on: float = DEFAULT_BOOST_SOC_ON
    thermal_boost_soc_off: float = DEFAULT_BOOST_SOC_OFF
    thermal_boost_saldo_on_w: float = DEFAULT_BOOST_SALDO_ON_W
    thermal_boost_saldo_off_w: float = DEFAULT_BOOST_SALDO_OFF_W
    # Witterungsgeführter Heizkreis (optional).
    heating: HeatingState | None = None
    # Modulierbare Lasten (Wallboxen …) für die Überschussregelung. Leer =
    # keine Wallbox konfiguriert; dann bleibt die alte, ungeprüfte Empfehlung.
    modulateds: list[ModulatedState] = field(default_factory=list)
    # Wärmepumpen-Verbrauchsmodell. Ist es gesetzt, ist das Lastprofil
    # WP-bereinigt (der Coordinator zieht die WP-Statistik beim Lernen ab)
    # und die WP wird hier explizit temperaturabhängig aufgeschlagen —
    # so folgt die Bedarfsprognose der Wettervorhersage statt dem
    # 28-Tage-Mittel hinterherzulaufen.
    wp_model: WpModel | None = None
    # Stündliche Temperaturvorhersage (UTC-Stundenanfang → °C) aus der
    # Wetterintegration; fehlende Stunden fallen auf die aktuelle
    # Außentemperatur des Heizkreises zurück.
    temp_forecast_c: dict[datetime, float] | None = None
    # Schmitt-Trigger-Zustand des vorigen Laufs; siehe PlanFlags.
    flags: PlanFlags = field(default_factory=PlanFlags)


@dataclass
class PvSlot:
    """Ein Stunden-Slot der geschätzten PV-Leistungskurve."""

    start: datetime
    end: datetime
    watt: float


@dataclass
class DischargeSlot:
    """Ein Stunden-Slot des Entladeplans (watt = geplante Obergrenze)."""

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
    # Anteil der Wärmepumpe am Nachtdefizit (bereits darin enthalten),
    # nur zur Transparenz separat ausgewiesen.
    wp_nacht_kwh: float = 0.0
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
    entlade_budget_kwh: float = 0.0
    entlade_w_jetzt: float | None = None
    entladeplan: list[DischargeSlot] = field(default_factory=list)
    pv_kurve: list[PvSlot] = field(default_factory=list)
    soc_prognose: list[SocPoint] = field(default_factory=list)
    ww_gesperrt: bool = False
    ww_sperrfenster: list[tuple[datetime, datetime]] = field(default_factory=list)
    # Warmwasser-Orchestrierung: empfohlener Sollwert nach Priorität
    # Legionellenschutz > PV-Boost > Basis; in der Sperrzeit None ("aus").
    ww_soll_c: float | None = None
    ww_status: str = ""  # "aus" | "legionellenschutz" | "pv_boost" | "basis"
    ww_legionelle_aktiv: bool = False
    ww_legionellen_fenster: list[tuple[datetime, datetime]] = field(
        default_factory=list
    )
    # Empfehlung der Saldo-Regelung über alle Speicher (None ohne Daten).
    regelung: ControlResult | None = None
    # Empfehlung der Wallbox-Überschussregelung (None ohne Wallbox/Saldo).
    ev_regelung: EvControlResult | None = None
    # Heizkreis-Empfehlung (None, wenn kein Heizkreis konfiguriert ist).
    heizung: HeatingResult | None = None
    empfehlung: str = "keine Daten"
    prioritaeten: list[str] = field(default_factory=list)
    # Fortgeschriebener Trigger-Zustand für den nächsten Lauf.
    flags: PlanFlags = field(default_factory=PlanFlags)


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
    if known and cap > 0:
        available = sum(s.soc / 100 * s.capacity_kwh for s in known)
        reserve = sum(s.reserve_soc / 100 * s.capacity_kwh for s in inp.storages)
        result.speicher_verfuegbar_kwh = round(available, 2)
        result.speicher_soc = round(available / cap * 100, 1)
        # Voll laden, wenn morgen wenig kommt ODER das Ziel es verlangt:
        # Nulleinspeisung braucht maximale Aufnahmekapazität gegen Export,
        # Vollladen hält das Ziel ohnehin dauerhaft auf 100 %.
        ziel_voll = result.morgen_knapp or inp.goal in (
            GOAL_ZERO_FEEDIN,
            GOAL_FULL_CHARGE,
        )
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

    # Warmwasser-Sperre: Fenster durchreichen und prüfen, ob jetzt gesperrt ist.
    result.ww_sperrfenster = list(inp.thermal_block_windows)
    result.ww_gesperrt = any(
        start <= inp.now < end for start, end in inp.thermal_block_windows
    )

    # Legionellenschutz: wöchentliches Fenster mit erhöhtem Sollwert,
    # unabhängig vom Überschuss (Hygiene geht vor, notfalls aus dem Netz).
    result.ww_legionellen_fenster = list(inp.thermal_legionella_windows)
    result.ww_legionelle_aktiv = any(
        start <= inp.now < end for start, end in inp.thermal_legionella_windows
    )

    # PV-Boost-Kriterien: Speicher fast voll UND kräftige Einspeisung.
    # Ohne konfigurierte Speicher entfällt das SoC-Kriterium.
    if inp.storages:
        result.flags.ww_boost_soc = _latch(
            inp.flags.ww_boost_soc,
            result.speicher_soc,
            on=inp.thermal_boost_soc_on,
            off=inp.thermal_boost_soc_off,
        )
    else:
        result.flags.ww_boost_soc = True
    result.flags.ww_boost_saldo = _latch(
        inp.flags.ww_boost_saldo,
        inp.saldo_w,
        on=inp.thermal_boost_saldo_on_w,
        off=inp.thermal_boost_saldo_off_w,
    )

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
        result.soc_prognose = _soc_forecast(inp, result, available, reserve, cap)

    result.prioritaeten = _priorities(inp, result)
    result.empfehlung = (
        " → ".join(result.prioritaeten) if result.prioritaeten else "Einspeisen"
    )

    # Empfohlener WW-Sollwert nach Priorität: Sperrzeit (aus) >
    # Legionellenschutz > PV-Boost > Basis. Nutzt die in _priorities
    # fortgeschriebenen Temperatur-Latches.
    if not inp.thermal_present:
        pass  # kein Warmwasser-Gerät: ww_soll_c bleibt None, Status leer
    elif result.ww_gesperrt:
        result.ww_soll_c = None
        result.ww_status = "aus"
    elif result.ww_legionelle_aktiv:
        result.ww_soll_c = inp.thermal_legionella_target
        result.ww_status = "legionellenschutz"
    elif (
        result.flags.ww_komfort
        and result.flags.ww_boost_soc
        and result.flags.ww_boost_saldo
    ):
        result.ww_soll_c = inp.thermal_comfort
        result.ww_status = "pv_boost"
    else:
        result.ww_soll_c = inp.thermal_base
        result.ww_status = "basis"
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
        balance_w = _pv_power_at(res.pv_kurve, t) - _total_load_w(inp, t)
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


def _ziel_offset(inp: PlanInput) -> float:
    """Regel-Zieloffset: Eigenverbrauch/Vollladen lassen ein kleines
    Einspeise-Residuum zu (+Offset), Nulleinspeisung hält einen kleinen Bezug
    (−Offset). Gemeinsame Größe für Speicher- und Wallbox-Regelung, damit beide
    denselben Netz-Sollpunkt anstreben."""
    return (
        -CONTROL_ZERO_FEEDIN_OFFSET_W
        if inp.goal == GOAL_ZERO_FEEDIN
        else CONTROL_TARGET_OFFSET_W
    )


def _ampere(watt: float, m: ModulatedState) -> float:
    """Sollleistung → Ladestrom, konservativ abgerundet (nie mehr ziehen als
    der Überschuss hergibt), geklemmt auf [min_a, max_a]."""
    volt = m.phases * EV_VOLTAGE_PER_PHASE_V
    return float(max(m.min_a, min(math.floor(watt / volt), m.max_a)))


def _modulated_control(inp: PlanInput, res: PlanResult) -> EvControlResult | None:
    """Modulierbare Lasten (Wallboxen) am Überschuss VOR dem Akku führen —
    HEMS besitzt den Ladestrom.

    Der Überschuss ergibt sich aus dem Saldo, aus dem die Ist-Last aller Lasten
    und die Akkuleistung herausgerechnet werden. Er wird über alle Lasten
    innerhalb ihres Schwankungsbereichs [min, max] verteilt; sinkt er ins
    Defizit, werden sie heruntergeregelt, bevor der Akku entlädt (modulierbare
    Lasten weichen vor dem Akku).

    Zwei Regime:
    - Überschuss ≥ Summe der Minima: alle laufen, der Rest wird proportional
      zum Schwankungsbereich verteilt (alle anteilig gedrosselt statt eine ganz).
    - Überschuss < Summe der Minima: nicht alle können über ihr Minimum. Dann
      entscheidet Priorität (grob: höhere Priorität zuerst) und darunter
      Energie-Fairness — die heute am wenigsten geladene Last kommt zuerst, mit
      Rotations-Hysterese (eine laufende Last räumt ihren Platz erst, wenn eine
      wartende um mehr als eine Mindestlaufzeit-Ladung zurückliegt) und
      Mindestlaufzeit-Lock gegen Schützflattern.

    Nachfrage-Trennung: Nur Lasten, die real Leistung ziehen (oder heute schon
    geladen haben), konkurrieren um knappe Kapazität. Eine angeschaltete, aber
    autolose Wallbox zieht ~0 und würde sonst als „am wenigsten geladen" jede
    Rotation gewinnen und eine ladende verdrängen.

    Zwangsladung: alle Lasten volle Ampere. Ohne Saldo/Leistungsmessung keine
    Empfehlung (Fail-safe: Lasten unangetastet, externe Automation zuständig).
    """
    loads = inp.modulateds
    if not loads:
        return None

    if inp.ev_force:
        lasten = [
            ModulatedSetpoint(
                name=m.name, id=m.id, laden=True, strom_a=m.max_a,
                soll_w=m.max_w, grund="Zwang",
            )
            for m in loads
        ]
        return EvControlResult(
            lasten=lasten, ueberschuss_w=0.0,
            soll_summe_w=sum(m.max_w for m in loads), zwang=True,
        )

    if inp.saldo_w is None or inp.wallbox_w is None:
        return None

    bat_ist = sum(s.power_w for s in inp.storages if s.power_w is not None)
    mess_summe = sum(m.power_w or 0.0 for m in loads)
    # Überschuss vor dem Akku: Ist-Last aller Wallboxen und Akkuleistung heraus-
    # rechnen. Bewusst ohne Regel-Offset (der gilt nur der Akku-Ruhelage).
    avail_w = -(inp.saldo_w - mess_summe + bat_ist)
    margin = EV_SURPLUS_MARGIN_W

    def _demanding(m: ModulatedState) -> bool:
        """Zieht real Leistung — oder ist frisch an und noch im Anlauf."""
        if m.nachfrage:
            return True
        return (
            m.ist_an
            and m.an_seit_s is not None
            and m.an_seit_s < EV_DEMAND_GRACE_S
        )

    def _locked_on(m: ModulatedState) -> bool:
        """Innerhalb der Mindestlaufzeit an (Taktschutz, darf nicht sofort aus)."""
        return (
            m.ist_an
            and m.an_seit_s is not None
            and m.an_seit_s < m.min_on_min * 60
        )

    def _rotation_credit(m: ModulatedState) -> float:
        """Energie (kWh), die m in einer Mindestlaufzeit bei Mindestleistung
        sammelt — Hysterese, damit eine laufende Last erst weicht, wenn eine
        wartende um mehr als das zurückliegt (kein Ping-Pong bei Gleichstand)."""
        return m.min_w * (m.min_on_min / 60.0) / 1000.0

    # --- Auswahl: welche Lasten laufen (Minima reservieren) -----------------
    # Rangfolge je Prioritätsstufe: wenig Energie zuerst (Fairness), laufende
    # Lasten mit Rotations-Kredit bevorzugt (Hysterese gegen Ping-Pong),
    # beobachtet-leere Lasten mit großem Malus nach hinten (sie weichen jeder
    # real ladenden Last, laufen aber weiter, wenn keine Konkurrenz da ist).
    def _rang(m: ModulatedState) -> float:
        return (
            m.energie_heute_kwh
            - (_rotation_credit(m) if m.ist_an else 0.0)
            + (EV_LEER_PENALTY_KWH if m.leer else 0.0)
        )

    run: list[ModulatedState] = []
    remaining = avail_w
    for prio in sorted({m.priority for m in loads}):
        tier = sorted(
            (m for m in loads if m.priority == prio), key=_rang
        )
        # Mindestlaufzeit-gesperrte Lasten zuerst: sie MÜSSEN laufen (Taktschutz),
        # also reservieren sie ihre Kapazität vor den frei wählbaren. Sonst
        # bekäme eine neu startende Last Kapazität, während die abtretende noch
        # gesperrt-an ist — beide liefen kurz (Akku müsste die Überlappung
        # decken). So wartet die Rotation, bis die alte Last abschaltbereit ist.
        for m in sorted(tier, key=lambda m: (not _locked_on(m), _rang(m))):
            # An, aber leer und Mindestlaufzeit vorbei → abschalten, Slot frei
            # für eine nachfragende Last.
            if m.ist_an and not _locked_on(m) and not _demanding(m):
                continue
            # Schmitt-Band: an-Last hält bis min_w−Marge, aus-Last startet erst
            # ab min_w+Marge. Ein min_on-Lock zwingt ohnehin an (Taktschutz).
            schwelle = m.min_w - margin if m.ist_an else m.min_w + margin
            if _locked_on(m) or remaining >= schwelle:
                run.append(m)
                # Nur reservieren, was real gezogen wird: eine an-gesperrte Last
                # ohne Auto belegt keine Kapazität.
                if _demanding(m) or not m.ist_an:
                    remaining -= m.min_w

    # --- Headroom: Rest proportional zum Schwankungsbereich, Priorität zuerst.
    # Nur nachfragende Lasten bekommen mehr als ihr Minimum; frisch angeschaltete
    # laufen erst am Minimum, bis sie im Folgezyklus Nachfrage nachweisen.
    soll: dict[str, float] = {m.id: m.min_w for m in run}
    for prio in sorted({m.priority for m in run}):
        tier_run = [m for m in run if m.priority == prio and _demanding(m)]
        headroom = sum(m.max_w - m.min_w for m in tier_run)
        if headroom <= 0 or remaining <= 0:
            continue
        geben = min(remaining, headroom)
        for m in tier_run:
            anteil = (m.max_w - m.min_w) / headroom
            soll[m.id] = min(m.max_w, m.min_w + geben * anteil)
        remaining -= geben

    # --- Sollwerte je Last --------------------------------------------------
    # soll_summe_w koppelt an den Speicher-Regler und darf NUR real gezogene
    # Leistung enthalten: eine min_on-gehaltene Leerlast (an, kein Auto) zieht
    # ~0 — würde sie mitgezählt, entlädt der Speicher-Regler den Akku für eine
    # Phantomlast. Gezählt wird also nur, was zieht (oder gerade anläuft).
    lasten = []
    soll_summe = 0.0
    for m in loads:
        if m.id in soll:
            strom_a = _ampere(soll[m.id], m)
            watt = strom_a * m.phases * EV_VOLTAGE_PER_PHASE_V
            zieht = _demanding(m) or not m.ist_an
            lasten.append(
                ModulatedSetpoint(
                    name=m.name, id=m.id, laden=True, strom_a=strom_a,
                    soll_w=watt, grund="läuft" if zieht else "an, kein Auto",
                )
            )
            if zieht:
                soll_summe += watt
        else:
            lasten.append(
                ModulatedSetpoint(
                    name=m.name, id=m.id, laden=False, strom_a=None, soll_w=0.0,
                    grund="Überschuss zu klein",
                )
            )
    return EvControlResult(
        lasten=lasten,
        ueberschuss_w=round(avail_w),
        soll_summe_w=round(soll_summe),
    )


def _storage_control(
    inp: PlanInput, res: PlanResult, ev_target_w: float | None = None
) -> ControlResult | None:
    """Saldo-Regelung: empfohlene Sollwerte je Speicher berechnen.

    Priorität "Bezug minimieren": Der Regler zieht den Netzsaldo auf einen
    leicht in die Einspeisung verschobenen Sollwert. Asymmetrische Gains
    (schnell gegen teuren Bezug, gemächlich beim Laden), Totband gegen
    Dauerkorrekturen. Entladen verteilt proportional zur verfügbaren Energie
    oberhalb der Reserve; Kaltreserve-Speicher nehmen daran erst teil, wenn
    der mittlere SoC der übrigen unter die Schwelle fällt (Hysterese).
    Geladen wird proportional zur freien Kapazität — über alle Speicher,
    Reserve eingeschlossen. Speicher ohne SoC-Wert werden aus der Zuteilung
    genommen (kein Phantomanteil).
    """
    if inp.saldo_w is None or not inp.storages:
        return None
    known = [s for s in inp.storages if s.soc is not None]
    if not known:
        return None

    # Kaltreserve-Hysterese über den mittleren SoC der Nicht-Reserve-Speicher.
    primary_socs = [s.soc for s in known if not s.cold_reserve]
    res.flags.kaltreserve = _latch(
        inp.flags.kaltreserve,
        sum(primary_socs) / len(primary_socs) if primary_socs else None,
        on=RESERVE_SOC_ON,
        off=RESERVE_SOC_OFF,
    )
    reserve_aktiv = res.flags.kaltreserve

    bat_ist = sum(s.power_w for s in inp.storages if s.power_w is not None)
    # E-Auto-Zwangsladung: die Wallbox-Last nicht ausregeln, sonst entlädt der
    # Regler den Hausakku, um den Netzbezug der Wallbox zu decken. Der
    # herausgerechnete Saldo lässt den Akku seinen SoC halten; das Zwangs-Delta
    # bleibt beim Netz.
    saldo_w = inp.saldo_w
    if inp.ev_force and inp.wallbox_w:
        saldo_w = inp.saldo_w - inp.wallbox_w
    elif ev_target_w is not None and inp.wallbox_w is not None:
        # Überschussregelung: HEMS stellt die Wallbox gleich auf ev_target_w.
        # Der Regler soll den Saldo sehen, der sich mit diesem NEUEN Sollwert
        # ergibt (Ist-Last + Delta), sonst hielte er die Akku-Entladung für die
        # bereits gedrosselte Wallbox aufrecht. Am Nullpunkt (Wallbox schon auf
        # Soll) verschwindet das Delta — der Regler sieht wieder den Rohsaldo.
        saldo_w = inp.saldo_w + (ev_target_w - inp.wallbox_w)
    # Sollwert-Offset: Eigenverbrauch/Vollladen schieben das Regel-Residuum
    # leicht in die Einspeisung (+25 W). Echte Nulleinspeisung hält stattdessen
    # einen kleinen Bezug (−100 W) — deutlich über Totband, damit das Ziel
    # wirklich anders regelt: gegen Export laden, kleinen Restbezug tolerieren.
    offset = _ziel_offset(inp)
    fehler = saldo_w + offset
    gain = CONTROL_GAIN_DISCHARGE if fehler > 0 else CONTROL_GAIN_CHARGE
    max_ent = sum(s.max_discharge_w for s in known)
    max_lad = sum(s.max_charge_w for s in known)
    soll = max(-max_lad, min(bat_ist + fehler * gain, max_ent))

    ctrl = ControlResult(
        modus="pausiert",
        fehler_w=round(fehler, 0),
        soll_w=round(soll, 0),
        reserve_aktiv=reserve_aktiv,
        reserve_namen=[s.name for s in inp.storages if s.cold_reserve],
    )

    def _verteile(
        anteile: list[tuple[StorageState, float]], gesamt: float, laden: bool
    ) -> list[StorageSetpoint]:
        summe = sum(a for _s, a in anteile)
        setpoints = []
        for s, anteil in anteile:
            grenze = s.max_charge_w if laden else s.max_discharge_w
            watt = min(gesamt * anteil / summe, grenze) if summe > 0 else 0.0
            if watt < CONTROL_MIN_SETPOINT_W:
                watt = 0.0
            setpoints.append(StorageSetpoint(name=s.name, watt=round(watt)))
        return setpoints

    if soll > CONTROL_DEADBAND_W:
        ctrl.modus = "entladen"
        # Verfügbare Energie oberhalb der Reserve, Kaltreserve nur bei Bedarf.
        anteile = [
            (
                s,
                max(0.0, (s.soc - s.reserve_soc) / 100 * s.capacity_kwh)
                if (not s.cold_reserve or reserve_aktiv)
                else 0.0,
            )
            for s in known
        ]
        ctrl.zuteilung = _verteile(anteile, soll, laden=False)
    elif soll < -CONTROL_DEADBAND_W:
        ctrl.modus = "laden"
        # Freie Kapazität bis 100 % — wer mehr Platz hat, bekommt mehr.
        anteile = [
            (s, max(0.0, (100 - s.soc) / 100 * s.capacity_kwh)) for s in known
        ]
        ctrl.zuteilung = _verteile(anteile, -soll, laden=True)
    else:
        ctrl.zuteilung = [StorageSetpoint(name=s.name, watt=0.0) for s in known]
    return ctrl


def _heating_plan(inp: PlanInput, res: PlanResult) -> HeatingResult:
    """Heizkreis: Modus über Außentemperatur-Hysterese, Vorlauf aus der Kurve.

    Heizen unterliegt der Sommersperre; Kühlen greift oberhalb der eigenen
    Schwellen. Im Heizbetrieb hebt die Wärmeanforderung der Räume die
    witterungsgeführte Kurve an; ohne Anforderung fällt der Vorlauf auf das
    Minimum (Absenkbetrieb). Der Vorlauf bleibt zwischen Minimum und Maximum.
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

    if res.flags.wp_heizen:
        result.modus = "heizen"
        vlt_min = (
            h.vlt_min_cold_c if t < HEATING_COLD_THRESHOLD_C else h.vlt_min_c
        )
        if h.demand_pct is not None and h.demand_pct < 1:
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


def _priorities(inp: PlanInput, res: PlanResult) -> list[str]:
    """Dynamische Reihenfolge für den Überschuss. WW ist Priorität 1, sofern
    keine Sperrzeit läuft; in der Sperrzeit entfallen Basis- und Komfort-
    ladung, der Speicher darf also unter die Basistemperatur auskühlen."""
    prio: list[str] = []

    # Thermostat-Logik: Bedarf wird erst gemeldet, wenn die Temperatur das
    # Totband unter dem Sollwert durchschritten hat, und erst beim Erreichen
    # des Sollwerts wieder fallengelassen.
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
