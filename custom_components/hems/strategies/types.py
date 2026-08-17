"""Laufzeit-Datentypen des Planners (HA-frei) plus der Schmitt-Trigger `_latch`.

Gemeinsame Heimat aller Domänen-Strategien: importiert nur aus `..const` und der
Standardbibliothek, nie aus anderen Strategie-Modulen (kein Zirkularimport).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..const import (
    DEFAULT_BOOST_MIN_HOLD_MIN,
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
    # Der Speicher ist ausgefallen: Seine SoC-Entität schweigt seit
    # STORAGE_STALE_MIN Minuten UND er hat einen Befehl ≠ 0 nicht ausgeführt.
    # Setzt der Coordinator (`_stumm`; die Frist braucht Zeitstempel aus HA),
    # auswerten muss es die Regelung: `soc` und `power_w` sind dann Fiktion,
    # und wer einer Fiktion Leistung zuteilt, hält den Rest der Anlage still.
    # Anders als `soc = None` (Wert nie lesbar) ist das ein Wert, der bloß
    # nicht mehr stimmt — deshalb ein eigenes Feld statt eines gelöschten SoC.
    # Schweigen allein reicht ausdrücklich NICHT: Ein ruhender Speicher an
    # einer push-basierten Integration schweigt, ohne ausgefallen zu sein.
    stale: bool = False


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
    # Speicher, die nicht mehr melden und deshalb aus der Zuteilung genommen
    # wurden (siehe StorageState.stale). Steht hier ein Name, regelt HEMS
    # bewusst ohne diesen Speicher — das gehört sichtbar gemacht, sonst ist
    # eine halbierte Anlage von einer vollständigen nicht zu unterscheiden.
    abgemeldet_namen: list[str] = field(default_factory=list)
    # „Laden statt einspeisen": alle Speicher stehen am Ladedeckel, es bliebe
    # aber Überschuss übrig, den auch die Lasten nicht nehmen. Dann wird über
    # den Deckel hinaus geladen — Einspeisen ist die schlechtere Verwendung.
    laden_statt_einspeisen: bool = False


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
    die Sofortladung: sie garantiert, DASS jede Last läuft (an Auswahl, Rotation
    und Mindestpause vorbei), nicht wie schnell — der Sollwert folgt weiter dem
    Überschuss und sinkt bei Defizit auf die Untergrenze.
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
    # Vorgaben einer übergeordneten Domäne, die vor jeder Überschuss- und
    # Anti-Takt-Entscheidung stehen. Heute setzt sie nur die Heizung
    # (`strategies/heating.py`), und zwar in dieser Rangfolge:
    #   zwang_an       – Frostschutz: einschalten, notfalls aus dem Netz.
    #   zwang_aus      – Sommersperre: nicht heizen.
    #   nicht_abschalten – HEMS ist blind (keine Außentemperatur) und lässt
    #                    eine laufende Anlage in Ruhe, statt sie abzuschalten.
    # Eine Schaltlast ohne Heizungsrolle lässt alle drei auf ihrem Default und
    # verhält sich damit exakt wie bisher.
    zwang_an: bool = False
    zwang_aus: bool = False
    nicht_abschalten: bool = False
    grund_vorgabe: str = ""


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
    Leistung, die in diesem Zyklus dazukommt oder wegfällt — die Vorsteuerung
    für den modulierbaren und den Speicher-Regler. Nur Lagewechsel zählen: eine
    Last, die ihre Lage behält, steht bereits im gemessenen Saldo, und ihre
    Erwartung dort noch einmal gegenzurechnen verschöbe den Regel-Sollpunkt
    dauerhaft.
    """

    lasten: list[SwitchableSetpoint]
    soll_w: float = 0.0
    delta_w: float = 0.0


