"""Speicher-Domäne: Saldo-Regelung und Ladestrategie über den Tag (Ladedeckel).

Live-Zuteilung der Lade-/Entladeleistung je Speicher nach der Zendure-
Verteilstrategie (Tobias 12.10.2026, T-002): Laden verteilt parallel (gewichtet
nach freier Kapazität × Ladeleistung), Kaltreserve zuerst; Entladen greedy mit
Auswahl-Hysterese in 0,5-kWh-Stufen, ab 85 % Auslastung parallel.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from ..const import (
    CONTROL_DEADBAND_W,
    CONTROL_GAIN_CHARGE,
    CONTROL_GAIN_DISCHARGE,
    CONTROL_GAIN_FULL_CHARGE,
    CONTROL_GAIN_FACTORS,
    CONTROL_LEAD_POWER_W,
    CONTROL_LEAD_SHARE,
    CONTROL_LEAD_STEP_KWH,
    CONTROL_MIN_SETPOINT_W,
    CONTROL_PARALLEL_OFF,
    CONTROL_PARALLEL_ON,
    CONTROL_SPLIT_MIN_W,
    CONTROL_TARGET_OFFSET_W,
    CONTROL_ZERO_FEEDIN_OFFSET_W,
    GOAL_ZERO_FEEDIN,
    RESERVE_POWER_OFF,
    RESERVE_POWER_ON,
    RESERVE_SOC_OFF,
    RESERVE_SOC_ON,
    SALDO_JUMP_W,
    SPEICHER_VOLL_SOC,
    STORAGE_AFTERNOON_FROM_H,
    STORAGE_DIRECTION_HOLD_S,
    STORAGE_FULL_BY_LEAD_H,
    STORAGE_MORNING_UNTIL_H,
    STORAGE_RAMP_SAFETY,
)
from .types import (
    ChargeRamp,
    ControlResult,
    PlanFlags,
    PlanInput,
    PlanResult,
    StorageSetpoint,
    StorageState,
    _latch,
)


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


def _lokale_stunde(inp: PlanInput, t: datetime) -> float:
    """Lokale Tageszeit von t als Dezimalstunde (0.0 … 24.0).

    Der Planner rechnet in UTC; die Ladefenster sind aber Uhrzeiten, die der
    Nutzer lokal meint. `utc_offset_h` liefert der Coordinator.
    """
    lokal = t + timedelta(hours=inp.utc_offset_h)
    return lokal.hour + lokal.minute / 60 + lokal.second / 3600


def _lokale_uhrzeit(inp: PlanInput, t: datetime) -> str:
    """Lokale Uhrzeit von t als „HH:MM" — für Empfehlungstexte."""
    return (t + timedelta(hours=inp.utc_offset_h)).strftime("%H:%M")


def _ist_ladepause(inp: PlanInput, sofort_voll: bool, t: datetime) -> bool:
    """Mittags-Ladepause (11:00–14:00 lokal): der Akku tritt beim Überschuss
    hinter die Lasten zurück, die Mittagsspitze gehört Warmwasser, Wallbox und
    Wärmepumpe — deren Puffer kostet keine Zyklenfestigkeit. Er lädt in der
    Pause weiter, aber nur noch aus dem, was die Lasten übrig lassen — statt es
    einzuspeisen (siehe `_storage_control`).

    `sofort_voll` hebt die Pause auf: wer heute noch voll werden muss (oder per
    „Akkus aufladen" bereitstehen soll), hat keine drei Stunden zu verschenken.
    """
    if sofort_voll:
        return False
    return STORAGE_MORNING_UNTIL_H <= _lokale_stunde(inp, t) < STORAGE_AFTERNOON_FROM_H


def _ladeplan(
    inp: PlanInput,
    res: PlanResult,
    *,
    soc_jetzt: float,
    cap_kwh: float,
    ziel_soc: float,
    sofort_voll: bool,
) -> ChargeRamp:
    """Den Ladeplan des Tages rückwärts aus dem Nachtbedarf rechnen.

    Für die kalendarische Alterung zählt die Zeit bei hohem SoC, nicht die
    Spitze. Also wird so spät wie vertretbar geladen: Der Speicher soll
    STORAGE_FULL_BY_LEAD_H vor Sonnenuntergang auf `ziel_soc` stehen, und die
    Rampe beginnt genau so früh, dass der erwartete Überschuss das schafft —
    mit STORAGE_RAMP_SAFETY als Zuschlag gegen Prognosefehler. Vorher hält der
    Deckel auf dem aktuellen Stand, der Akku überlässt den Überschuss also den
    Lasten (nimmt aber weiter, was sonst ins Netz ginge).

    Ohne Rampe (`start=None`) steht der Deckel sofort auf dem Ziel — bzw. auf
    dem Stand, falls der schon reicht. Das gilt, wenn keine Zeit mehr zu
    verlieren ist (`sofort_voll`, kein Sonnenfenster, kein Überschuss zu
    erwarten) oder nichts mehr fehlt. Der Fall, dass die Rampe rechnerisch in
    der Vergangenheit beginnt, braucht keine Sonderbehandlung — dann steht der
    Deckel ohnehin schon über dem aktuellen Stand.
    """
    if sofort_voll:
        return ChargeRamp(ziel_soc=ziel_soc, basis_soc=soc_jetzt)
    ende = inp.sunset - timedelta(hours=STORAGE_FULL_BY_LEAD_H)
    fehlend_kwh = max(0.0, (ziel_soc - soc_jetzt) / 100 * cap_kwh)
    if fehlend_kwh <= 0:
        # Der Speicher deckt die Nacht schon: nichts zu planen, der Deckel hält
        # auf dem Stand (`_lade_deckel_soc` nimmt das Maximum aus beiden).
        return ChargeRamp(ziel_soc=ziel_soc, basis_soc=soc_jetzt)
    # Erwartete Ladeleistung: der mittlere Restüberschuss des Tages, gedeckelt
    # auf das, was die Speicher überhaupt aufnehmen können.
    mittel_w = (
        res.ueberschuss_rest_kwh * 1000 / res.sonnenfenster_h
        if res.sonnenfenster_h > 0
        else 0.0
    )
    lade_w = min(sum(s.max_charge_w for s in inp.storages), mittel_w)
    if lade_w <= 0:
        return ChargeRamp(ziel_soc=ziel_soc, basis_soc=soc_jetzt)
    dauer_h = fehlend_kwh * 1000 / lade_w * STORAGE_RAMP_SAFETY
    return ChargeRamp(
        ziel_soc=ziel_soc,
        basis_soc=soc_jetzt,
        start=ende - timedelta(hours=dauer_h),
        ende=ende,
    )


