"""Koordinations-Domäne: Ladevorrang zwischen Akku und modulierbaren Lasten.

Historisch bediente sich der Lasten-Regler (loads) immer zuerst am Überschuss —
er rechnete die Akkuleistung heraus und der Speicher-Regler bekam nur den Rest.
Der konfigurierte `priority_mode` wirkte dadurch nur auf den Empfehlungstext,
nicht auf die tatsächliche Aufteilung: das E-Auto gewann die Ladehoheit immer.

Diese Naht macht den Vorrang echt. Sie berechnet, wie viel Überschuss der Akku
VOR der Wallbox reservieren darf. Der Betrag wird im Lasten-Regler von dessen
verfügbarem Überschuss abgezogen; den reservierten Teil holt sich der Speicher-
Regler anschließend über sein normales Saldo-Residuum.

Am 23.08.2026 hat der Vorrang seine Ausnahmen verloren, und dabei blieb die
Regelmathematik nicht mehr unangetastet — die Naht reicht seitdem in beide
Regler hinein:

- Die Reservierung hängt am tatsächlichen Ladebedarf statt am SoC-Abstand
  (`akku_ladereservierung`). Ein fertiger Akku reserviert nichts.
- Der Lasten-Regler zieht sie voll ab, auch von den Minima laufender Lasten
  (`_modulated_control`). Fängt der Akku an zu laden, weicht das Auto.
- Der Speicher-Regler entlädt nicht mehr in die Wallbox (`_storage_control`).
  Vorrang, der beim Entladen wieder abgegeben wird, ist keiner.

Reihenfolge: Die Reservierung entsteht VOR der Speicher-Regelung (siehe
`compute_plan`) und kann deren Zuteilung deshalb nicht kennen. Sie rechnet
bewusst gegen `max_charge_w` und nicht gegen einen erwarteten Sollwert — was der
Regler daraus macht, entscheidet er selbst, einen Takt später.
"""
from __future__ import annotations

from ..actuation import ladeauftrag_in_frist_erfuellbar
from ..const import PRIORITY_AUTO, PRIORITY_BATTERY_FIRST, PRIORITY_EV_FIRST
from .types import PlanInput, PlanResult


def akku_hat_vorrang(inp: PlanInput) -> bool:
    """Ob der Akku beim Laden Vorrang vor den modulierbaren Lasten hat.

    Notstromreserve überstimmt die Einstellung: eine Reserve, die hinter dem
    Auto ansteht, ist im Ausfall keine. Sonst battery_first immer; ev_first nie;
    auto ebenfalls immer — wer keine Priorität setzt, will den Akku zuerst voll
    haben (entschieden am 23.08.2026).

    Bis dahin entschied im Auto-Modus der `knapp`-Latch: Vorrang nur, wenn der
    Restertrag für Akku UND Auto nicht reicht. Das las den Vorrang als Notnagel
    für schlechte Tage statt als Grundhaltung, und an einem Tag mit reichlich
    Ertrag lud das Auto den Überschuss weg, den der Akku für die Nacht braucht.
    Die Reservierung selbst kostet nichts mehr, seit sie am tatsächlichen
    Ladebedarf hängt (`akku_ladereservierung`): Ein Akku ohne Bedarf reserviert
    nichts, unabhängig vom Vorrang. Vorrang heißt jetzt „zuerst bedient", nicht
    mehr „blockiert vorsorglich".
    """
    if inp.emergency_reserve:
        return True
    if inp.priority_mode == PRIORITY_EV_FIRST:
        return False
    return inp.priority_mode in (PRIORITY_BATTERY_FIRST, PRIORITY_AUTO)


def akku_ladereservierung(inp: PlanInput, res: PlanResult) -> float:
    """Überschuss (W), den der Akku vor der Wallbox reservieren darf.

    Reserviert wird nur für Speicher, die den reservierten Betrag auch längere
    Zeit aufnehmen können. Maßstab ist `ladeauftrag_in_frist_erfuellbar` — genau
    das Prädikat, mit dem der Actuator seit dem 19.08.2026 „fertig" von
    „antwortet nicht" trennt: Ist die freie Kapazität bis zum Ladedeckel kleiner
    als das, was die volle Ladeleistung in der Frist liefern würde, ist der
    Speicher fertig und reserviert nichts.

    Die Frist ist hier die längste Mindestlaufzeit der wartenden Lasten. Das ist
    der Preis, den die Reservierung dem Auto abverlangt: Wer die Wallbox am
    Starten hindert, hindert sie mindestens eine Mindestlaufzeit lang. Ein Akku,
    der in dieser Zeit ohnehin voll wird, hat diesen Preis nicht verdient.

    Zwei Bedingungen, die verschiedene Fragen stellen, und beide müssen erfüllt
    sein:

    - `soc < lade_deckel_soc` — soll der Akku JETZT laden? Vor dem Rampenstart
      hält der Deckel auf dem Ist-Stand; dann reserviert der Akku nichts und
      überlässt dem Auto den Vormittag. Das ist die Just-in-time-Ladung, und sie
      steht so schon in `test_charge_deckel.py`.
    - Frist-Prädikat gegen `lade_ziel_soc` — hat er HEUTE überhaupt noch Bedarf?
      Bewusst gegen den Endwert der Ladekurve statt gegen den Deckel: Der Deckel
      läuft der Rampe nur wenige Prozentpunkte voraus, ein halb leerer Akku
      hätte danach ständig „keinen Bedarf mehr" und die Rampe käme nie in Gang.
      Das Tagesziel steht still — ist es erreicht, ist der Bedarf erschöpft.

    Der Anlass, am 23.08.2026: Drei Speicher standen bei 99/100/99 % mit 0,07 kWh
    freier Kapazität und nahmen 0 W. Die alte Bedingung `soc < deckel` reservierte
    trotzdem 2400 W, das Auto blieb mit 2066 W unter seinem Mindeststrom stehen,
    und 4466 W gingen ins Netz. Die Reservierung hielt Leistung für einen
    Verbraucher frei, den es nicht mehr gab.

    Ein abgemeldeter Speicher (`stale`) reserviert nichts: Sein SoC ist kein
    Messwert mehr, sondern der letzte, den er gemeldet hat — und wer einer
    Fiktion Leistung zuteilt, hält den Rest der Anlage still (siehe
    `StorageState.stale`).

    Ist der Akku fertig, hat er keinen Vorrang oder läuft die Mittags-Ladepause
    (11:00–14:00), bekommt die Wallbox den vollen Überschuss. Der Akku geht dabei
    nicht leer aus — was die Lasten nicht nehmen, holt sich die Saldo-Regelung
    anschließend über ihr Residuum, statt es einzuspeisen.
    """
    if not inp.modulateds or res.lade_pause or not akku_hat_vorrang(inp):
        return 0.0
    deckel = res.lade_deckel_soc if res.lade_deckel_soc is not None else 100.0
    ziel = res.lade_ziel_soc if res.lade_ziel_soc is not None else deckel
    frist_h = max(m.min_on_min for m in inp.modulateds) / 60.0
    return sum(
        s.max_charge_w
        for s in inp.storages
        if s.soc is not None
        and not s.stale
        and s.soc < deckel
        and not ladeauftrag_in_frist_erfuellbar(
            ist_soc=s.soc,
            grenze_soc=ziel,
            capacity_kwh=s.capacity_kwh,
            zugeteilt_w=s.max_charge_w,
            frist_h=frist_h,
        )
    )
