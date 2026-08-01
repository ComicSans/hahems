"""Konstanten für die HEMS-Integration."""

DOMAIN = "hems"

CONF_METER = "meter_entity"
CONF_PV_POWER = "pv_power_entity"
CONF_INVERT = "invert_meter"
# Manche Wechselrichter melden die PV-Leistung mit umgekehrtem Vorzeichen
# (negativ = Erzeugung). Ist die Option aktiv, dreht der Coordinator das
# Vorzeichen der gemessenen PV-Momentanleistung vor allen Weiterberechnungen.
CONF_INVERT_PV = "invert_pv_power"
# Manche Anlagen messen PV und Akku am selben Punkt (Hybrid-Wechselrichter):
# der PV-Sensor enthält dann die Akkuleistung — Entladen treibt den PV-Wert
# hoch, Laden senkt ihn. Ist die Option aktiv, rechnet der Coordinator die
# Akkuleistung aus der gemessenen PV heraus (pv - batterie_w), damit die
# Anzeige die reine Erzeugung zeigt.
CONF_PV_MINUS_BATTERY = "pv_minus_battery"
CONF_BASELINE_W = "baseline_load_w"
CONF_NIGHT_W = "night_load_w"
CONF_DEVICES = "devices"
CONF_PRIORITY_MODE = "priority_mode"
CONF_WEATHER = "weather_entity"
CONF_FREE_KWH = "free_capacity_kwh"
CONF_FREE_H = "free_capacity_h"

PRIORITY_AUTO = "auto"
PRIORITY_BATTERY_FIRST = "battery_first"
PRIORITY_EV_FIRST = "ev_first"

# Optimierungsziel der Speicher-/Saldo-Regelung (Laufzeit-Select, orthogonal
# zum priority_mode, der nur die Überschussreihenfolge bestimmt).
#   eigenverbrauch  – bisheriges Verhalten: Bezug minimieren, Einspeisung
#                     ausdrücklich erlaubt (Regel-Residuum leicht in den Export),
#                     Ladeziel nur bis Nachtdeckung.
#   nulleinspeisung – echter Zero-Export: Regel-Residuum in den (minimalen)
#                     Bezug geschoben und Akku voll geladen, damit möglichst
#                     nichts ins Netz zurückfließt.
#   vollladen       – Ladeziel dauerhaft auf 100 %, sonst wie eigenverbrauch.
GOAL_SELF_CONSUMPTION = "eigenverbrauch"
GOAL_ZERO_FEEDIN = "nulleinspeisung"
GOAL_FULL_CHARGE = "vollladen"
GOALS = (GOAL_SELF_CONSUMPTION, GOAL_ZERO_FEEDIN, GOAL_FULL_CHARGE)

ROLE_FORECAST = "forecast"
ROLE_STORAGE = "storage"
ROLE_THERMAL = "thermal"
ROLE_SWITCHABLE = "switchable_load"
ROLE_MODULATED = "modulated_load"

MODE_OBSERVE = "beobachten"  # empfehlen + loggen, nicht schalten
MODE_AUTO = "auto"  # empfehlen + schalten (Actuator aktiv)
MODE_OFF = "aus"  # reiner Stopp: keine Schreibzugriffe (Kill-Switch)

DEFAULT_BASELINE_W = 500
DEFAULT_NIGHT_W = 400
DEFAULT_RESERVE_SOC = 10
DEFAULT_MAX_CHARGE_W = 1200
DEFAULT_MAX_DISCHARGE_W = 1200
DEFAULT_BASE_TARGET = 48
DEFAULT_COMFORT_TARGET = 60
DEFAULT_FREE_KWH = 3.0
DEFAULT_FREE_H = 1.0

# Warmwasser: Legionellenschutz (wöchentliches Fenster mit erhöhtem Sollwert)
# und PV-Boost-Kriterien. Der Boost auf den Komfort-Sollwert wird nur
# empfohlen, wenn der Speicher fast voll ist UND kräftig eingespeist wird;
# beide Schwellen mit Hysterese (Ein-/Aus-Niveau), damit die Empfehlung
# nicht im Minutentakt kippt.
DEFAULT_LEGIONELLA_TARGET = 60
DEFAULT_BOOST_SOC_ON = 80  # Speicher-SoC (%), ab dem der Boost starten darf
DEFAULT_BOOST_SOC_OFF = 75  # ... und unter dem er wieder endet
DEFAULT_BOOST_SALDO_ON_W = -2800  # Netzsaldo, ab dem der Boost starten darf
DEFAULT_BOOST_SALDO_OFF_W = 200  # ... und ab dem er wieder endet