def _lade_deckel_soc(rampe: ChargeRamp, t: datetime) -> float:
    """Ladedeckel (SoC-%) zum Zeitpunkt t entlang des Ladeplans.

    Nur eine Ladeobergrenze, kein Entladebefehl — liegt der SoC bereits über dem
    Deckel, bleibt er stehen (die Regelung lädt ihn nur nicht weiter). Und keine
    harte Grenze gegen die Einspeisung: bliebe der Überschuss sonst ungenutzt,
    lädt `_storage_control` auch darüber hinaus. Nie unter den Planungsstand:
    ein Deckel unter dem Ist-SoC wäre keine Grenze mehr, sondern eine Auffor-
    derung ans Gerät, sich leer zu machen.
    """
    if rampe.start is None or rampe.ende is None or t >= rampe.ende:
        return max(rampe.basis_soc, rampe.ziel_soc)
    if t <= rampe.start:
        return rampe.basis_soc
    anteil = (t - rampe.start) / (rampe.ende - rampe.start)
    return max(
        rampe.basis_soc,
        rampe.basis_soc + anteil * (rampe.ziel_soc - rampe.basis_soc),
    )


def _primaer_max_entladung(known: list[StorageState]) -> float:
    """Summe der Entladegrenzen der Nicht-Reserve-Speicher mit Energie.

    Bezugsgröße der Leistungs-Kaltreserve (T-002): Ein Speicher an der
    Reserve-Grenze trägt nichts und darf die Schwelle nicht nach oben schieben.
    """
    return sum(
        s.max_discharge_w
        for s in known
        if not s.cold_reserve and s.soc > s.reserve_soc
    )


def _richtung_gehalten(
    inp: PlanInput, res: PlanResult, soll: float, fehler: float
) -> bool:
    """Mindesthaltezeit der Speicher-Richtung (T-012, Tobias 12.10.2026).

    Gibt True zurück, wenn der Regler innerhalb von STORAGE_DIRECTION_HOLD_S
    nach einem Wechsel wieder in die Gegenrichtung will — dann pausiert der
    Speicher (0 W, Select bleibt stehen). Ein großer Regelfehler (Lastsprung ab
    SALDO_JUMP_W) und „Akkus aufladen" wechseln sofort. Schreibt die gefahrene
    Richtung samt Zeitpunkt in `res.flags`.
    """
    richtung = (
        "entladen" if soll > CONTROL_DEADBAND_W
        else "laden" if soll < -CONTROL_DEADBAND_W
        else None
    )
    vorher = inp.flags.speicher_richtung
    seit = inp.flags.speicher_richtung_seit
    if richtung is None or richtung == vorher:
        return False
    if (
        vorher is not None
        and seit is not None
        and (inp.now - seit).total_seconds() < STORAGE_DIRECTION_HOLD_S
        and abs(fehler) < SALDO_JUMP_W
        and not inp.battery_force
    ):
        return True
    res.flags.speicher_richtung = richtung
    res.flags.speicher_richtung_seit = inp.now
    return False


def _verteile_entladen(
    anteile: list[tuple[StorageState, float]], gesamt: float
) -> dict[str, float]:
    """Entladeleistung greedy zuteilen: die Einheit mit der meisten
    verfügbaren Energie zuerst voll ausschöpfen, dann die nächste. Bewusst
    NICHT proportional zerstäuben — bei N Einheiten läge sonst jeder Anteil
    unter dem Mindest-Setpoint und würde auf 0 gerundet (Totzone ~N×min).
    Das Bündeln reduziert zugleich das Schütz-/Umschalt-Flattern: es
    entlädt möglichst nur ein Akku zur Zeit (Verschleiß der Akku-Elektronik).

    Auswahl-Hysterese: Gereiht wird nach der verfügbaren Energie in Stufen
    von LEAD_STEP_KWH; innerhalb derselben Stufe behält der aktuell
    FÜHRENDE Speicher den Vorrang, damit die Führung nicht bei jedem
    minimalen SoC-Crossover rotiert. Die Stufe verschiebt NUR die
    Reihenfolge; die Teilnahme-Schranke unten prüft weiter den
    rohen `anteil` (Reserve-Grenze, Kaltreserve-Ausschluss bleiben
    unberührt). Reicht ein Speicher nicht (soll > seine Grenze), füllt die
    Schleife den nächsten weiter auf.

    Führung heißt: die Einheit trägt den Hauptteil der Leistung — absolut
    über LEAD_POWER_W UND über dem Anteil LEAD_SHARE am Gesamtsoll. Die
    absolute Schwelle allein genügt nicht, sobald gesplittet werden muss:
    Dann liegt auch der Mitläufer über ihr, beide gelten als Führung, der
    Vorrang hebt sich auf, und die Rangfolge kippt bei jedem 1-%-Crossover.
    Über LEAD_SHARE = 0.5 kommt nur eine Einheit, der Vorrang bleibt eindeutig
    (Begründung samt Messung bei CONTROL_LEAD_SHARE)."""

    # Führung am GEMESSENEN Anteil (T-009): Nach einem 600/600-Split fällt
    # das neue Soll etwa auf 800 W; gegen `gesamt` gerechnet lägen beide
    # über der Hälfte, beide „führten", und die Rangfolge kippte beim
    # nächsten 1-%-Crossover. Gegen die Summe der Messwerte kann nur eine
    # Einheit über LEAD_SHARE liegen.
    gemessen = sum(max(0.0, s.power_w or 0.0) for s, _ in anteile)

    def _rang(paar: tuple[StorageState, float]) -> tuple[bool, float, bool, float]:
        s, anteil = paar
        p = s.power_w or 0.0
        arbeitet = p > CONTROL_LEAD_POWER_W and p > CONTROL_LEAD_SHARE * gemessen
        # Kleine Toleranz gegen Gleitkomma-Reste genau an der Stufengrenze
        # (0.55 * 8 kWh ergibt sonst 4.3999… und fiele eine Stufe tiefer).
        stufe = math.floor(anteil / CONTROL_LEAD_STEP_KWH + 1e-9)
        # Kaltreserve nie vorn (T-011): sie trägt nur den Rest.
        return (not s.cold_reserve, stufe, arbeitet and anteil > 0, anteil)

    rest = gesamt
    watts: dict[str, float] = {s.name: 0.0 for s, _ in anteile}
    for s, anteil in sorted(anteile, key=_rang, reverse=True):
        watt = min(rest, s.max_discharge_w)
        if anteil <= 0 or watt < CONTROL_MIN_SETPOINT_W:
            continue
        watts[s.name] = watt
        rest -= watt
    return watts