@dataclass
class HeatingState:
    """Zustand eines Wärmeerzeugers für die Witterungsführung.

    Die Schaltlage (`ist_an`, Mindestzeiten, gelernte Leistung) läuft NICHT
    hier, sondern über den `SwitchableState` derselben `id` — die Heizung
    konkurriert im selben Überschuss-Budget wie jede andere schaltbare Last.
    Dieser Zustand trägt nur, was eine Steckdosenlast nicht hat: die
    Außentemperatur und die Parameter von Frostschutz, Heizgrenze, Kurve und
    Sommersperre.

    `outdoor_temp_c` ist `None`, wenn weder ein eigener Temperatursensor noch
    die Wetter-Entität einen Wert liefert. Dann kann HEMS weder Frost erkennen
    noch die Kurve rechnen — siehe `heating_control`.

    `betriebsart` sagt, was die Anlage laut ihrem Zustand gerade tut —
    `heizen`, `kuehlen` oder `fremd` (Modus nicht zugeordnet, typisch
    `heat_cool`/`auto`). Die Zuordnung Modus → Betriebsart trifft
    `entity_domain`; hier steht nur ihr Ergebnis, damit dieses Modul HA-frei
    bleibt. Alles, was diese Klasse sonst trägt, ist Heizungs-Semantik und gilt
    ausschließlich für `heizen`.
    """

    name: str
    id: str = ""
    outdoor_temp_c: float | None = None
    month: int = 1
    hat_vorlauf_entity: bool = False
    betriebsart: str = "heizen"
    frost_on_c: float = 3.0
    frost_off_c: float = 5.0
    heat_on_c: float = 15.0
    heat_off_c: float = 18.0
    curve_base_c: float = 32.0
    curve_slope: float = 0.6
    vlt_min_c: float = 25.0
    vlt_max_c: float = 45.0
    heat_lock_from_month: int = 0
    heat_lock_to_month: int = 0


@dataclass
class HeatingSetpoint:
    """Empfehlung für einen Wärmeerzeuger.

    `zwang_an` ist der Frostschutz und der einzige Teil dieser Empfehlung, der
    ohne Netzsaldo entsteht: er hängt allein an der Temperatur. Der Actuator
    wertet ihn deshalb auch dann aus, wenn gar kein Überschussplan vorliegt
    (Zähler unerreichbar) — sonst bliebe eine abgeschaltete Heizung genau in
    der Störung aus, in der niemand hinschaut.
    """

    name: str
    id: str = ""
    zwang_an: bool = False
    sperre: bool = False
    nicht_abschalten: bool = False
    # Betriebsart, aus der die Empfehlung entstanden ist. Der Actuator schaltet
    # damit in denselben Modus zurück, aus dem er abgeschaltet hat — sonst käme
    # eine im Kühlbetrieb abgeschaltete Anlage als Heizung wieder hoch.
    betriebsart: str = "heizen"
    # Witterungsgeführter Vorlauf-Sollwert (°C); None heißt „nicht schreiben".
    vorlauf_c: float | None = None
    t_aussen_c: float | None = None
    # "frostschutz" | "heizen" | "sommersperre" | "heizgrenze" | "unbekannt"
    # | "kuehlen" | "fremdmodus"
    status: str = ""
    grund: str = ""


