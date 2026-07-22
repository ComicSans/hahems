"""Schaltlast-Domäne: schaltbare Lasten (nur an/aus) überschussgesteuert.

HEMS schaltet schaltbare Lasten ein, solange der Überschuss ihre erwartete
Leistung deckt, und aus, wenn er fehlt. Prioritätsreihenfolge bei knappem
Überschuss:

  1. Modulierbare Lasten geben ihr Headroom auf (drosseln herunter),
  2. schaltbare Lasten (niedrigste Priorität zuerst) werden abgeschaltet,
  3. der Akku pausiert zuletzt.

Umsetzung: die Schaltentscheidung bekommt den Überschuss VOR dem Headroom der
modulierbaren Lasten (nur deren Minima sind geschützt). Die daraus folgende
Leistungs-Differenz (`delta_w`) wird dem modulierbaren Regler vom Überschuss
abgezogen — er drosselt entsprechend herunter und gibt so die Leistung frei,
die die schaltbaren Lasten ziehen.

Anti-Takt: Mindestlaufzeit (`min_on`) hält eine Last an, Mindestpause
(`min_off`) hält sie aus, `max_block` erzwingt ein Einschalten, wenn HEMS sie
zu lange ausgehalten hat (z. B. eine Umwälzpumpe, die laufen muss).
"""
from __future__ import annotations

from ..const import DEFAULT_SWITCHABLE_EXPECTED_W, SWITCH_SURPLUS_MARGIN_W
from .types import PlanInput, PlanResult, SwitchableResult, SwitchableSetpoint


def _erwartet_w(s) -> float:
    return s.erwartet_w if s.erwartet_w and s.erwartet_w > 0 else DEFAULT_SWITCHABLE_EXPECTED_W


def switchable_control(inp: PlanInput, res: PlanResult) -> SwitchableResult | None:
    """An/Aus-Empfehlung je schaltbarer Last berechnen."""
    loads = inp.switchables
    if not loads or inp.saldo_w is None:
        return None

    mess_mod = sum(m.power_w or 0.0 for m in inp.modulateds)
    mess_sw = sum(s.power_w or 0.0 for s in loads)
    bat_ist = sum(s.power_w for s in inp.storages if s.power_w is not None)
    # Überschuss, wenn alle steuerbaren Lasten aus wären und der Akku ruht.
    frei = -(inp.saldo_w - mess_mod - mess_sw + bat_ist)
    # Minima bereits laufender modulierbarer Lasten schützen — die drosseln nur
    # ihr Headroom weg, ihr Minimum bleibt (ein ladendes Auto behält seine 6 A).
    mod_minima = sum(m.min_w for m in inp.modulateds if m.ist_an)
    budget = frei - mod_minima
    margin = SWITCH_SURPLUS_MARGIN_W

    def _locked_on(s) -> bool:
        return s.ist_an and s.an_seit_s is not None and s.an_seit_s < s.min_on_min * 60

    def _locked_off(s) -> bool:
        return (
            not s.ist_an
            and s.aus_seit_s is not None
            and s.aus_seit_s < s.min_off_min * 60
        )

    def _block_ueberschritten(s) -> bool:
        return (
            not s.ist_an
            and s.aus_seit_s is not None
            and s.aus_seit_s >= s.max_block_min * 60
        )

    # Wichtigste Priorität (kleinste Zahl) zuerst; bei Gleichstand laufende
    # Lasten vor wartenden (Hysterese auf Flottenebene: Läufer bleiben an).
    reihenfolge = sorted(loads, key=lambda s: (s.priority, not s.ist_an))

    lasten: list[SwitchableSetpoint] = []
    soll_w = 0.0
    for s in reihenfolge:
        erwartet = _erwartet_w(s)
        if _block_ueberschritten(s):
            an, grund = True, "max_block erreicht"
        elif _locked_on(s):
            an, grund = True, "min_on gehalten"
        elif _locked_off(s):
            an, grund = False, "min_off gehalten"
        else:
            # Hysterese: einschalten ab erwartet+Marge, anlassen bis erwartet−Marge.
            schwelle = erwartet - margin if s.ist_an else erwartet + margin
            if budget >= schwelle:
                an, grund = True, "Überschuss deckt Last"
            else:
                an, grund = False, "Überschuss zu klein"
        if an:
            budget -= erwartet
            soll_w += erwartet
        lasten.append(SwitchableSetpoint(name=s.name, an=an, id=s.id, grund=grund))

    return SwitchableResult(
        lasten=lasten, soll_w=round(soll_w), delta_w=round(soll_w - mess_sw)
    )