def _verteile_proportional(
    anteile: list[tuple[StorageState, float]], gesamt: float, *, laden: bool
) -> dict[str, float]:
    """Leistung PARALLEL auf mehrere Akkus verteilen — gewichtet nach
    `anteil` × Höchstleistung der Einheit (Zendure-Gewichtung; `anteil` ist
    beim Laden die freie Kapazität, beim Entladen die verfügbare Energie
    über der Reserve). Das gleicht die SoCs an und gibt der stärkeren
    Einheit den größeren Teil. Verteilt wird nur auf so viele Einheiten,
    dass jede mindestens CONTROL_SPLIT_MIN_W trägt — darunter arbeitet der
    Wandler im ineffizienten Teillastbereich, und bei N Einheiten fiele
    jeder Anteil sonst unter den Mindest-Setpoint und würde auf 0 gerundet
    (die Leistung liefe trotz freiem Akku ins bzw. aus dem Netz). Reicht
    sie nur für weniger Einheiten, fällt die Verteilung schrittweise auf
    die Akkus mit dem größten Anteil zurück — aber nur, solange die
    verbleibenden das Ganze aufnehmen können; sonst ginge der Rest ins
    Netz, und dann ist ein Teillast-Mitläufer das kleinere Übel.

    Beim LADEN ist das immer die richtige Form: mehrere Akkus gleichzeitig
    zu laden ist schonender und schneller, und Ladeflattern ist unkritisch
    (kein Richtungswechsel). Beim ENTLADEN erst ab 85 % des Einzelmaximums
    (CONTROL_PARALLEL_ON) — bis dahin bündelt `_verteile_entladen` bewusst
    auf eine Einheit."""

    def _grenze(s: StorageState) -> float:
        return s.max_charge_w if laden else s.max_discharge_w

    def _fuellen(einheiten: list[tuple[StorageState, float]]) -> dict[str, float]:
        # Proportional zum Anteil, iterativ auf die Leistungsgrenze der
        # Einheit gedeckelt: was eine gedeckelte Einheit nicht aufnimmt,
        # fließt an die übrigen.
        verteilt = {s.name: 0.0 for s, _ in einheiten}
        # Gewicht > 0 statt nur Anteil > 0: eine Einheit mit Höchstleistung
        # 0 hätte sonst Gewicht 0, und frei_summe könnte 0 werden.
        aktiv = [
            (s, f * _grenze(s)) for s, f in einheiten if f > 0 and _grenze(s) > 0
        ]
        rest = gesamt
        while rest > 1e-6 and aktiv:
            frei_summe = sum(f for _, f in aktiv)
            basis = rest
            naechste: list[tuple[StorageState, float]] = []
            gedeckelt = False
            for s, f in aktiv:
                zusatz = basis * f / frei_summe
                platz = _grenze(s) - verteilt[s.name]
                if zusatz >= platz:
                    verteilt[s.name] += platz
                    rest -= platz
                    gedeckelt = True
                else:
                    verteilt[s.name] += zusatz
                    rest -= zusatz
                    naechste.append((s, f))
            aktiv = naechste
            if not gedeckelt:
                break
        return verteilt

    # Kandidaten mit Anteil > 0, größter Anteil zuerst (beim Laden also der
    # leerste Akku, beim Entladen der vollste).
    kandidaten = sorted(
        [(s, a) for s, a in anteile if a > 0], key=lambda p: p[1], reverse=True
    )
    watts: dict[str, float] = {s.name: 0.0 for s, _ in anteile}
    while kandidaten:
        verteilt = _fuellen(kandidaten)
        positive = [w for w in verteilt.values() if w > 0]
        # Kleinster gestellter Anteil zu klein? Schwächste Einheit
        # (kleinster Anteil = letzte im sortierten Feld) fallen lassen und
        # erneut auf die übrigen verteilen — sofern die übrigen das Ganze
        # tragen können. Unter dem Mindest-Setpoint wird immer gestrichen
        # (der Anteil würde ohnehin auf 0 gerundet).
        zu_klein = bool(positive) and len(kandidaten) > 1 and (
            min(positive) < CONTROL_MIN_SETPOINT_W
            or (
                min(positive) < CONTROL_SPLIT_MIN_W
                and sum(_grenze(s) for s, _ in kandidaten[:-1]) >= gesamt
            )
        )
        if zu_klein:
            # Die Einheit mit der kleinsten GESTELLTEN Leistung streichen
            # (T-011) — nicht die mit dem kleinsten Anteil: gewichtet wird
            # nach Anteil × Höchstleistung, beides fällt nicht zusammen.
            # Bei Gleichstand fällt die hintere (kleinerer Anteil).
            kleinste = min(
                (k[0].name for k in reversed(kandidaten) if verteilt[k[0].name] > 0),
                key=lambda n: verteilt[n],
            )
            kandidaten = [k for k in kandidaten if k[0].name != kleinste]
            continue
        for name, w in verteilt.items():
            watts[name] = w if w >= CONTROL_MIN_SETPOINT_W else 0.0
        break
    return watts


