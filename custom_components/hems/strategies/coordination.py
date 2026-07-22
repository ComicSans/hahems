"""Koordinations-Domäne: Ladevorrang zwischen Akku und modulierbaren Lasten.

Historisch bediente sich der Lasten-Regler (loads) immer zuerst am Überschuss —
er rechnete die Akkuleistung heraus und der Speicher-Regler bekam nur den Rest.
Der konfigurierte `priority_mode` wirkte dadurch nur auf den Empfehlungstext,
nicht auf die tatsächliche Aufteilung: das E-Auto gewann die Ladehoheit immer.

Diese Naht macht den Vorrang echt. Sie berechnet, wie viel Überschuss der Akku
VOR der Wallbox reservieren darf. Der Betrag wird im Lasten-Regler von dessen
verfügbarem Überschuss abgezogen; den reservierten Teil holt sich der Speicher-
Regler anschließend über sein normales Saldo-Residuum. Die Regelmathematik
beider Regler bleibt unangetastet — es ändert sich nur, wie viel Überschuss der
Lasten-Regler sieht.
"""
from __future__ import annotations

from ..const import PRIORITY_AUTO, PRIORITY_BATTERY_FIRST
from .types import PlanInput, PlanResult


def akku_hat_vorrang(inp: PlanInput) -> bool:
    """Ob der Akku beim Laden Vorrang vor den modulierbaren Lasten hat.

    battery_first immer; auto genau dann, wenn der Tagesertrag knapp ist
    (knapp-Latch — dieselbe Bedingung, nach der die Empfehlung schon heute den
    Akku vor das Auto stellt). ev_first nie.
    """
    if inp.priority_mode == PRIORITY_BATTERY_FIRST:
        return True
    if inp.priority_mode == PRIORITY_AUTO:
        return inp.flags.knapp
    return False


def akku_ladereservierung(inp: PlanInput, res: PlanResult) -> float:
    """Überschuss (W), den der Akku vor der Wallbox reservieren darf.

    Bei Akku-Vorrang ist das die freie Ladeleistung aller Speicher UNTER dem
    Ladedeckel (Summe ihrer max. Ladeleistung). Ist der Akku am Deckel oder hat
    er keinen Vorrang, wird nichts reserviert und die Wallbox bekommt den vollen
    Überschuss. Der Lasten-Regler deckelt diese Reservierung zusätzlich so, dass
    ein bereits laufendes Auto sein Minimum behält (siehe loads).
    """
    if not inp.modulateds or not akku_hat_vorrang(inp):
        return 0.0
    deckel = res.lade_deckel_soc if res.lade_deckel_soc is not None else 100.0
    return sum(
        s.max_charge_w
        for s in inp.storages
        if s.soc is not None and s.soc < deckel
    )
