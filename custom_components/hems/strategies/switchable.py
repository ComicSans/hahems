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

Wärmeerzeuger (Rolle Heizung) laufen im selben Budget und derselben Rangfolge
mit; ihre Witterungsführung entscheidet vorher in `strategies/heating.py` und
reicht das Ergebnis über `zwang_an`/`zwang_aus`/`nicht_abschalten` herein.
"""
from __future__ import annotations

from dataclasses import replace

from ..const import (
    DEFAULT_SWITCHABLE_EXPECTED_W,
    SWITCH_LEARN_DECAY,
    SWITCH_LEARN_WARMUP_S,
    SWITCH_SURPLUS_MARGIN_W,
)
from .types import PlanInput, PlanResult, SwitchableResult, SwitchableSetpoint


def _erwartet_w(s) -> float:
    return s.erwartet_w if s.erwartet_w and s.erwartet_w > 0 else DEFAULT_SWITCHABLE_EXPECTED_W


def lern_leistung(
    alt: float | None,
    mess: float,
    an_seit_s: float | None,
    *,
    floor_w: float,
) -> float | None:
    """Neuen `erwartet_w`-Wert aus einer Messung im An-Zustand bilden.

    `None` heißt „diese Messung taugt nicht zum Lernen" — der bisherige Wert
    bleibt stehen. Verworfen wird innerhalb der Anlaufkarenz (der Verbraucher ist
    noch nicht auf Leistung) und unterhalb des Bodens (Standby, Regelung,
    Umwälzpumpe).

    Übernommen wird asymmetrisch: nach oben sofort, weil eine unterschätzte Last
    zu früh eingeschaltet wird und Netzbezug provoziert; nach unten nur gedämpft,
    damit eine Teillastphase den gelernten Wert nicht auf ihren Momentanwert
    zieht. Der gedämpfte Wert nähert sich der Messung über mehrere Zyklen und
    steht dabei nie unter ihr.
    """
    if an_seit_s is None or an_seit_s < SWITCH_LEARN_WARMUP_S:
        return None
    if mess < floor_w:
        return None
    if alt is None or mess >= alt:
        return round(mess, 1)
    return round(alt + SWITCH_LEARN_DECAY * (mess - alt), 1)


def _mit_vorgaben(loads, heizung):
    """Die Witterungsführung über die Schaltlasten legen.

    Kopien statt Mutation: `inp` gehört dem Aufrufer, und der Planner bleibt
    eine reine Funktion. Lasten ohne Heizungsrolle bleiben unverändert.
    """
    if heizung is None:
        return loads
    out = []
    for s in loads:
        sp = heizung.by_id(s.id)
        if sp is None:
            out.append(s)
            continue
        out.append(
            replace(
                s,
                zwang_an=sp.zwang_an,
                zwang_aus=sp.sperre,
                nicht_abschalten=sp.nicht_abschalten,
                grund_vorgabe=sp.grund,
            )
        )
    return out


def switchable_control(inp: PlanInput, res: PlanResult) -> SwitchableResult | None:
    """An/Aus-Empfehlung je schaltbarer Last berechnen.

    Ohne Netzsaldo gibt es keinen Überschuss zu verteilen und damit keine
    Empfehlung. Der Frostschutz der Heizung hängt bewusst NICHT hier dran — er
    entsteht in `strategies/heating.py` und wird vom Actuator eigenständig
    gestellt, gerade damit er einen unerreichbaren Zähler überlebt.
    """
    loads = _mit_vorgaben(inp.switchables, res.heizung)
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
        # Vorgaben einer übergeordneten Domäne stehen vor allem anderen —
        # heute setzt sie nur die Heizung. Der Frostschutz (`zwang_an`) geht
        # damit auch an der Mindestpause und am Budget vorbei: er kauft die
        # Wärme notfalls aus dem Netz.
        if s.zwang_an:
            an, grund = True, s.grund_vorgabe or "Zwang"
        elif _locked_on(s):
            # Vor `zwang_aus`, nicht dahinter: Ein Zwang zum EINschalten ist
            # Sicherheit und geht über alles, ein Zwang zum AUSschalten
            # (Sommersperre, Heizgrenze) ist Sparsamkeit. Stünde er vor der
            # Mindestlaufzeit, risse der Monatswechsel um Mitternacht oder das
            # Überschreiten der Heizgrenze einen laufenden Kompressor mitten
            # aus dem Takt — genau das, was `min_on` verhindern soll.
            an, grund = True, "min_on gehalten"
        elif s.zwang_aus:
            an, grund = False, s.grund_vorgabe or "gesperrt"
        elif _block_ueberschritten(s):
            an, grund = True, "max_block erreicht"
        elif _locked_off(s):
            an, grund = False, "min_off gehalten"
        else:
            # Hysterese: einschalten ab erwartet+Marge, anlassen bis erwartet−Marge.
            schwelle = erwartet - margin if s.ist_an else erwartet + margin
            if budget >= schwelle:
                an, grund = True, "Überschuss deckt Last"
            else:
                an, grund = False, "Überschuss zu klein"
        # Blindflug: HEMS würde abschalten, kann die Lage aber nicht beurteilen
        # (der Heizung fehlt die Außentemperatur). Eine laufende Anlage bleibt
        # dann laufen — abschalten wäre die einzige Entscheidung, die sich
        # nicht zurücknehmen lässt, bevor das Haus kalt ist.
        if not an and s.nicht_abschalten and s.ist_an:
            an, grund = True, s.grund_vorgabe or "Lage unbekannt"
        if an:
            budget -= erwartet
            soll_w += erwartet
        lasten.append(SwitchableSetpoint(name=s.name, an=an, id=s.id, grund=grund))

    return SwitchableResult(
        lasten=lasten, soll_w=round(soll_w), delta_w=round(soll_w - mess_sw)
    )