@dataclass
class HeatingResult:
    """Empfehlung über alle Wärmeerzeuger."""

    anlagen: list[HeatingSetpoint] = field(default_factory=list)

    def by_id(self, geraete_id: str) -> HeatingSetpoint | None:
        return next((a for a in self.anlagen if a.id == geraete_id), None)


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
    warmwasser_basis: bool = False
    warmwasser_komfort: bool = False
    wetter_knapp: bool = False
    pv_morgen_knapp: bool = False
    # PV-Boost-Kriterien fürs Warmwasser: Speicher fast voll bzw. kräftige
    # Einspeisung, jeweils mit eigener Ein-/Aus-Schwelle.
    warmwasser_boost_soc: bool = False
    warmwasser_boost_saldo: bool = False
    # Der gehaltene Boost-Zustand samt Zeitpunkt seines letzten Wechsels — die
    # Kriterien oben dürfen kippen, der Boost folgt ihnen erst nach Ablauf des
    # Mindestabstands. `None` heißt „noch nie gewechselt" und gibt den nächsten
    # Wechsel frei; sonst wäre der Boost nach jedem Neustart eine Stunde stumm.
    warmwasser_boost: bool = False
    warmwasser_boost_seit: datetime | None = None
    # Kaltreserve der Saldo-Regelung: Reserve-Speicher entladen mit, solange
    # der mittlere SoC der übrigen unten ist.
    kaltreserve: bool = False
    # E-Auto: Überschuss reicht (mit Marge) für die Wallbox-Mindestleistung.
    # Start konservativ False, damit der erste Lauf nach einem Neustart nicht
    # sofort "E-Auto laden" meldet, ohne den Momentanüberschuss zu kennen.
    ev_bereit: bool = False
    # Zuletzt kommandierte Soll-Leistung der modulierbaren Lasten. Nur dazu da,
    # eine Wiederholung von einer echten Änderung zu unterscheiden: Die
    # Vorsteuerung des Speicher-Reglers gilt der Aktuierungs-Totzeit und darf
    # sich nicht in einen Dauer-Offset verwandeln, wenn eine Last ihrem Sollwert
    # nicht folgt. `None` heißt „noch nichts kommandiert" (erster Lauf, oder
    # Zwangsladung/keine Empfehlung) und lässt die Vorsteuerung voll wirken.
    ev_soll_w: float | None = None
    # Heizung: je Anlagen-ID ein eigener Schmitt-Trigger, weil mehrere
    # Wärmeerzeuger unterschiedliche Schwellen (und Sensoren) haben können.
    # Anders als die flachen Flags oben müssen diese beiden beim Fortschreiben
    # KOPIERT werden — `dataclasses.replace` kopiert nur flach, ein geteiltes
    # Dict würde die Eingabe des Aufrufers mitverändern (siehe compute_plan).
    frost: dict[str, bool] = field(default_factory=dict)
    heizen: dict[str, bool] = field(default_factory=dict)


