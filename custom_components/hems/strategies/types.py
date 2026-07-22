"""Laufzeit-Datentypen des Planners (HA-frei) plus der Schmitt-Trigger `_latch`.

Gemeinsame Heimat aller Domänen-Strategien: importiert nur aus `..const` und der
Standardbibliothek, nie aus anderen Strategie-Modulen (kein Zirkularimport).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, tzinfo

from ..const import (
    DEFAULT_BOOST_SALDO_OFF_W,
    DEFAULT_BOOST_SALDO_ON_W,
    DEFAULT_BOOST_SOC_OFF,
    DEFAULT_BOOST_SOC_ON,
    DEFAULT_GAIN_LEVEL,
    DEFAULT_LEGIONELLA_TARGET,
    EV_VOLTAGE_PER_PHASE_V,
    GOAL_SELF_CONSUMPTION,
    PRIORITY_AUTO,
)


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
    frost_on_c: float  # Frostschutz greift ab dieser Außentemperatur (mit Hysterese)
    frost_off_c: float  # ... und lässt erst darüber wieder los
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
    min_off_min: int = 10
    hat_schalter: bool = True
    power_w: float | None = None
    energie_heute_kwh: float = 0.0
    ist_an: bool = False
    an_seit_s: float | None = None
    aus_seit_s: float | None = None
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
class SwitchableState:
    """Zustand einer schaltbaren Last (nur an/aus) für die Überschussregelung.

    `erwartet_w` ist die vom Coordinator gelernte Leistungsaufnahme im An-Zustand
    (letzter gemessener Bezug); solange die Last noch nie lief, greift ein
    konservativer Fallback. `an_seit_s`/`aus_seit_s` liefern die Mindestlauf-/
    Mindestpausenzeit-Sperren gegen Schützflattern, `max_block_min` erzwingt ein
    Einschalten, wenn HEMS die Last zu lange ausgehalten hat. `priority` (klein =
    wichtiger) entscheidet, welche Last bei knappem Überschuss zuerst weicht.
    """

    name: str
    id: str = ""
    priority: int = 1
    power_w: float | None = None       # gemessener Bezug (An-Zustand)
    erwartet_w: float | None = None    # gelernte Leistung im An-Zustand
    ist_an: bool = False
    an_seit_s: float | None = None
    aus_seit_s: float | None = None
    min_on_min: int = 20
    min_off_min: int = 10
    max_block_min: int = 120


@dataclass
class SwitchableSetpoint:
    """Empfehlung für eine einzelne schaltbare Last (an/aus)."""

    name: str
    an: bool
    id: str = ""
    grund: str = ""  # Kurzbegründung (Transparenz)


@dataclass
class SwitchableResult:
    """Empfehlung der Schaltlast-Regelung über alle schaltbaren Lasten.

    Schaltbare Lasten bekommen Überschuss VOR dem Headroom der modulierbaren
    Lasten: reicht er nicht, drosseln die modulierbaren Lasten herunter (geben
    Überschuss frei), bevor eine schaltbare Last abgeschaltet wird. `soll_w` ist
    die erwartete Leistung aller empfohlen-eingeschalteten Lasten, `delta_w` die
    Differenz zur aktuell gemessenen Schaltlast (neue/wegfallende Last), mit der
    der modulierbare Regler seinen Überschuss bereinigt.
    """

    lasten: list[SwitchableSetpoint]
    soll_w: float = 0.0
    delta_w: float = 0.0


@dataclass
class HeatingResult:
    """Empfehlung für den Heizkreis: Modus plus Vorlauf-Sollwert."""

    name: str
    modus: str = "unbekannt"  # "heizen" | "kuehlen" | "aus" | "unbekannt"
    vlt_ziel_c: float | None = None
    t_aussen_c: float | None = None
    sommer_sperre: bool = False
    leise_empfohlen: bool | None = None  # Flüsterbetrieb reicht (niedriger Vorlauf)
    frostschutz: bool = False  # Heizen nur wegen Frostschutz erzwungen (Sperre übersteuert)


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
    # Frostschutz-Trigger: eigener Latch, unabhängig vom Heiz-/Sperr-Zustand.
    wp_frost: bool = False
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
    # Regel-Aggressivität (Laufzeit, min/normal/max): skaliert die Regler-Gains,
    # damit Ladelücken schneller geschlossen werden. Beeinflusst nur die
    # Schrittweite pro Zyklus, nicht die Umschaltrate (bleibt 1×/min).
    gain_level: str = DEFAULT_GAIN_LEVEL
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
    # Schaltbare Lasten (nur an/aus) für die Überschussregelung. Leer = keine
    # konfiguriert; dann bleibt die Schaltlast-Empfehlung leer.
    switchables: list[SwitchableState] = field(default_factory=list)
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
    # Ladedeckel jetzt (Akku-Schonung): SoC-Obergrenze, bis zu der die
    # Saldo-Regelung gerade laden darf. Tagsüber < 100 %, zum Abend hin per
    # Rampe auf 100 % — außer Nachtdeckung geht vor Schonung (dann 100 %).
    lade_deckel_soc: float | None = None
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
    schaltbare: SwitchableResult | None = None
    # Heizkreis-Empfehlung (None, wenn kein Heizkreis konfiguriert ist).
    heizung: HeatingResult | None = None
    empfehlung: str = "keine Daten"
    prioritaeten: list[str] = field(default_factory=list)
    # Fortgeschriebener Trigger-Zustand für den nächsten Lauf.
    flags: PlanFlags = field(default_factory=PlanFlags)