def _verteile(
    anteile: list[tuple[StorageState, float]],
    gesamt: float,
    laden: bool,
    *,
    parallel: bool = False,
) -> list[StorageSetpoint]:
    """Gesamtleistung je Speicher zuteilen. Laden verteilt immer parallel
    (proportional zur freien Kapazität), Entladen greedy mit Auswahl-
    Hysterese (ein Akku zur Zeit, gegen Verschleiß) — außer `parallel`,
    dann anteilig wie beim Laden. Ein Rest unter dem Mindest-Setpoint
    bleibt ungestellt — konservativ: nie mehr kommandieren als der gain-/
    offset-gedämpfte Zielwert hergibt (kein Netzbezug)."""
    watts = (
        _verteile_proportional(anteile, gesamt, laden=laden)
        if laden or parallel
        else _verteile_entladen(anteile, gesamt)
    )
    return [
        StorageSetpoint(name=s.name, watt=round(watts[s.name]))
        for s, _a in anteile
    ]


def _entlade_zuteilung(
    primaer: list[tuple[StorageState, float]],
    reserve_teil: list[tuple[StorageState, float]],
    primaer_max: float,
    soll: float,
    flags: PlanFlags,
) -> list[StorageSetpoint]:
    """Entladen mit Kaltreserve als Rest (T-011, Tobias 12.10.2026).

    Die übrigen Speicher tragen zuerst — bei Leistungsspitze nur bis zu
    ihrer Auslastungsgrenze (RESERVE_POWER_ON × Σ Höchstleistung), damit
    die 85-%-Regel gegen Wärme auch hier gilt. Die Reserve bekommt den
    Rest; was auch sie nicht trägt, füllen die übrigen bis an ihre Grenze
    auf. Ein Rest unter dem Mindest-Setpoint bleibt bei den übrigen statt
    die Reserve für ein paar Watt anlaufen zu lassen.
    """
    parallel = flags.parallel_entladen
    if not any(a > 0 for _, a in reserve_teil):
        return _verteile(primaer + reserve_teil, soll, laden=False, parallel=parallel)
    deckel = soll
    if flags.kaltreserve_leistung and primaer_max > 0:
        deckel = min(soll, RESERVE_POWER_ON * primaer_max)
    z_pri = _verteile(primaer, deckel, laden=False, parallel=parallel) if primaer else []
    rest = soll - sum(z.watt for z in z_pri)
    z_res: list[StorageSetpoint] = []
    if rest >= CONTROL_MIN_SETPOINT_W:
        z_res = _verteile(reserve_teil, rest, laden=False)
        offen = soll - sum(z.watt for z in z_pri) - sum(z.watt for z in z_res)
        if offen >= CONTROL_MIN_SETPOINT_W and primaer:
            z_pri = _verteile(
                primaer, soll - sum(z.watt for z in z_res),
                laden=False, parallel=parallel,
            )
    elif primaer:
        z_pri = _verteile(primaer, soll, laden=False, parallel=parallel)
    nach_name = {z.name: z for z in z_pri + z_res}
    return [
        nach_name.get(s.name, StorageSetpoint(name=s.name, watt=0))
        for s, _ in primaer + reserve_teil
    ]