def speicher_stumm_latch(
    verriegelt: set[str], name: str, *, schweigt: bool, nicht_gefolgt: bool
) -> bool:
    """Verriegelung „Speicher ausgefallen": zwei Auslöser, ein Rückweg.

    Verriegelt wird, wenn die SoC-Entität schweigt UND der Speicher einem Befehl
    ≠ 0 nicht gefolgt ist. Entriegelt wird ausschließlich über eine frische
    Meldung (`schweigt` fällt auf False) — nie dadurch, dass HEMS aufhört zu
    befehlen. Die Begründung beider Richtungen steht bei `HemsCoordinator._stumm`;
    hier steht sie HA-frei, damit der Übergang über mehrere Zyklen prüfbar ist
    (`tests/test_speicher_selbstsperre.py`). `verriegelt` wird dabei verändert.
    """
    if not schweigt:
        verriegelt.discard(name)
    elif nicht_gefolgt:
        verriegelt.add(name)
    return name in verriegelt


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
    # warmwasser_soll_c/warmwasser_status leer, statt den Default-Sollwert zu melden.
    thermal_present: bool = True
    priority_mode: str = PRIORITY_AUTO
    # Optimierungsziel (Laufzeit): steuert Ladeziel-SoC und Regler-Offset.
    goal: str = GOAL_SELF_CONSUMPTION
    # Regel-Aggressivität (Laufzeit, min/normal/max): skaliert die Regler-Gains,
    # damit Ladelücken schneller geschlossen werden. Beeinflusst nur die
    # Schrittweite pro Zyklus, nicht die Umschaltrate (bleibt 1×/min).
    gain_level: str = DEFAULT_GAIN_LEVEL
    # Notstromreserve (Laufzeit-Schalter): Der Speicher soll für einen Ausfall
    # bereitstehen. Hebt Ladedeckel, Just-in-time-Rampe und Mittagspause auf,
    # gibt dem Akku den Ladevorrang vor allen Lasten und lädt mit voller
    # Regel-Schrittweite. Alterung ist dann zweitrangig — ein leerer Speicher
    # im Ausfall kostet mehr als ein paar Zyklen Lebensdauer.
    emergency_reserve: bool = False
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
    # Verschiebung der lokalen Zeit gegen UTC in Stunden (z. B. 2.0 für MESZ),
    # vom Coordinator geliefert. Der Planner rechnet weiter ausschließlich in
    # UTC und kennt keine Zeitzone; für die Uhrzeit-Regeln der Ladestrategie
    # (Ladefenster, Mittagspause) braucht er aber die lokale Uhrzeit — sonst
    # verschöbe sich das Fenster mit dem Sommerzeit-Offset. Ein einzelner
    # Skalar reicht, weil die Regeln über den Planungshorizont von höchstens
    # zwei Tagen ausgewertet werden; eine Zeitumstellung darin verschiebt die
    # Fenster für einen Tag um eine Stunde, was folgenlos ist.
    utc_offset_h: float = 0.0
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
    # Mindestabstand zwischen zwei Boost-Wechseln (Minuten), siehe water_plan.
    thermal_boost_min_hold_min: int = DEFAULT_BOOST_MIN_HOLD_MIN
    # Modulierbare Lasten (Wallboxen …) für die Überschussregelung. Leer =
    # keine Wallbox konfiguriert; dann bleibt die alte, ungeprüfte Empfehlung.
    modulateds: list[ModulatedState] = field(default_factory=list)
    # Schaltbare Lasten (nur an/aus) für die Überschussregelung. Leer = keine
    # konfiguriert; dann bleibt die Schaltlast-Empfehlung leer. Wärmeerzeuger
    # stehen hier MIT drin (gleiche `id` wie in `heatings`) — sie konkurrieren
    # im selben Budget; `heatings` trägt nur ihre Witterungsführung.
    switchables: list[SwitchableState] = field(default_factory=list)
    # Wärmeerzeuger für Frostschutz, Heizgrenze, Sommersperre und Heizkurve.
    heatings: list[HeatingState] = field(default_factory=list)
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
class ChargeRamp:
    """Der Ladeplan des Tages als Gerade: von wo, wohin, wann.

    `basis_soc` ist der Stand bei der Planung — bis `start` hält der Deckel
    dort (der Akku lädt dann nur, was sonst eingespeist würde). Zwischen
    `start` und `ende` steigt er linear auf `ziel_soc`, danach bleibt er dort.
    `start is None` heißt "keine Zeit mehr für die Rampe": der Deckel steht
    sofort auf dem Ziel. Berechnet in `_ladeplan`, ausgewertet in
    `_lade_deckel_soc` — auch für zukünftige Zeitpunkte der SoC-Prognose.
    """

    ziel_soc: float
    basis_soc: float
    start: datetime | None = None
    ende: datetime | None = None