# Saldo-Regelung der Speicher: Proportionalregler auf den Netzsaldo mit
# Prioritaet "Bezug minimieren". Asymmetrische Gains (schnell gegen teuren
# Bezug, gemächlich beim Laden), Sollwert leicht in die Einspeisung
# verschoben, damit das Regel-Residuum nie im Bezug landet. Selbst-
# korrigierend über die gemessene Speicherleistung.
CONTROL_GAIN_DISCHARGE = 0.65
CONTROL_GAIN_CHARGE = 0.5
# Regel-Aggressivität (Laufzeit, per Select): skaliert beide Gains gemeinsam,
# damit der Regler Ladelücken schneller schließt. Wirkt NUR auf die
# Korrektur-Schrittweite pro Zyklus — der 60-s-Takt (und damit die maximale
# Umschaltrate von 1×/min) bleibt unberührt. Ein größerer Faktor gleicht
# schneller aus, erhöht aber die Oszillations-Amplitude; das 1.0-Cap in
# _storage_control verhindert Overshoot über die volle Ein-Schritt-Korrektur
# hinaus. „normal" = 1.0 = bisheriges Verhalten. Default „max" (aggressiv).
GAIN_MIN = "min"
GAIN_NORMAL = "normal"
GAIN_MAX = "max"
GAIN_LEVELS = (GAIN_MIN, GAIN_NORMAL, GAIN_MAX)
CONTROL_GAIN_FACTORS = {GAIN_MIN: 0.6, GAIN_NORMAL: 1.0, GAIN_MAX: 1.5}
DEFAULT_GAIN_LEVEL = GAIN_MAX
CONTROL_TARGET_OFFSET_W = 25.0
CONTROL_DEADBAND_W = 30.0
CONTROL_MIN_SETPOINT_W = 60.0  # kleinere Sollwerte werden auf 0 gerundet
# Ziel "Nulleinspeisung": der Regler hält das Netz auf einem kleinen Bezug
# statt auf leichter Einspeisung. Der Betrag liegt bewusst deutlich über
# Totband und Min-Sollwert (30/60 W) — sonst wäre das Ziel vom Eigenverbrauch
# (Offset 25 W) nicht unterscheidbar, weil die 50-W-Differenz weggerundet würde.
# Wirkung: gegen realen Export wird geladen, kleiner Restbezug (bis ~Offset)
# wird toleriert statt in die Einspeisung ausgeregelt; am Nullpunkt bleibt der
# Regler stehen (kein Zwangsbezug zum Laden).
CONTROL_ZERO_FEEDIN_OFFSET_W = 100.0

# Sofort-Refresh außerhalb des 60-s-Takts, wenn der Netzsaldo sprunghaft
# springt (PV-Wolkenkante, Lastsprung). Der reguläre Poll reagiert im
# schlechtesten Fall erst fast 60 s später auf so einen Sprung — der Speicher
# hängt bis dahin am alten Sollwert (Aktuierungs-Totzeit). Der Cooldown
# verhindert Update-Stürme bei einer Serie kleiner Sprünge (z. B. flackernde
# Wolkenkante); er ist bewusst kürzer als der reguläre Takt, sonst brächte
# der Sofort-Refresh keinen Zeitgewinn.
SALDO_JUMP_W = 800.0
SALDO_JUMP_COOLDOWN_S = 20.0

# Kaltreserve: als Reserve markierte Speicher nehmen am Entladen erst teil,
# wenn der mittlere SoC der übrigen unter ON fällt, und scheiden erst
# oberhalb von OFF wieder aus (Hysterese). Geladen werden sie immer mit,
# proportional zur freien Kapazität.
RESERVE_SOC_ON = 40.0
RESERVE_SOC_OFF = 45.0

