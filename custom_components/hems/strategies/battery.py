"""Speicher-Domäne: Saldo-Regelung und Akku-Schonung (Ladedeckel).

Live-Zuteilung der Lade-/Entladeleistung je Speicher. Laden verteilt parallel
(proportional zur freien Kapazität), Entladen greedy mit Auswahl-Hysterese.
"""
from __future__ import annotations

from datetime import datetime

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
    GOAL_ZERO_FEEDIN,
    RESERVE_SOC_OFF,
    RESERVE_SOC_ON,
    STORAGE_DAY_HOLD_SOC,
    STORAGE_FULL_CHARGE_LEAD_H,
)
from .types import (
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


def _lade_deckel_soc(inp: PlanInput, voll_noetig: bool, t: datetime) -> float:
    """Zeitabhängiger Ladedeckel (SoC-%) zum Zeitpunkt t — Akku-Schonung.

    Tagsüber wird nur bis STORAGE_DAY_HOLD_SOC geladen (kalendarische Alterung
    ist bei hohem SoC am größten). Erst in den letzten STORAGE_FULL_CHARGE_LEAD_H
    vor Sonnenuntergang steigt der Deckel linear auf 100 %, sodass der Speicher
    ~zum Sonnenuntergang voll für die Nacht ist und möglichst wenig Zeit bei
    100 % verbringt. `voll_noetig` (Ziel/morgen knapp/heute zu wenig Ertrag zum
    späteren Nachladen — sowie Nacht, dann ohnehin kein Überschuss) hebt den
    Deckel sofort auf 100 %: Nachtdeckung geht vor Schonung.

    Nur eine Ladeobergrenze, kein Entladebefehl — liegt der SoC bereits über dem
    Deckel, bleibt er stehen (die Regelung lädt ihn nur nicht weiter).
    """
    if voll_noetig:
        return 100.0
    h_bis_sonnenuntergang = (inp.sunset - t).total_seconds() / 3600
    if h_bis_sonnenuntergang <= 0:
        return 100.0
    if h_bis_sonnenuntergang >= STORAGE_FULL_CHARGE_LEAD_H:
        return STORAGE_DAY_HOLD_SOC
    anteil = 1.0 - h_bis_sonnenuntergang / STORAGE_FULL_CHARGE_LEAD_H
    return STORAGE_DAY_HOLD_SOC + anteil * (100.0 - STORAGE_DAY_HOLD_SOC)


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
    # Basis-Gain (asymmetrisch: schnell gegen Bezug, gemächlich beim Laden),
    # skaliert mit der Regel-Aggressivität. Auf 1.0 gedeckelt: ein Gain von 1
    # korrigiert den Fehler bereits in einem Schritt vollständig; darüber würde
    # der Proportionalregler überschwingen. Der 60-s-Takt bleibt unberührt —
    # aggressiver heißt größerer Schritt, nicht häufigeres Umschalten.
    basis_gain = CONTROL_GAIN_DISCHARGE if fehler > 0 else CONTROL_GAIN_CHARGE
    faktor = CONTROL_GAIN_FACTORS.get(inp.gain_level, 1.0)
    gain = min(1.0, basis_gain * faktor)
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
        gain_roh = min(
            1.0,
            (CONTROL_GAIN_DISCHARGE if fehler_roh > 0 else CONTROL_GAIN_CHARGE)
            * faktor,
        )
        soll = max(-max_lad, min(0.0, max(soll, bat_ist + fehler_roh * gain_roh)))

    ctrl = ControlResult(
        modus="pausiert",
        fehler_w=round(fehler, 0),
        soll_w=round(soll, 0),
        reserve_aktiv=reserve_aktiv,
        reserve_namen=[s.name for s in inp.storages if s.cold_reserve],
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
        # Freie Kapazität bis zum Ladedeckel (Akku-Schonung: tagsüber < 100 %,
        # zum Abend voll) — wer mehr Platz hat, bekommt mehr. Speicher über dem
        # Deckel bekommen 0 (kein Zwangsentladen; Überschuss geht ggf. ins Netz).
        deckel = res.lade_deckel_soc if res.lade_deckel_soc is not None else 100.0
        anteile = [
            (s, max(0.0, (deckel - s.soc) / 100 * s.capacity_kwh)) for s in known
        ]
        ctrl.zuteilung = _verteile(anteile, -soll, laden=True)
    else:
        ctrl.zuteilung = [StorageSetpoint(name=s.name, watt=0.0) for s in known]
    return ctrl