@dataclass
class PlanResult:
    nachtdefizit_kwh: float = 0.0
    ueberschuss_rest_kwh: float = 0.0
    speicher_soc: float | None = None
    speicher_verfuegbar_kwh: float = 0.0
    speicher_kapazitaet_kwh: float = 0.0
    speicher_ziel_soc: float | None = None
    speicher_bedarf_kwh: float = 0.0
    # Ladedeckel jetzt: SoC-Obergrenze, bis zu der die Saldo-Regelung laden
    # soll. Vor dem Rampenstart der aktuelle Stand (halten), danach die Rampe
    # auf das Nacht-Ziel — außer Deckung geht vor Schonung (dann sofort das
    # Ziel). Die geplante Absicht, nicht der Befehl: Überschuss, den sonst
    # niemand nimmt, lädt der Akku auch darüber hinaus
    # (regelung.laden_statt_einspeisen). Der Actuator schreibt den Deckel auf
    # den geräteseitigen Ziel-SoC — und hebt ihn in genau dem Fall auf 100 %.
    lade_deckel_soc: float | None = None
    # Endwert der Ladekurve: was der Speicher heute Abend haben soll
    # (Nachtbedarf + Marge, 100 % wenn Vollladung nötig ist).
    lade_ziel_soc: float | None = None
    # Wann die Ladung dafür beginnen muss (just in time). None = jetzt, weil
    # keine Zeit mehr zu verlieren ist oder nichts mehr fehlt.
    lade_start: datetime | None = None
    # Mittags-Ladepause (11:00–14:00 lokal): der Akku reserviert keinen
    # Überschuss vor den Lasten. Aufgehoben, wenn der Deckel ohnehin aufgehoben
    # ist (Deckung vor Schonung).
    lade_pause: bool = False
    sonnenfenster_h: float = 0.0
    morgen_knapp: bool = False
    kapazitaet_frei: bool = False
    kapazitaet_frei_kwh: float = 0.0
    entlade_budget_kwh: float = 0.0
    entlade_w_jetzt: float | None = None
    entladeplan: list[DischargeSlot] = field(default_factory=list)
    pv_kurve: list[PvSlot] = field(default_factory=list)
    soc_prognose: list[SocPoint] = field(default_factory=list)
    warmwasser_gesperrt: bool = False
    warmwasser_sperrfenster: list[tuple[datetime, datetime]] = field(default_factory=list)
    # Warmwasser-Orchestrierung: empfohlener Sollwert nach Priorität
    # Legionellenschutz > PV-Boost > Basis; in der Sperrzeit None ("aus").
    warmwasser_soll_c: float | None = None
    warmwasser_status: str = ""  # "aus" | "legionellenschutz" | "pv_boost" | "basis"
    warmwasser_legionelle_aktiv: bool = False
    # Ab wann der PV-Boost wieder wechseln darf (Mindestabstand); None heißt
    # „jederzeit". Reine Anzeige: erklärt einen Boost, der steht, obwohl seine
    # Kriterien schon wieder aus sind (und umgekehrt).
    warmwasser_boost_frei_ab: datetime | None = None
    warmwasser_legionellen_fenster: list[tuple[datetime, datetime]] = field(
        default_factory=list
    )
    # Das Gerät hat die geschriebene Freigabe nicht übernommen: HEMS hat sie
    # gestellt, und ein danach gelesener Ist-Zustand zeigt sie immer noch nicht.
    # Wird nicht geplant, sondern vom Actuator eingetragen — die Planung kennt
    # den Ist-Zustand der Steuer-Entität nicht.
    warmwasser_nicht_uebernommen: bool = False
    # Wärmeerzeuger, die eine geschriebene An/Aus-Lage nicht übernommen haben
    # (Namen). Wie oben vom Actuator eingetragen, nicht geplant. Eine Anlage,
    # die ein „aus" quittiert und weiterläuft, stünde sonst im Lastfluss als
    # abgeschaltet, während sie Strom zieht.
    heizung_nicht_uebernommen: list[str] = field(default_factory=list)
    # Speicher, die zugeteilte Ladeleistung nicht ziehen (Namen). Wie oben vom
    # Actuator eingetragen, nicht geplant: Die Planung kennt nur die Zuteilung,
    # nicht die Messung. Ohne diesen Rückweg sieht ein Speicher, der den
    # Ladebefehl entgegennimmt und stehen bleibt, in Empfehlung und Lastfluss
    # aus wie einer, der lädt — während der Überschuss ins Netz geht.
    speicher_nicht_uebernommen: list[str] = field(default_factory=list)
    # Empfehlung der Saldo-Regelung über alle Speicher (None ohne Daten).
    regelung: ControlResult | None = None
    # Empfehlung der Wallbox-Überschussregelung (None ohne Wallbox/Saldo).
    ev_regelung: EvControlResult | None = None
    schaltbare: SwitchableResult | None = None
    # Witterungsführung der Wärmeerzeuger. Entsteht ohne Netzsaldo (nur aus der
    # Temperatur) und liegt deshalb auch dann vor, wenn `schaltbare` None ist.
    heizung: HeatingResult | None = None
    empfehlung: str = "keine Daten"
    prioritaeten: list[str] = field(default_factory=list)
    # Fortgeschriebener Trigger-Zustand für den nächsten Lauf.
    flags: PlanFlags = field(default_factory=PlanFlags)