# Speicher-Auswahl-Hysterese ("erst einen Akku nutzen, dann den nächsten").
# Die Zuteilung bündelt greedy auf den Speicher mit der meisten verfügbaren
# Energie (bzw. freien Kapazität beim Laden). Bei fast gleichem SoC rotiert die
# Führung sonst jeden Zyklus, weil der arbeitende Speicher minimal unter die
# anderen fällt — jede Umschaltung ist eine kurze Leistungslücke (Netz-Spike)
# und ein Schaltvorgang, der die Akku-Elektronik verschleißt. LEAD_HYST_SOC ist
# der SoC-Vorsprung (in Prozentpunkten), den der aktuell arbeitende Speicher in
# der Rangfolge behält, bevor ein voller Speicher übernimmt: der Bonus
# verschiebt NUR die Reihenfolge, nie die Teilnahme-Schranke (Reserve-Grenze,
# Kaltreserve-Ausschluss bleiben unberührt). Größer = weniger Wechsel, aber ein
# Speicher altert etwas ungleicher; ~12 % ≈ ein Wechsel je 12 % Entladung.
# LEAD_POWER_W ist die |Leistung|, ab der ein Speicher als "arbeitend" gilt
# (über dem Mess-/Standby-Rauschen, unter dem kleinsten Setpoint von 60 W).
CONTROL_LEAD_HYST_SOC = 12.0
CONTROL_LEAD_POWER_W = 30.0

# Akku-Schonung: Ladedeckel über den Tag. Kalendarische Alterung ist bei hohem
# SoC am größten — ein bei 100 % dösender Akku altert schneller als einer bei
# ~78 %. Deshalb wird tagsüber nur bis STORAGE_DAY_HOLD_SOC geladen; erst in den
# letzten STORAGE_FULL_CHARGE_LEAD_H vor Sonnenuntergang steigt der Deckel linear
# auf 100 %, sodass der Speicher ~zum Sonnenuntergang voll für die Nacht ist und
# möglichst wenig Zeit bei 100 % verbringt. Der Deckel begrenzt nur das Laden
# (kein Zwangsentladen, wenn der SoC schon darüber liegt) und wird sofort auf
# 100 % aufgehoben, sobald Nachtdeckung vor Schonung geht: Ziel verlangt
# Vollladung (Nulleinspeisung/Vollladen), morgen wird es knapp, oder der
# Restertrag heute reicht nicht mehr, um später von HOLD auf 100 % nachzuladen.
STORAGE_DAY_HOLD_SOC = 78.0
STORAGE_FULL_CHARGE_LEAD_H = 3.0

# E-Auto: Die "E-Auto laden"-Empfehlung setzt voraus, dass der Überschuss die
# physikalische Mindestladeleistung der Wallbox erreicht (min_a × Phasen ×
# Netzspannung) — darunter kann real gar nicht geladen werden. Die
# Ein-Schwelle liegt mit Sicherheitsmarge über diesem Minimum, damit die
# Empfehlung nicht sofort wieder kippt, wenn der Überschuss knapp am Minimum
# schwankt; die Aus-Schwelle ist das nackte physikalische Minimum.
EV_VOLTAGE_PER_PHASE_V = 230.0
EV_SURPLUS_MARGIN_W = 200.0
# Nachfrage-Erkennung für die Rotation mehrerer modulierbarer Lasten: Eine an-
# geschaltete Last gilt als "lädt / fragt nach", sobald ihre gemessene Leistung
# über EV_DEMAND_FLOOR_W liegt. Frisch eingeschaltete Lasten zählen für die
# Anlaufzeit EV_DEMAND_GRACE_S auch bei geringer Leistung noch als nachfragend
# (das Auto handelt den Ladestrom erst aus). Nur nachfragende Lasten (oder
# solche, die heute schon geladen haben) konkurrieren um knappe Kapazität; eine
# angesteckt-lose Wallbox verdrängt so keine ladende — sie bekäme sonst als
# "am wenigsten geladen" dauernd den Vorzug.
EV_DEMAND_FLOOR_W = 500.0
EV_DEMAND_GRACE_S = 180.0
# Eine angeschaltete Last, die nach der Anlaufzeit weiter unter EV_DEMAND_FLOOR_W
# bleibt, gilt als „leer" (kein/volles Auto) und wird für EV_EMPTY_COOLDOWN_S in
# der Rotationsrangfolge nach hinten gestellt — sie weicht damit einer real
# ladenden Last, wird aber nach Ablauf erneut kurz geprüft (der einzige Weg,
# ein zwischenzeitlich angestecktes Auto zu entdecken). Zieht sie wieder
# Leistung, entfällt der Cooldown sofort.
EV_EMPTY_COOLDOWN_S = 1800.0

