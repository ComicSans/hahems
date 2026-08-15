"""Speicher-Domäne: Saldo-Regelung und Ladestrategie über den Tag (Ladedeckel).

Live-Zuteilung der Lade-/Entladeleistung je Speicher. Laden verteilt parallel
(proportional zur freien Kapazität), Entladen greedy mit Auswahl-Hysterese.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..const import (
    CONTROL_DEADBAND_W,
    CONTROL_GAIN_CHARGE,
    CONTROL_GAIN_DISCHARGE,
    CONTROL_GAIN_FACTORS,
    CONTROL_LEAD_HYST_SOC,
    CONTROL_LEAD_POWER_W,
    CONTROL_MIN_SETPOINT_W,
    CONTROL_TARGET_OFFSET_W,
    CONTROL_ZERO_FEEDIN_OFFSET_W,
    CONTROL_GAIN_EMERGENCY,
    GOAL_ZERO_FEEDIN,
    RESERVE_SOC_OFF,
    RESERVE_SOC_ON,
    STORAGE_AFTERNOON_FROM_H,
    STORAGE_FULL_BY_LEAD_H,
    STORAGE_MORNING_UNTIL_H,
    STORAGE_RAMP_SAFETY,
)
from .types import (
    ChargeRamp,
    ControlResult,
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

    `sofort_voll` hebt die Pause auf: wer heute noch voll werden muss (oder als
    Notstromreserve bereitstehen soll), hat keine drei Stunden zu verschenken.
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
    if not known:
        abgemeldet = [s.name for s in inp.storages if s.stale]
        if not abgemeldet:
            # Kein Speicher hat je einen SoC geliefert: unverändert keine
            # Empfehlung — daran hängt nichts, was abgeschaltet gehörte.
            return None
        # Alle Speicher stumm — bei drei Geräten an einem MQTT-Pfad ein
        # Broker-Neustart, nicht die Ausnahme. Hier NICHT auf `None` fallen:
        # Ohne Empfehlung schreibt der Actuator gar nichts, und der zuletzt
        # kommandierte Sollwert bliebe blind stehen (genau das, wogegen
        # `release_battery` existiert). Stattdessen eine ausdrücklich passive
        # Empfehlung — 0 W an alle, und der Grund steht in `abgemeldet_namen`,
        # damit Sensor und Log den Zustand auch dann noch benennen können.
        return ControlResult(
            modus="pausiert",
            fehler_w=0.0,
            soll_w=0.0,
            zuteilung=[StorageSetpoint(name=s.name, watt=0.0) for s in inp.storages],
            abgemeldet_namen=abgemeldet,
        )

    # Kaltreserve-Hysterese über den mittleren SoC der Nicht-Reserve-Speicher.
    primary_socs = [s.soc for s in known if not s.cold_reserve]
    res.flags.kaltreserve = _latch(
        inp.flags.kaltreserve,
        sum(primary_socs) / len(primary_socs) if primary_socs else None,
        on=RESERVE_SOC_ON,
        off=RESERVE_SOC_OFF,
    )
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
        # Notstromreserve: beim Laden die volle Schrittweite, unabhängig von der
        # eingestellten Aggressivität — der gemächliche Lade-Gain ist Schonung,
        # und die ist hier ausdrücklich nicht gefragt.
        if inp.emergency_reserve and fehler_w <= 0:
            return CONTROL_GAIN_EMERGENCY
        basis = CONTROL_GAIN_DISCHARGE if fehler_w > 0 else CONTROL_GAIN_CHARGE
        return min(1.0, basis * faktor)

    gain = _gain(fehler)
    max_ent = sum(s.max_discharge_w for s in known)
    max_lad = sum(s.max_charge_w for s in known)
    soll = max(-max_lad, min(bat_ist + fehler * gain, max_ent))

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

    ctrl = ControlResult(
        modus="pausiert",
        fehler_w=round(fehler, 0),
        soll_w=round(soll, 0),
        reserve_aktiv=reserve_aktiv,
        reserve_namen=[s.name for s in inp.storages if s.cold_reserve],
        abgemeldet_namen=[s.name for s in inp.storages if s.stale],
    )

    def _verteile_entladen(
        anteile: list[tuple[StorageState, float]], gesamt: float
    ) -> dict[str, float]:
        """Entladeleistung greedy zuteilen: die Einheit mit der meisten
        verfügbaren Energie zuerst voll ausschöpfen, dann die nächste. Bewusst
        NICHT proportional zerstäuben — bei N Einheiten läge sonst jeder Anteil
        unter dem Mindest-Setpoint und würde auf 0 gerundet (Totzone ~N×min).
        Das Bündeln reduziert zugleich das Schütz-/Umschalt-Flattern: es
        entlädt möglichst nur ein Akku zur Zeit (Verschleiß der Akku-Elektronik).

        Auswahl-Hysterese: Der aktuell arbeitende Speicher (gemessene Leistung
        über LEAD_POWER_W) behält in der Rangfolge einen SoC-Vorsprung von
        LEAD_HYST_SOC, damit die Führung nicht bei jedem minimalen SoC-Crossover
        rotiert. Der Bonus verschiebt NUR die Reihenfolge; die Teilnahme-Schranke
        unten prüft weiter den rohen `anteil` (Reserve-Grenze, Kaltreserve-
        Ausschluss bleiben unberührt). Reicht ein Speicher nicht (soll > seine
        Grenze), füllt die Schleife den nächsten weiter auf."""

        def _rang(paar: tuple[StorageState, float]) -> float:
            s, anteil = paar
            p = s.power_w or 0.0
            arbeitet = p > CONTROL_LEAD_POWER_W
            bonus = (
                CONTROL_LEAD_HYST_SOC / 100.0 * s.capacity_kwh
                if arbeitet and anteil > 0
                else 0.0
            )
            return anteil + bonus

        rest = gesamt
        watts: dict[str, float] = {s.name: 0.0 for s, _ in anteile}
        for s, anteil in sorted(anteile, key=_rang, reverse=True):
            watt = min(rest, s.max_discharge_w)
            if anteil <= 0 or watt < CONTROL_MIN_SETPOINT_W:
                continue
            watts[s.name] = watt
            rest -= watt
        return watts

    def _verteile_laden(
        anteile: list[tuple[StorageState, float]], gesamt: float
    ) -> dict[str, float]:
        """Ladeleistung PARALLEL auf mehrere Akkus verteilen — proportional zur
        freien Kapazität (gleicht die SoCs an, hält die C-Rate je Akku niedrig),
        aber nur auf so viele Einheiten, dass jeder Anteil ≥ Mindest-Setpoint
        bleibt. Sonst fiele bei N Einheiten jeder Anteil unter den Mindestwert
        und würde auf 0 gerundet (Totzone ~N×min) — der Überschuss liefe trotz
        freiem Akku ins Netz. Reicht die Leistung nur für weniger Einheiten,
        fällt die Verteilung schrittweise auf die Akkus mit der meisten freien
        Kapazität zurück (leerste zuerst). Anders als beim Entladen ist paralleles
        Laden gewollt: mehrere Akkus gleichzeitig laden ist schonender und
        schneller, und Ladeflattern ist unkritisch (kein Richtungswechsel)."""

        def _fuellen(einheiten: list[tuple[StorageState, float]]) -> dict[str, float]:
            # Proportional zur freien Kapazität, iterativ auf max_charge_w
            # gedeckelt: was eine gedeckelte Einheit nicht aufnimmt, fließt an
            # die übrigen.
            soll = {s.name: 0.0 for s, _ in einheiten}
            aktiv = [(s, f) for s, f in einheiten if f > 0]
            rest = gesamt
            while rest > 1e-6 and aktiv:
                frei_summe = sum(f for _, f in aktiv)
                basis = rest
                naechste: list[tuple[StorageState, float]] = []
                gedeckelt = False
                for s, f in aktiv:
                    zusatz = basis * f / frei_summe
                    platz = s.max_charge_w - soll[s.name]
                    if zusatz >= platz:
                        soll[s.name] += platz
                        rest -= platz
                        gedeckelt = True
                    else:
                        soll[s.name] += zusatz
                        rest -= zusatz
                        naechste.append((s, f))
                aktiv = naechste
                if not gedeckelt:
                    break
            return soll

        # Kandidaten mit freier Kapazität, leerste (meiste freie kWh) zuerst.
        kandidaten = sorted(
            [(s, a) for s, a in anteile if a > 0], key=lambda p: p[1], reverse=True
        )
        watts: dict[str, float] = {s.name: 0.0 for s, _ in anteile}
        while kandidaten:
            soll = _fuellen(kandidaten)
            positive = [w for w in soll.values() if w > 0]
            # Kleinster gestellter Anteil zu klein? Schwächste Einheit (wenigste
            # freie Kapazität = letzte im sortierten Feld) fallen lassen und
            # erneut auf die übrigen verteilen.
            if (
                positive
                and min(positive) < CONTROL_MIN_SETPOINT_W
                and len(kandidaten) > 1
            ):
                kandidaten.pop()
                continue
            for name, w in soll.items():
                watts[name] = w if w >= CONTROL_MIN_SETPOINT_W else 0.0
            break
        return watts

    def _verteile(
        anteile: list[tuple[StorageState, float]], gesamt: float, laden: bool
    ) -> list[StorageSetpoint]:
        """Gesamtleistung je Speicher zuteilen. Laden verteilt parallel
        (proportional zur freien Kapazität), Entladen greedy mit Auswahl-
        Hysterese (ein Akku zur Zeit, gegen Verschleiß). Ein Rest unter dem
        Mindest-Setpoint bleibt ungestellt — konservativ: nie mehr kommandieren
        als der gain-/offset-gedämpfte Zielwert hergibt (kein Netzbezug)."""
        watts = (
            _verteile_laden(anteile, gesamt)
            if laden
            else _verteile_entladen(anteile, gesamt)
        )
        return [
            StorageSetpoint(name=s.name, watt=round(watts[s.name]))
            for s, _a in anteile
        ]

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
        # Freie Kapazität bis zum Ladedeckel (tagsüber < 100 %, zum Abend voll)
        # — wer mehr Platz hat, bekommt mehr. Speicher über dem Deckel bekommen
        # 0 (kein Zwangsentladen).
        deckel = res.lade_deckel_soc if res.lade_deckel_soc is not None else 100.0

        def _anteile(grenze: float) -> list[tuple[StorageState, float]]:
            return [
                (s, max(0.0, (grenze - s.soc) / 100 * s.capacity_kwh)) for s in known
            ]

        ctrl.zuteilung = _verteile(_anteile(deckel), -soll, laden=True)
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
            ohne_deckel = _verteile(_anteile(100.0), -soll, laden=True)
            if sum(z.watt for z in ohne_deckel) > gestellt:
                ctrl.zuteilung = ohne_deckel
                ctrl.laden_statt_einspeisen = True
    else:
        ctrl.zuteilung = [StorageSetpoint(name=s.name, watt=0.0) for s in known]
    return ctrl
