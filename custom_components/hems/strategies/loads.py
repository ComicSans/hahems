"""Lasten-Domäne: Überschussregelung modulierbarer Lasten (Wallboxen).

HEMS besitzt den Überschussstrom vor dem Akku und verteilt ihn über die Lasten;
bei Defizit weichen sie vor der Akku-Entladung.
"""
from __future__ import annotations

import math

from ..const import (
    EV_DEMAND_GRACE_S,
    EV_SURPLUS_MARGIN_W,
    EV_VOLTAGE_PER_PHASE_V,
)
from .types import (
    EvControlResult,
    ModulatedSetpoint,
    ModulatedState,
    PlanInput,
    PlanResult,
)


# Rotations-Malus (kWh) für eine beobachtet-leere Last: groß genug, um jede
# realistische Tagesenergie zu überbieten, damit eine leere Last in der
# Rangfolge stets hinter jede nicht-leere fällt — aber endlich, damit sie ohne
# Konkurrenz (einzige Last, oder echte Restkapazität) weiterläuft.
EV_LEER_PENALTY_KWH = 1_000_000.0


def _ampere(watt: float, m: ModulatedState) -> float:
    """Sollleistung → Ladestrom, konservativ abgerundet (nie mehr ziehen als
    der Überschuss hergibt), geklemmt auf [min_a, max_a]."""
    volt = m.phases * EV_VOLTAGE_PER_PHASE_V
    return float(max(m.min_a, min(math.floor(watt / volt), m.max_a)))


def _modulated_control(
    inp: PlanInput, res: PlanResult, lade_reservierung: float = 0.0
) -> EvControlResult | None:
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
    # Akku-Ladevorrang (coordination): den reservierten Überschuss abziehen,
    # damit der Speicher-Regler ihn über sein Saldo-Residuum bekommt. Aber nur
    # den Teil OBERHALB der Minima bereits laufender Lasten — ein ladendes Auto
    # behält sein Minimum und wird nie abgeregelt.
    if lade_reservierung > 0:
        laufende_minima = sum(m.min_w for m in loads if m.ist_an)
        reservierbar = max(0.0, avail_w - laufende_minima)
        avail_w -= min(lade_reservierung, reservierbar)
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

    def _locked_off(m: ModulatedState) -> bool:
        """Innerhalb der Mindestpause aus (Schützschutz, darf nicht sofort wieder
        an). Greift nur mit Schalter — ohne schaltet HEMS die Last nicht, dann
        bleibt aus_seit_s None."""
        return (
            not m.ist_an
            and m.aus_seit_s is not None
            and m.aus_seit_s < m.min_off_min * 60
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
            # Mindestpause nach dem Abschalten: eine gerade abgeschaltete Last
            # bleibt aus, bis die Pause abgelaufen ist — auch wenn wieder
            # Überschuss anliegt (Schützschutz gegen zu häufiges Takten). Der
            # frei bleibende Überschuss geht solange in den Akku.
            if _locked_off(m):
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
