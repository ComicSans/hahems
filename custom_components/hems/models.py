"""Geräte-agnostisches Rollenmodell.

Der Planner arbeitet ausschließlich gegen diese Rollen; welche realen Geräte
dahinterstehen, entscheidet die Konfiguration (Options-Flow).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .const import (
    DEFAULT_ANTITAKT_PAUSE_MIN,
    DEFAULT_ANTITAKT_STARTS,
    DEFAULT_ANTITAKT_WINDOW_MIN,
    DEFAULT_BASE_TARGET,
    DEFAULT_BOOST_SALDO_OFF_W,
    DEFAULT_BOOST_SALDO_ON_W,
    DEFAULT_BOOST_SOC_OFF,
    DEFAULT_BOOST_SOC_ON,
    DEFAULT_COMFORT_TARGET,
    DEFAULT_COOL_OFF_C,
    DEFAULT_COOL_ON_C,
    DEFAULT_COOL_VLT_C,
    DEFAULT_CURVE_BASE_C,
    DEFAULT_CURVE_SLOPE,
    DEFAULT_DEWPOINT_MARGIN_K,
    DEFAULT_HEAT_FROST_OFF_C,
    DEFAULT_HEAT_FROST_ON_C,
    DEFAULT_HEAT_LOCK_FROM,
    DEFAULT_HEAT_LOCK_TO,
    DEFAULT_HEAT_OFF_C,
    DEFAULT_HEAT_ON_C,
    DEFAULT_LEGIONELLA_TARGET,
    DEFAULT_MAX_CHARGE_W,
    DEFAULT_MAX_DISCHARGE_W,
    DEFAULT_RESERVE_SOC,
    DEFAULT_VLT_MAX_C,
    DEFAULT_VLT_MIN_C,
    DEFAULT_VLT_MIN_COLD_C,
    ROLE_ANALYSIS,
    ROLE_FORECAST,
    ROLE_HEATING,
    ROLE_MODULATED,
    ROLE_STORAGE,
    ROLE_SWITCHABLE,
    ROLE_THERMAL,
)


@dataclass
class ForecastSource:
    id: str
    name: str
    energy_today: str
    energy_remaining: str
    energy_tomorrow: str


@dataclass
class Storage:
    id: str
    name: str
    soc_entity: str
    capacity_kwh: float
    reserve_soc: float = DEFAULT_RESERVE_SOC
    max_charge_w: float = DEFAULT_MAX_CHARGE_W
    max_discharge_w: float = DEFAULT_MAX_DISCHARGE_W
    power_entity: str | None = None
    # Stellgrößen: aktuelle Lade-/Entladeleistung in W setzen (z. B. Zendure
    # Input/Output-Limit). Ohne diese Entitäten wird der Speicher nur beobachtet.
    charge_setpoint_entity: str | None = None
    discharge_setpoint_entity: str | None = None
    # Optionaler Richtungs-Select (z. B. Zendure ac_mode): wird beim Laden auf
    # mode_charge_option, beim Entladen auf mode_discharge_option gestellt.
    # Ohne diese drei Felder werden nur die Leistungslimits geschrieben.
    mode_entity: str | None = None
    mode_charge_option: str | None = None
    mode_discharge_option: str | None = None
    # Optionaler geräteseitiger Ziel-SoC (z. B. Zendure soc_set): wird jeden
    # Zyklus auf den Ladedeckel gesetzt. Nötig für Geräte, die im Lademodus
    # nach ihrem EIGENEN Ziel-SoC laden und den Leistungs-Setpoint (charge)
    # dabei ignorieren — dann kappt erst der Ziel-SoC das Laden am Deckel.
    soc_set_entity: str | None = None
    # Kaltreserve: nimmt am Entladen erst teil, wenn der mittlere SoC der
    # übrigen Speicher die Reserve-Schwelle unterschreitet (mit Hysterese).
    # Geladen wird sie immer mit, proportional zur freien Kapazität.
    cold_reserve: bool = False


@dataclass
class ThermalStore:
    id: str
    name: str
    temp_entity: str | None = None
    # Steuer-Entity für On/Off im Auto-Modus. Zwei Geräteformen:
    #  • water_heater: trägt On/Off UND den Sollwert selbst (set_temperature).
    #  • switch/input_boolean: schaltet nur ein/aus; der Sollwert läuft dann
    #    über setpoint_entity (z. B. Modbus-Wärmepumpe, die Freigabe-Schalter +
    #    Soll-Temperatur-Number getrennt anbietet).
    # Ohne dieses Entity wird die WW-Empfehlung nur angezeigt, nicht gestellt.
    control_entity: str | None = None
    # Separate Sollwert-Number (nur nötig, wenn control_entity ein Schalter ist).
    # Bei einem water_heater bleibt es leer — der trägt den Sollwert selbst.
    setpoint_entity: str | None = None
    base_target: float = DEFAULT_BASE_TARGET
    comfort_target: float = DEFAULT_COMFORT_TARGET
    # Sperrzeit als lokale Uhrzeiten "HH:MM:SS". In diesem Fenster wird weder
    # Basis- noch Komfortladung empfohlen; block_end < block_start bedeutet ein
    # Fenster über Mitternacht (z. B. 18:00 → 06:00). Gleiche Zeiten = keine
    # Sperre.
    block_start: str | None = None
    block_end: str | None = None
    # Legionellenschutz: wöchentliches Fenster (Wochentag + lokale Uhrzeiten),
    # in dem der Sollwert unabhängig vom Überschuss auf legionella_target
    # angehoben wird — notfalls aus dem Netz. "none"/leer = deaktiviert.
    legionella_weekday: str | int | None = None  # 0 = Montag … 6 = Sonntag
    legionella_start: str | None = None
    legionella_end: str | None = None
    legionella_target: float = DEFAULT_LEGIONELLA_TARGET
    # PV-Boost auf den Komfort-Sollwert nur, wenn der Speicher fast voll ist
    # UND kräftig eingespeist wird. Jeweils Ein-/Aus-Schwelle (Hysterese).
    boost_soc_on: float = DEFAULT_BOOST_SOC_ON
    boost_soc_off: float = DEFAULT_BOOST_SOC_OFF
    boost_saldo_on_w: float = DEFAULT_BOOST_SALDO_ON_W
    boost_saldo_off_w: float = DEFAULT_BOOST_SALDO_OFF_W


@dataclass
class HeatingCircuit:
    """Witterungsgeführter Heizkreis (z. B. Wärmepumpe): Modus-Empfehlung
    (heizen/kühlen/aus) über Außentemperatur-Schwellen mit Hysterese plus
    Vorlauf-Sollwert aus der Heizkurve."""

    id: str
    name: str
    outdoor_temp_entity: str
    # Wärmeanforderung der Räume in % (0–100), z. B. aus einem PID-Thermostat
    # oder einem Template-Sensor; hebt die Vorlaufkurve an. Ohne Anforderung
    # (< 1 %) fällt der Vorlauf auf das Minimum (Absenkbetrieb).
    demand_entity: str | None = None
    # Steuer-Entities für den Auto-Modus (alle optional): Ohne control_entity
    # nur Anzeige. Zwei Geräteformen für control_entity:
    #  • climate: trägt Modus (set_hvac_mode) UND Vorlauf-Soll (set_temperature).
    #  • select/input_select: trägt nur den Modus; der Vorlauf-Soll läuft dann
    #    über setpoint_entity (z. B. Modbus-Wärmepumpe mit Betriebsmodus-Register
    #    und getrennter Vorlauf-Soll-Number).
    control_entity: str | None = None
    # Vorlauf-Sollwert-Number (nur bei Select-Steuerung; ein climate trägt den
    # Sollwert selbst).
    setpoint_entity: str | None = None
    # Klartext-Optionen des Modus-Select, die HEMS für heizen/kühlen/aus schreibt
    # (analog zu Storage.mode_charge_option). Nur nötig, wenn control_entity ein
    # Select ist; bei einem climate ungenutzt. mode_cool_option darf bei
    # reinen Heizgeräten leer bleiben.
    mode_heat_option: str | None = None
    mode_cool_option: str | None = None
    mode_off_option: str | None = None
    silent_switch_entity: str | None = None
    season_select_entity: str | None = None
    # Optionale Störungsquelle für Betriebsalarme (Push/Notification/Repair):
    # ein binary_sensor (on = Störung) oder ein sensor, dessen Rohwert ≠ „ok"
    # als Fehlercode gilt. Rein informativ — steuert nichts, wird nur überwacht.
    fault_entity: str | None = None
    # Optionale Rückmeldung „die Anlage bereitet gerade Warmwasser“ (on = Ladung
    # läuft): binary_sensor/switch/input_boolean. Solange sie an ist, lässt HEMS
    # den Heizkreis in Ruhe: Viele Wärmepumpen heben den Vorlauf-Sollwert
    # während der Speicherladung selbst an und schreiben gegen jeden Wert an,
    # den HEMS setzt (Schreib-Pingpong). Bewusst NICHT aus dem Betriebsmodus
    # oder der eigenen WW-Empfehlung abgeleitet: die Anlage entscheidet
    # autonom, wann sie lädt, auch im Heiz- oder Kühlbetrieb. Ohne dieses
    # Entity bleibt das Verhalten unverändert.
    dhw_active_entity: str | None = None
    # Optionale Rückmeldung „der Verdichter läuft" (on = läuft): binary_sensor/
    # switch/input_boolean. Einzige Quelle des Taktschutzes — ohne dieses Entity
    # zählt HEMS keine Starts und pausiert nie. Bewusst nicht aus der
    # Leistungsmessung abgeleitet: die Umwälzpumpe läuft auch ohne Verdichter,
    # und die Schwelle dazwischen ist anlagenabhängig.
    compressor_entity: str | None = None
    # Raumklima für die Taupunkt-Untergrenze im Kühlbetrieb (beide optional,
    # aber nur zusammen wirksam — eine Taupunktrechnung braucht Temperatur UND
    # relative Feuchte). Ohne sie fährt der Kühl-Vorlauf auf cool_vlt_c, auch
    # wenn der unter dem Taupunkt liegt; an einer Flächenkühlung schlägt sich
    # dann Wasser nieder.
    room_temp_entity: str | None = None
    room_humidity_entity: str | None = None
    # Sicherheitsabstand des Vorlaufs zum Taupunkt. Die Vorlauftemperatur ist
    # nicht die Oberflächentemperatur — der Aufbau puffert, die Oberfläche
    # bleibt wärmer als das Wasser.
    dewpoint_margin_k: float = DEFAULT_DEWPOINT_MARGIN_K
    # Taktschutz (nur Kühlbetrieb): ab wie vielen Starts im Fenster HEMS eine
    # Zwangspause einlegt und wie lange sie dauert. starts = 0 schaltet ihn ab.
    antitakt_starts: int = DEFAULT_ANTITAKT_STARTS
    antitakt_window_min: int = DEFAULT_ANTITAKT_WINDOW_MIN
    antitakt_pause_min: int = DEFAULT_ANTITAKT_PAUSE_MIN
    heat_on_c: float = DEFAULT_HEAT_ON_C
    heat_off_c: float = DEFAULT_HEAT_OFF_C
    cool_on_c: float = DEFAULT_COOL_ON_C
    cool_off_c: float = DEFAULT_COOL_OFF_C
    # Frostschutz-Schwellen (mit Hysterese): erzwingen Heizen bei tiefer
    # Außentemperatur, auch während der Sommersperre.
    frost_on_c: float = DEFAULT_HEAT_FROST_ON_C
    frost_off_c: float = DEFAULT_HEAT_FROST_OFF_C
    # Sommersperre: in diesen Monaten (einschließlich) wird Heizen nur noch vom
    # Frostschutz erzwungen, sonst nie empfohlen.
    heat_lock_from_month: int = DEFAULT_HEAT_LOCK_FROM
    heat_lock_to_month: int = DEFAULT_HEAT_LOCK_TO
    curve_base_c: float = DEFAULT_CURVE_BASE_C
    curve_slope: float = DEFAULT_CURVE_SLOPE
    # Heizkurve aus der Rolle Wärmepumpen-Analyse übernehmen, statt
    # curve_base_c und curve_slope zu verwenden. Voreingestellt aus.
    #
    # Die Empfehlung entsteht aus Betrieb, den HEMS mit der vorigen Empfehlung
    # selbst erzeugt hat. Wer das einschaltet, schließt eine Rückkopplung —
    # gedämpft durch belastbare Datenbasis, Tagesabstand und eine
    # Mindeständerung, siehe strategies/kurve.py. Die konfigurierten Werte
    # bleiben stehen und gelten wieder, sobald der Schalter aus ist.
    curve_from_analysis: bool = False
    vlt_min_c: float = DEFAULT_VLT_MIN_C
    vlt_min_cold_c: float = DEFAULT_VLT_MIN_COLD_C
    vlt_max_c: float = DEFAULT_VLT_MAX_C
    cool_vlt_c: float = DEFAULT_COOL_VLT_C


@dataclass
class SwitchableLoad:
    id: str
    name: str
    switch_entity: str
    power_entity: str | None = None
    min_on_min: int = 20
    min_off_min: int = 10
    max_block_min: int = 120
    priority: int = 1
    # Heizungsgekoppelt: die Last folgt der Außentemperatur (Wärmepumpe,
    # Heizstab) und wird deshalb im Heizgradstunden-Modell mitgelernt und aus
    # dem Lastprofil herausgerechnet. Nur solche Lasten dürfen dort einfließen —
    # eine überschussgesteuerte Last (Pool, Entfeuchter) hat keinen
    # Temperaturbezug und würde die Regression verzerren.
    heat_coupled: bool = False


@dataclass
class ModulatedLoad:
    id: str
    name: str
    current_entity: str
    switch_entity: str | None = None
    power_entity: str | None = None
    min_a: float = 6
    max_a: float = 16
    phases: int = 3
    min_on_min: int = 10
    min_off_min: int = 10
    priority: int = 1


@dataclass
class HeatPumpAnalysis:
    """Effizienzmessung einer Wärmepumpe. Beratend, ohne Steuer-Entity.

    Fünf Messeingänge sind Pflicht, zwei verbessern das Ergebnis. Die Rolle
    kennt weder Protokolle noch Register: woher die Werte kommen — Modbus,
    Herstellerintegration, eigene Zähler — ist ihr gleich. Herstellerwissen
    steckt allein im Preset, und das ist eine JSON-Datei.

    Bewusst ohne Steuer-Entity: Die Empfehlungen werden veröffentlicht,
    gestellt wird über die Rolle Heizkreis. Zwei Stellen, die denselben
    Sollwert schreiben, sind der Fehler, den diese Trennung verhindert.
    """

    id: str
    name: str
    # Schlüssel einer Datei in `waermepumpe/presets/`. Modellscharf, nicht
    # markenscharf: allein die Therma-V-Reihe hat vier Kennlinien.
    preset: str
    vorlauf_temp: str
    ruecklauf_temp: str
    leistung_elektrisch: str
    aussentemperatur: str
    # Ohne Volumenstromzähler tritt der Nennvolumenstrom des Presets ein. Der
    # COP ist dann geschätzt statt gemessen, `durchfluss_geschaetzt` steht an
    # und die Datenbasis wird gedeckelt. Das ist keine Notlösung, sondern der
    # Normalfall: an vielen Anlagen ist der Volumenstrom nicht auslesbar.
    durchfluss: str | None = None
    # Ohne Verdichterfrequenz wird die Taktung aus der Leistung geschätzt.
    verdichter_frequenz: str | None = None
    # Ohne Betriebsart vermischen sich Heizen und Warmwasser in einer
    # Kennzahl. Zulässig, wertet aber die Datenbasis ab.
    betriebsart: str | None = None
    # Anlagenspezifischer Standby-Sockel; 0 heißt: Wert aus dem Preset. Der
    # Sockel hängt an der Umwälzpumpe der Anlage, nicht am Gerätemodell.
    standby_w: float = 0.0
    # Nennvolumenstrom in l/h, wenn kein Zähler verdrahtet ist; 0 heißt: Wert
    # aus dem Preset. Wie der Standby-Sockel eine Eigenschaft der Anlage —
    # Umwälzpumpe und Hydraulik — und nicht des Gerätemodells. Die sechs
    # generischen Presets führen ihn deshalb gar nicht, und ohne diesen Wert
    # verwirft die Analyse dort jede Messung mit `kein_durchfluss`: ohne
    # Volumenstrom keine thermische Leistung und damit kein COP.
    durchfluss_nominal_lh: float = 0.0


@dataclass
class DeviceRegistry:
    forecasts: list[ForecastSource] = field(default_factory=list)
    storages: list[Storage] = field(default_factory=list)
    thermals: list[ThermalStore] = field(default_factory=list)
    heatings: list[HeatingCircuit] = field(default_factory=list)
    switchables: list[SwitchableLoad] = field(default_factory=list)
    modulateds: list[ModulatedLoad] = field(default_factory=list)
    analyses: list[HeatPumpAnalysis] = field(default_factory=list)


_ROLE_CLASSES = {
    ROLE_FORECAST: (ForecastSource, "forecasts"),
    ROLE_STORAGE: (Storage, "storages"),
    ROLE_THERMAL: (ThermalStore, "thermals"),
    ROLE_HEATING: (HeatingCircuit, "heatings"),
    ROLE_SWITCHABLE: (SwitchableLoad, "switchables"),
    ROLE_MODULATED: (ModulatedLoad, "modulateds"),
    ROLE_ANALYSIS: (HeatPumpAnalysis, "analyses"),
}


def parse_devices(raw: list[dict]) -> DeviceRegistry:
    """Options-Liste (dicts mit 'role') in das Rollenmodell übersetzen."""
    registry = DeviceRegistry()
    for item in raw:
        role = item.get("role")
        if role not in _ROLE_CLASSES:
            continue
        cls, attr = _ROLE_CLASSES[role]
        fields = {k: v for k, v in item.items() if k in cls.__dataclass_fields__}
        getattr(registry, attr).append(cls(**fields))
    # Lasten in Nutzer-Priorität (1 = höchste), damit Konsumenten sie der
    # Reihe nach abarbeiten können.
    registry.switchables.sort(key=lambda d: d.priority)
    registry.modulateds.sort(key=lambda d: d.priority)
    return registry