def _storage_control(
    inp: PlanInput,
    res: PlanResult,
    ev_target_w: float | None = None,
    schaltbar_delta_w: float = 0.0,
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

    `schaltbar_delta_w` ist die Feedforward-Korrektur für schaltbare Lasten
    (z. B. Wärmepumpe): `switchable_control` kennt deren NEUE Soll-Leistung
    bereits einen Zyklus, bevor sie real anliegt (Aktuierungs-Totzeit der
    Last selbst). Ohne diese Vorsteuerung sieht der Speicher-Regler erst im
    nächsten Zyklus, dass eine Last dazu- oder wegschaltet, und reagiert einen
    Takt zu spät — sichtbar als kurzer Bezugs-Spike beim Zuschalten. Die
    Korrektur wird wie beim Wallbox-Delta oben auf den Saldo aufaddiert, der
    ECHTE-Saldo-Schutz beim Laden (unten) bleibt unverändert wirksam, falls
    die reale Last noch nicht nachgezogen ist.
    """
    if inp.saldo_w is None or not inp.storages:
        return None
    # Ein abgemeldeter Speicher ist für die Zuteilung dasselbe wie einer ohne
    # SoC: Sein Stand ist nicht bekannt, sondern nur zuletzt bekannt gewesen.
    # Der Unterschied liegt allein im Schaden — ein stehengebliebener Wert
    # gewinnt jede Rangfolge, ein fehlender nimmt gar nicht teil (siehe
    # STORAGE_STALE_MIN).
    known = [s for s in inp.storages if s.soc is not None and not s.stale]
    if not known and not any(s.stale for s in inp.storages):
        # Kein Speicher hat je einen SoC geliefert: unverändert keine
        # Empfehlung — daran hängt nichts, was abgeschaltet gehörte.
        return None
    # Sind ALLE Speicher abgemeldet (bei drei Geräten an einem MQTT-Pfad ein
    # Broker-Neustart, nicht die Ausnahme), läuft die Regelung trotzdem weiter
    # bis zur Zuteilung — siehe den Zweig `if not known` dort.

    # Kaltreserve-Hysterese über den mittleren Abstand der Nicht-Reserve-
    # Speicher zu ihrer eigenen Reserve-SoC („erst kurz vor leer", T-011).
    # Meldet kein Primärspeicher (alle abgemeldet), ist die Reserve aktiv:
    # Sonst hielte der Latch nach einem Neustart False, und das Haus zöge aus
    # dem Netz, während der Notfallakku voll danebensteht.
    primaer_abstand = [s.soc - s.reserve_soc for s in known if not s.cold_reserve]
    if primaer_abstand:
        res.flags.kaltreserve = _latch(
            inp.flags.kaltreserve,
            sum(primaer_abstand) / len(primaer_abstand),
            on=RESERVE_SOC_ON,
            off=RESERVE_SOC_OFF,
        )
    elif any(s.cold_reserve for s in known):
        res.flags.kaltreserve = True
    reserve_aktiv = res.flags.kaltreserve

    # Ist-Leistung nur aus Speichern, die noch melden: Der eingefrorene Wert
    # eines abgemeldeten Speichers ist kein Messwert mehr, und der Regler
    # rechnet ihn sonst als reale Leistung in seinen nächsten Schritt ein.
    bat_ist = sum(
        s.power_w for s in inp.storages if s.power_w is not None and not s.stale
    )
    # E-Auto-Zwangsladung: die Wallbox-Last nicht ausregeln, sonst entlädt der
    # Regler den Hausakku, um den Netzbezug der Wallbox zu decken. Der
    # herausgerechnete Saldo lässt den Akku seinen SoC halten; das Zwangs-Delta
    # bleibt beim Netz. Der Grundwerte-Schalter `battery_to_ev` hebt genau
    # diese Bereinigung auf: Steht er, bleibt `saldo_w` unverändert, der Regler
    # sieht den vollen Zwangsbezug und deckt ihn aus dem Akku. `ev_target_w`
    # bleibt bei Zwangsladung planungsseitig None (planner.py) — das Vorsteuer-
    # `elif` darunter greift also auch mit aktivem Schalter nicht, keine
    # Doppelzählung des Wallbox-Deltas.
    saldo_w = inp.saldo_w
    if inp.ev_force and inp.wallbox_w and not inp.battery_to_ev:
        saldo_w = inp.saldo_w - inp.wallbox_w
    elif ev_target_w is not None and inp.wallbox_w is not None:
        # Überschussregelung: HEMS stellt die Wallbox gleich auf ev_target_w.
        # Der Regler soll den Saldo sehen, der sich mit diesem NEUEN Sollwert
        # ergibt (Ist-Last + Delta), sonst hielte er die Akku-Entladung für die
        # bereits gedrosselte Wallbox aufrecht. Am Nullpunkt (Wallbox schon auf
        # Soll) verschwindet das Delta — der Regler sieht wieder den Rohsaldo.
        saldo_w = inp.saldo_w + (ev_target_w - inp.wallbox_w)
    saldo_w += schaltbar_delta_w
    # Sollwert-Offset: Eigenverbrauch/Vollladen schieben das Regel-Residuum
    # leicht in die Einspeisung (+25 W). Echte Nulleinspeisung hält stattdessen
    # einen kleinen Bezug (−100 W) — deutlich über Totband, damit das Ziel
    # wirklich anders regelt: gegen Export laden, kleinen Restbezug tolerieren.
    offset = _ziel_offset(inp)
    fehler = saldo_w + offset
    # Basis-Gain (asymmetrisch: schnell gegen Bezug, gemächlich beim Laden),
    # skaliert mit der Regel-Aggressivität. Auf 1.0 gedeckelt: ein Gain von 1
    # korrigiert den Fehler bereits in einem Schritt vollständig; darüber würde
    # der Proportionalregler überschwingen. Der 60-s-Takt bleibt unberührt —
    # aggressiver heißt größerer Schritt, nicht häufigeres Umschalten.
    faktor = CONTROL_GAIN_FACTORS.get(inp.gain_level, 1.0)

    def _gain(fehler_w: float) -> float:
        # „Akkus aufladen": beim Laden die volle Schrittweite, unabhängig von der
        # eingestellten Aggressivität — der gemächliche Lade-Gain ist Schonung,
        # und die ist hier ausdrücklich nicht gefragt.
        if inp.battery_force and fehler_w <= 0:
            return CONTROL_GAIN_FULL_CHARGE
        basis = CONTROL_GAIN_DISCHARGE if fehler_w > 0 else CONTROL_GAIN_CHARGE
        return min(1.0, basis * faktor)

    gain = _gain(fehler)
    max_ent = sum(s.max_discharge_w for s in known)
    max_lad = sum(s.max_charge_w for s in known)
    soll = max(-max_lad, min(bat_ist + fehler * gain, max_ent))
    # Ungekappte Forderung für die Freischwimm-Probe weiter unten (Frage 2,
    # Aufgabe „Speicher-Selbstsperre", Git 129880c): dieselbe Formel wie `soll`,
    # nur OHNE die max_ent-Kappung — sonst verschwindet genau der Rest, den
    # `known` nicht decken kann, den die Probe aber sehen muss. Läuft durch
    # dieselben Wallbox-Klauseln wie `soll` (unten), damit „kein Akkustrom
    # ins Auto" auch für die Probe gilt — die Klauseln deckeln sonst nur das
    # bereits gekappte `soll`.
    soll_wunsch = max(-max_lad, bat_ist + fehler * gain)

    # Asymmetrie gegen Laden-in-den-Netzbezug: Die Wallbox-Herausrechnung oben
    # soll den Akku nur davon abhalten, FÜR die Wallbox zu ENTLADEN — sie darf
    # ihn aber nicht gegen echten Netzbezug WEITERLADEN lassen. Regelt HEMS die
    # Wallbox herunter (dann inp.saldo_w > saldo_w), unterstellt der bereinigte
    # Saldo, ihre Last sei schon weg; folgt das Auto erst im nächsten Zyklus
    # (Totzeit) oder hängt es an seinem Mindeststrom, lädt der Akku sonst in den
    # Bezug hinein (bis hin zu halluzinierter Einspeisung, wenn der bereinigte
    # Saldo ins Minus kippt). Beim Laden (soll < 0) daher zusätzlich den ECHTEN
    # Saldo prüfen — über denselben Gain geglättet — und die Ladung höchstens
    # bis zur Ruhelage zurücknehmen. Der Entlade-Zweig (soll > 0) bleibt der
    # wallbox-bereinigten Logik überlassen (kein Entladen für die Wallbox).
    if soll < 0 and inp.saldo_w > saldo_w:
        fehler_roh = inp.saldo_w + offset
        soll = max(-max_lad, min(0.0, max(soll, bat_ist + fehler_roh * _gain(fehler_roh))))
        soll_wunsch = max(
            -max_lad, min(0.0, max(soll_wunsch, bat_ist + fehler_roh * _gain(fehler_roh)))
        )

    # Gegenstück beim ENTLADEN: kein Akkustrom ins Auto (23.08.2026). Die
    # Vorsteuerung oben (`saldo + (ev_target − wallbox_w)`) hält den Regler nur
    # davon ab, für eine Wallbox zu entladen, die HEMS gerade herunterregelt.
    # Steht die Wallbox schon auf ihrem Sollwert — typisch: sie hängt an ihrem
    # Mindeststrom, oder eine Mindestlaufzeit hält sie — ist das Delta null, der
    # Regler sieht ihre volle Last im Saldo und deckt sie aus dem Akku. Dann
    # fließt Akkustrom ins Auto, ohne dass irgendwo eine Entscheidung dafür
    # gefallen wäre: Der Akku entlädt sich in genau den Verbraucher, vor dem er
    # laut Vorrang stehen sollte.
    #
    # Deshalb den Entlade-Sollwert zusätzlich gegen den Saldo OHNE die
    # Wallbox-Last deckeln — dieselbe Sollwertformel, nur mit dem Fehler, den
    # der Regler ohne Wallbox sähe. Der Haushalt wird weiter aus dem Akku
    # gedeckt; der Wallbox-Anteil bleibt beim Netz oder fällt weg, wenn die
    # Lastregelung sie im nächsten Zyklus abschaltet. Spiegelbildlich zur
    # Klausel darüber: die schützt den Lade-Zweig vor der Bereinigung, diese
    # schützt den Entlade-Zweig vor ihrem Ausbleiben.
    #
    # Bei Zwangsladung ändert die Klausel nichts: Dort ist `saldo_w` bereits um
    # die Wallbox bereinigt, beide Rechnungen fallen zusammen.
    #
    # Auch diese Klausel steht unter dem Grundwerte-Schalter `battery_to_ev`:
    # aktiv, entfällt der Deckel, und der Akku deckt die volle Last inklusive
    # des Wallbox-Anteils — genau der Fall, den der Kommentar oben beschreibt
    # ("Akkustrom ins Auto"), nur jetzt als bewusste Entscheidung statt als
    # blinder Fleck.
    #
    # `soll_wunsch > 0` gehört mit in die Bedingung: Sind alle Speicher
    # abgemeldet, ist `max_ent` 0 und damit `soll` 0 — die Klausel griffe
    # sonst nie, und die Freischwimm-Probe bekäme ungedeckelt genau die
    # Wallbox-Last zugeteilt, die sie nicht decken darf.
    if (soll > 0 or soll_wunsch > 0) and inp.wallbox_w and not inp.battery_to_ev:
        fehler_ohne_ev = inp.saldo_w - inp.wallbox_w + offset
        soll = min(
            soll, max(0.0, bat_ist + fehler_ohne_ev * _gain(fehler_ohne_ev))
        )
        soll_wunsch = min(
            soll_wunsch, max(0.0, bat_ist + fehler_ohne_ev * _gain(fehler_ohne_ev))
        )

    # Speicher-Zwangsladung: der Sollwert kommt nicht mehr aus dem Saldo,
    # sondern steht auf der vollen Ladeleistung — auch gegen Netzbezug. Das ist
    # der einzige Pfad in HEMS, der Netzstrom in den Speicher schiebt, und er
    # ist ausdrücklich eine Anweisung des Betreibers, keine Regelentscheidung
    # (siehe `PlanInput.battery_force`).
    #
    # Bewusst NACH beiden Wallbox-Klauseln: Die deckeln das Laden gegen den
    # echten Saldo bzw. gegen die Wallbox-Last — genau die Deckel, die einen
    # Zwang wirkungslos machten. `fehler_w` bleibt unberührt und damit
    # informativ: Der Sensor zeigt weiter, was der Regler ohne den Zwang täte.
    #
    # Nur meldende Speicher unter dem PHYSISCHEN Ladeende nehmen teil. Beide
    # Einschränkungen tragen: `known` schließt abgemeldete aus (ihr SoC ist
    # keine Messung mehr), `SPEICHER_VOLL_SOC` die vollen. Die zweite ist keine
    # Feinheit, sondern die Bedingung dafür, dass der Zwang überhaupt endet:
    # Ein Zendure Hyper 2000 meldet 100 % faktisch nie, er steht bei 99 % im
    # CV-Taper und nimmt nichts mehr. Ein Kriterium „SoC < 100" hielte den
    # Zwang dort für immer offen und kommandierte drei sattten BMS dauerhaft
    # die volle Ladeleistung — dieselbe Bugfamilie wie in der Aufgabe
    # „Speicher-Selbstsperre" (Git 129880c).
    #
    # Ohne ladefähige Einheit (Summe 0) bleibt der normale Regelweg stehen: Ein
    # Zwang, der niemanden erreicht, soll auch nichts kommandieren.
    if _richtung_gehalten(inp, res, soll, fehler):
        soll = soll_wunsch = 0.0

    zwang_offen = [s for s in known if s.soc < SPEICHER_VOLL_SOC]
    if inp.battery_force:
        zwang_lad = sum(s.max_charge_w for s in zwang_offen)
        if zwang_lad > 0:
            soll = soll_wunsch = -zwang_lad
        else:
            # Voll halten (T-004, Tobias 12.10.2026): Kein meldender Speicher
            # steht mehr unter dem physischen Ladeende. Der Schalter bleibt
            # stehen; die Regelung folgt wieder dem Saldo, aber nur in
            # Laderichtung — Überschuss nimmt der Akku weiter auf, entladen
            # wird nicht. Sinkt ein Speicher unter das Ladeende, greift oben
            # wieder die volle Ladeleistung. Gilt auch, wenn alle Speicher
            # abgemeldet sind: kein Entladebefehl, solange aufgeladen werden
            # soll.
            soll = min(soll, 0.0)
            soll_wunsch = min(soll_wunsch, 0.0)

    ctrl = ControlResult(
        modus="pausiert",
        fehler_w=round(fehler, 0),
        soll_w=round(soll, 0),
        reserve_aktiv=reserve_aktiv,
        reserve_namen=[s.name for s in inp.storages if s.cold_reserve],
        abgemeldet_namen=[s.name for s in inp.storages if s.stale],
        zwang_aktiv=inp.battery_force and bool(zwang_offen),
    )

    def _freischwimm_probe(rest: float) -> None:
        """Den ungedeckten Rest probeweise den verriegelten Speichern zuteilen.

        Dieselbe Anteil-Formel wie für `known` (Energie über der Reserve,
        Kaltreserve-Regel), gerechnet auf den letzten bekannten SoC. Folgt ein
        Speicher, tickt sein SoC, und die frische Meldung entriegelt ihn im
        Coordinator — kein zweiter Eingang am Latch.
        """
        if rest < CONTROL_MIN_SETPOINT_W:
            return
        stale_anteile = [
            (
                s,
                max(0.0, (s.soc - s.reserve_soc) / 100 * s.capacity_kwh)
                if (not s.cold_reserve or reserve_aktiv)
                else 0.0,
            )
            for s in inp.storages
            if s.stale and s.soc is not None
        ]
        probe = _verteile_entladen(stale_anteile, rest)
        for name, watt in probe.items():
            if watt > 0:
                ctrl.zuteilung.append(StorageSetpoint(name=name, watt=round(watt)))
                ctrl.probe_namen.append(name)

    if not known:
        # Alle Speicher abgemeldet. NICHT auf `None` fallen: Ohne Empfehlung
        # schriebe der Actuator gar nichts, und der zuletzt kommandierte
        # Sollwert bliebe blind stehen (genau das, wogegen `release_battery`
        # existiert). Grundsätzlich passiv — 0 W an alle, der Grund steht in
        # `abgemeldet_namen`.
        #
        # Aber mit Freischwimm-Probe, sobald das Haus Bezug hat: Bis
        # 22.09.2026 stand hier ein nacktes 0 W an alle, und die Probe gab es
        # nur im Entlade-Zweig darunter, den ohne `known` nichts erreicht. Ein
        # ruhender Speicher an einer push-basierten Integration meldet aber
        # nichts, solange sich sein SoC nicht ändert — ohne Befehl kam also
        # nie die frische Meldung, die ihn entriegelt. Die Sperre hielt sich
        # selbst bis zum nächsten Neustart, während das Haus aus dem Netz zog:
        # das Schadensbild vom 17.08.2026, nur für alle Speicher zugleich. Der
        # ganze Wunsch ist hier Rest, weil kein bekannter Speicher etwas trägt.
        #
        # Nicht unter Speicher-Zwangsladung: Der Betreiber hat Laden bestellt,
        # und ein Entladebefehl wäre das Gegenteil davon.
        if soll_wunsch > CONTROL_DEADBAND_W and not inp.battery_force:
            _freischwimm_probe(soll_wunsch)
        if ctrl.probe_namen:
            ctrl.modus = "entladen"
        probiert = {z.name for z in ctrl.zuteilung}
        ctrl.zuteilung.extend(
            StorageSetpoint(name=s.name, watt=0.0)
            for s in inp.storages
            if s.name not in probiert
        )
        return ctrl

    if soll > CONTROL_DEADBAND_W:
        ctrl.modus = "entladen"
        # Kaltreserve als Leistungsreserve (T-002): Kämen die übrigen Speicher
        # zusammen an ihre Auslastungsgrenze, springt die Reserve mit ein —
        # zusätzlich zur Energie-Seite oben (mittlerer SoC). Nur Speicher mit
        # verfügbarer Energie zählen zur Summe: einer an der Reserve-Grenze
        # trägt nichts und darf die Schwelle nicht nach oben schieben.
        primaer_max = _primaer_max_entladung(known)
        res.flags.kaltreserve_leistung = _latch(
            inp.flags.kaltreserve_leistung,
            soll / primaer_max if primaer_max > 0 else None,
            on=RESERVE_POWER_ON,
            off=RESERVE_POWER_OFF,
        )
        # Bewusst derselbe Name wie oben (dort nur die Energie-Seite): ab hier
        # gilt die Reserve als beteiligt, wenn EINE der beiden Seiten greift —
        # `_freischwimm_probe` liest den Namen zur Laufzeit und muss genau
        # diesen kombinierten Wert sehen.
        reserve_aktiv = res.flags.kaltreserve or res.flags.kaltreserve_leistung
        ctrl.reserve_aktiv = reserve_aktiv
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
        # Betriebsart der Zuteilung: greedy (ein Akku trägt, Normalfall) oder
        # parallel (alle tragen anteilig). Entschieden allein daran, ob ein
        # einzelner Speicher das Soll unter seiner Auslastungsgrenze (85 %,
        # Wärme — Zendure-Verteilstrategie, T-002) tragen kann — kann er es
        # nicht, läuft der nächste mit. Schmitt-Trigger, weil die
        # Betriebsart an der Grenze sonst flattert; Schwellen und Messung bei
        # CONTROL_PARALLEL_ON/OFF. Nur teilnehmende Einheiten zählen: ein
        # Speicher an der Reserve (anteil 0) trägt nichts und darf die Grenze
        # nicht nach oben schieben.
        primaer = [(s, a) for s, a in anteile if not s.cold_reserve]
        reserve_teil = [(s, a) for s, a in anteile if s.cold_reserve]
        groesstes = max(
            (s.max_discharge_w for s, a in (primaer or anteile) if a > 0),
            default=0.0,
        )
        res.flags.parallel_entladen = _latch(
            inp.flags.parallel_entladen,
            soll / groesstes if groesstes > 0 else None,
            on=CONTROL_PARALLEL_ON,
            off=CONTROL_PARALLEL_OFF,
        )
        ctrl.parallel_aktiv = res.flags.parallel_entladen
        ctrl.zuteilung = _entlade_zuteilung(
            primaer, reserve_teil, primaer_max, soll, res.flags
        )
        # Freischwimm-Probe (Frage 2, Aufgabe „Speicher-Selbstsperre", Git 129880c):
        # `known` ist oben zuerst und unverändert zugeteilt worden — Invariante,
        # nicht verhandelbar. Bleibt danach ungedeckter Rest, bekommen die
        # verriegelten (`stale`) Speicher probeweise GENAU diesen Rest, mit
        # derselben Anteil-Formel wie `known` (Energie über der Reserve,
        # Kaltreserve-Regel), gerechnet auf ihren letzten bekannten SoC.
        # Absichtlich das Rest-Kriterium, nicht „known am Deckel": Ein
        # bekannter Speicher an der Reserve hat anteil ≤ 0, bekommt 0 W,
        # zählt aber weiter in max_ent — ein Deckel-Kriterium (Σ Zuteilung ≥
        # max_ent) verfehlt genau diesen Fall.
        _freischwimm_probe(soll_wunsch - sum(z.watt for z in ctrl.zuteilung))
    elif soll < -CONTROL_DEADBAND_W:
        ctrl.modus = "laden"
        # Freie Kapazität bis zum Ladedeckel (tagsüber < 100 %, zum Abend voll)
        # — wer mehr Platz hat, bekommt mehr. Speicher über dem Deckel bekommen
        # 0 (kein Zwangsentladen).
        deckel = res.lade_deckel_soc if res.lade_deckel_soc is not None else 100.0

        def _anteile(
            grenze: float, *, reserve_eigenes_ziel: bool = False
        ) -> list[tuple[StorageState, float]]:
            # Unter Zwang bleiben Speicher am physischen Ladeende außen vor:
            # Ihre rechnerisch freie Kapazität (99 → 100 %) ist im CV-Taper
            # nicht mehr abrufbar, ein Sollwert darauf erzeugt nur eine
            # Lade-Quittung, die niemand einlösen kann. Ohne Zwang bleibt es
            # beim Deckel — dort ist der Rest echter Überschuss, der sonst ins
            # Netz ginge, und der darf auch die letzte Kilowattstunde füllen.
            nur_offene = inp.battery_force
            return [
                (
                    s,
                    0.0
                    if nur_offene and s.soc >= SPEICHER_VOLL_SOC
                    else max(
                        0.0,
                        (
                            # Kaltreserve: ihr eigenes Ziel statt des Deckels;
                            # beim Laden statt Einspeisen (grenze 100) bis 100.
                            (max(s.reserve_target_soc, grenze)
                             if reserve_eigenes_ziel and s.cold_reserve
                             else grenze)
                            - s.soc
                        ) / 100 * s.capacity_kwh,
                    ),
                )
                for s in known
            ]

        def _lade_zuteilung(grenze: float) -> list[StorageSetpoint]:
            # Kaltreserve zuerst und bis zu ihrem Ziel-SoC (T-002, T-007): Der Notfallakku muss
            # bereitstehen, also gilt für ihn weder der Ladedeckel noch der
            # Nachrang hinter den übrigen. Was er nicht aufnimmt, geht an die
            # übrigen Speicher, dort wie bisher bis zum Deckel.
            reserve = [
                p for p in _anteile(grenze, reserve_eigenes_ziel=True)
                if p[0].cold_reserve
            ]
            primaer = [p for p in _anteile(grenze) if not p[0].cold_reserve]
            gesamt = -soll
            z_res = _verteile(reserve, gesamt, laden=True) if reserve else []
            rest = max(0.0, gesamt - sum(z.watt for z in z_res))
            z_pri = _verteile(primaer, rest, laden=True) if primaer else []
            nach_name = {z.name: z for z in z_res + z_pri}
            return [nach_name[s.name] for s in known]

        ctrl.zuteilung = _lade_zuteilung(deckel)
        # "Bevor eingespeist wird, immer Akkus laden": Der Deckel ordnet den
        # Vorrang (erst die Lasten, dann der Akku), er verschenkt aber keine
        # Energie. An dieser Stelle steht der Saldo, der NACH den Lasten übrig
        # bleibt — bekommt der Deckel davon nicht alles unter, ginge der Rest
        # ins Netz. Dann wird bis 100 % geladen: einspeisen ist die schlechtere
        # Verwendung. Betrifft auch gemischte Stände (ein Speicher am Deckel,
        # ein anderer an seiner Ladegrenze). Der Deckel im Plan bleibt dabei
        # stehen — er ist die Absicht, nicht der Befehl; das Überfahren meldet
        # `laden_statt_einspeisen`, und der Actuator hebt daraufhin auch den
        # geräteseitigen Ziel-SoC an (sonst hielte der dagegen).
        gestellt = sum(z.watt for z in ctrl.zuteilung)
        if deckel < 100.0 and gestellt < -soll - CONTROL_MIN_SETPOINT_W:
            ohne_deckel = _lade_zuteilung(100.0)
            if sum(z.watt for z in ohne_deckel) > gestellt:
                ctrl.zuteilung = ohne_deckel
                ctrl.laden_statt_einspeisen = True
    else:
        ctrl.zuteilung = [StorageSetpoint(name=s.name, watt=0.0) for s in known]
    return ctrl