# Schaltbare Lasten (nur an/aus), überschussgesteuert. SWITCH_SURPLUS_MARGIN_W ist
# die Hysterese-Marge: einschalten erst ab erwartet_w + Marge, ausschalten erst
# unter erwartet_w − Marge, damit die Empfehlung nicht am Rand flattert.
# DEFAULT_SWITCHABLE_EXPECTED_W ist die konservativ hohe Annahme der
# Leistungsaufnahme, solange eine Last noch nie im An-Zustand gemessen wurde —
# lieber später einschalten als Netzbezug provozieren; der Coordinator lernt den
# echten Wert beim ersten Lauf.
SWITCH_SURPLUS_MARGIN_W = 200.0
DEFAULT_SWITCHABLE_EXPECTED_W = 2000.0
# Ab dieser gemessenen An-Leistung lernt der Coordinator die erwartete Leistung
# einer schaltbaren Last (darunter gilt sie als „an, aber zieht nichts").
SWITCH_LEARN_FLOOR_W = 20.0
# Heizungsgekoppelte Lasten (Wärmepumpe) haben einen ausgeprägten Anlauf: Regler,
# Umwälzpumpe und Ventile ziehen ein paar hundert Watt, lange bevor der
# Kompressor auf Leistung ist. Mit dem allgemeinen 20-W-Boden würde dieser
# Zwischenstand als „erwartete Leistung" gelernt — die Last gälte fortan als
# Kleinverbraucher, würde bei minimalem Überschuss eingeschaltet und beim
# nächsten Zyklus wegen des realen Bezugs sofort wieder abgeschaltet (Takten).
SWITCH_LEARN_FLOOR_HEAT_W = 500.0
# Anlaufkarenz: erst nach dieser Laufzeit im An-Zustand ist eine Messung
# repräsentativ. Deckt zugleich den Neustartfall ab — nach einem HA-Neustart
# zeigt `last_changed` auf den Neustart, die Karenz läuft also neu an, statt
# einen zufälligen Momentanwert zu übernehmen.
SWITCH_LEARN_WARMUP_S = 300.0
# Asymmetrisches Lernen: nach oben sofort (eine unterschätzte Last provoziert
# Netzbezug), nach unten nur mit diesem Anteil pro Messung. Sonst zieht jede
# Teillast- oder Taktpause den gelernten Wert nach unten und die Last wird zu
# früh eingeschaltet.
SWITCH_LEARN_DECAY = 0.25

# PV-Ertragsfaktor (0–1) je Wetterlage, falls die Vorhersage keinen
# Bewölkungsgrad liefert. Diffuses Licht bringt auch bedeckt noch Ertrag.
WEATHER_CONDITION_FACTORS = {
    "sunny": 1.0,
    "clear-night": 1.0,
    "windy": 0.9,
    "windy-variant": 0.8,
    "partlycloudy": 0.65,
    "cloudy": 0.35,
    "fog": 0.25,
    "rainy": 0.25,
    "lightning": 0.25,
    "lightning-rainy": 0.2,
    "pouring": 0.15,
    "hail": 0.15,
    "snowy": 0.15,
    "snowy-rainy": 0.15,
    "exceptional": 0.5,
}

# Störungs-/Warnmeldungen an den Nutzer (Repair-Issue, persistente Notification,
# Reine Bewertung in strategies/alerts.py, Zustellung im Coordinator.
# Schweregrade steuern, über welche Kanäle eine Meldung läuft:
#  • ERROR   — Config-Fehler (Auto-Modus würde scheitern): Repair (zusätzlich
#              zum bestehenden Log + binary_sensor.hems_konfiguration).
#  • WARNING — Config-Warnung: bleibt Sensor + Log, kein neuer Kanal (Setup-
#              Thema, kein Betriebsalarm).
ALERT_ERROR = "error"
ALERT_WARNING = "warning"

# Schweregrad → Zustellkanäle. Config-Fehler (ERROR) werden ein Repair-Issue;
# Config-Warnungen tauchen hier bewusst nicht auf — sie bleiben Sensor + Log.
ALERT_CHANNELS = {
    ALERT_ERROR: ("repair",),
}

# Sonderzustände einer Entität, die Nichterreichbarkeit bedeuten.
STATE_UNAVAILABLE_VALUES = ("unavailable", "unknown", "none", "")
