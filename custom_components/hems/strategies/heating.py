"""Heizungs-Domäne: Frostschutz, Heizgrenze, Sommersperre und Heizkurve.

Ein Wärmeerzeuger ist für die Überschussregelung eine schaltbare Last wie jede
andere — dieselbe Priorität, dieselben Anti-Takt-Sperren, dasselbe Budget. Was
ihn unterscheidet, steht in diesem Modul: Er darf nicht beliebig lange aus
bleiben, er soll im Sommer gar nicht erst anlaufen, und sein Vorlauf-Sollwert
folgt der Außentemperatur.

**Warum das nicht in `switchable.py` steht.** `switchable_control` steigt ohne
Netzsaldo sofort aus (`inp.saldo_w is None`) — der Zähler ist unerreichbar,
also gibt es keinen Überschuss zu verteilen. Für die Verteilung ist das richtig.
Für den Frostschutz wäre es fatal: eine Heizung, die HEMS zuvor abgeschaltet
hat, bliebe genau in der Störung aus, in der keiner hinschaut. Der Frostschutz
hängt deshalb an nichts als der Temperatur, wird hier vor jeder Saldo-Frage
entschieden und vom Actuator auf einem eigenen Weg gestellt.

**Rangfolge der Entscheidung:**

0. **Betriebsart** — alles Folgende ist Heizungs-Semantik und gilt nur, wenn
   die Anlage auch heizt. Siehe unten.
1. **Frostschutz** — unter `frost_on_c` wird eingeschaltet, an Überschuss,
   Mindestpause und Sommersperre vorbei. Er kauft die Wärme notfalls aus dem
   Netz; das ist der Preis und hier die richtige Wahl.
2. **Sommersperre** — in den Sperrmonaten wird nicht geheizt.
3. **Heizgrenze** — oberhalb `heat_off_c` braucht das Haus keine Wärme.
4. Sonst entscheidet der Überschuss (in `switchable_control`).

**Betriebsart.** Eine Anlage, die kühlt, ist keine Heizung: Sommersperre und
Heizgrenze sagen über sie das Gegenteil dessen aus, wofür sie gedacht sind —
je heißer es wird, desto nötiger ist sie. Im Kühlbetrieb schweigt dieses Modul
deshalb vollständig (kein Zwang, keine Sperre, keine Kurve); es bleibt bei der
Überschussregelung in `switchable_control`, die von Temperaturen nichts weiß.
Ist der Modus gar nicht zugeordnet (`fremd`, typisch `heat_cool`/`auto`), weiß
HEMS nicht einmal, in welche Richtung die Anlage arbeitet — dann wird sie wie
bei fehlender Außentemperatur behandelt: nicht abschalten.

Das ist keine Vorsichtsmaßnahme auf Verdacht. Am 04.08.2026 schaltete die
Sommersperre eine Anlage ab, die im Modus `heat_cool` bei 39 °C
Außentemperatur kühlte.

**Wenn die Außentemperatur fehlt**, kann HEMS keine dieser vier Fragen
beantworten. Es erzwingt dann nichts, verbietet nichts — und schaltet vor allem
nichts ab: `nicht_abschalten` lässt eine laufende Anlage in Ruhe, statt sie
blind wegzunehmen. Wer nicht messen kann, soll nicht regeln.
"""
from __future__ import annotations

from ..entity_domain import BETRIEBSART_FREMD, BETRIEBSART_KUEHLEN
from .types import HeatingResult, HeatingSetpoint, PlanInput, PlanResult, _latch

# Mindestabstand zwischen Ein- und Aus-Schwelle, den `_ordnung` notfalls
# erzwingt. Siehe dort, warum das keine Kosmetik ist.
MIN_HYSTERESE_K = 1.0


def _ordnung(ein: float, aus: float) -> tuple[float, float]:
    """Ein-/Aus-Schwelle so ordnen, dass „aktiv, solange es kalt ist" gilt.

    `_latch` leitet seine Richtung aus der Lage der beiden Schwellen ab: `on <
    off` heißt „aktiv, solange der Wert klein ist", `on > off` das Gegenteil.
    Trägt jemand den Frostschutz als „ein bei 5 °C, aus bei 3 °C" ein — eine
    naheliegende Lesart —, kippt damit die ganze Bedeutung: Die Heizung liefe
    oberhalb von 5 °C zwangsweise an und ginge unterhalb von 3 °C aus. Genau
    verkehrt herum, ohne Fehlermeldung, und ausgerechnet an der Funktion, die
    das Haus vor dem Einfrieren schützen soll.

    Deshalb wird die Richtung hier festgeschrieben statt aus den Werten
    abgelesen. Der Mindestabstand verhindert zusätzlich den entarteten Fall
    `ein == aus`, in dem `_latch` keine Hysterese mehr hätte.
    """
    return ein, max(aus, ein + MIN_HYSTERESE_K)


def _gesperrt(month: int, von: int, bis: int) -> bool:
    """Ob der Monat in der Sommersperre liegt (beide Grenzen einschließlich).

    `von > bis` läuft über den Jahreswechsel (z. B. 11 → 2 = November bis
    Februar). Eine 0 in einer der Grenzen heißt „keine Sperre" — so lässt sich
    die Sperre abschalten, ohne ein Extra-Feld dafür zu haben.
    """
    if not 1 <= von <= 12 or not 1 <= bis <= 12:
        return False
    if von <= bis:
        return von <= month <= bis
    return month >= von or month <= bis


def _vorlauf_c(h, t_aussen: float) -> float:
    """Witterungsgeführter Vorlauf-Sollwert aus der Heizkurve.

    Fußpunkt ist der Vorlauf bei 0 °C Außentemperatur, die Steilheit sagt, um
    wie viel er je Kelvin Außenkälte steigt. Begrenzt auf [vlt_min, vlt_max]:
    unten, damit der Kreis in Ruhe nicht unter die Umwälzgrenze fällt, oben,
    weil jede Anlage eine Vorlauf-Obergrenze hat.
    """
    vlt = h.curve_base_c - t_aussen * h.curve_slope
    return float(round(max(h.vlt_min_c, min(vlt, h.vlt_max_c))))


def heating_control(inp: PlanInput, res: PlanResult) -> HeatingResult | None:
    """Witterungsführung je Wärmeerzeuger — ohne jeden Bezug zum Netzsaldo."""
    if not inp.heatings:
        return None

    anlagen: list[HeatingSetpoint] = []
    for h in inp.heatings:
        sp = HeatingSetpoint(
            name=h.name,
            id=h.id,
            t_aussen_c=h.outdoor_temp_c,
            betriebsart=h.betriebsart,
        )
        t = h.outdoor_temp_c

        if h.betriebsart == BETRIEBSART_KUEHLEN:
            # Die Anlage kühlt: Sommersperre, Heizgrenze und Heizkurve haben
            # hier keine Bedeutung — es bleibt bei der Überschussregelung.
            #
            # Der Frostschutz gilt trotzdem, und zwar mitsamt Moduswechsel: Er
            # steht laut Rangfolge über allem, und unter `frost_on_c` kühlt
            # niemand absichtlich. Steht eine Anlage dort auf Kühlen, ist das
            # ein Fehler — dann ist Umschalten die richtige Antwort und nicht
            # Zusehen.
            frost_ein, frost_aus = _ordnung(h.frost_on_c, h.frost_off_c)
            frost = (
                _latch(inp.flags.frost.get(h.id, False), t, on=frost_ein, off=frost_aus)
                if t is not None
                else inp.flags.frost.get(h.id, False)
            )
            res.flags.frost[h.id] = frost
            # Der Heiz-Latch wird eingefroren statt neu gerechnet: Er beschreibt
            # den Heizbetrieb, und der findet gerade nicht statt.
            res.flags.heizen[h.id] = inp.flags.heizen.get(h.id, False)
            if frost:
                sp.zwang_an = True
                sp.betriebsart = "heizen"
                sp.status = "frostschutz"
                sp.grund = (
                    f"Frostschutz ({t:.0f} °C, aus dem Kühlbetrieb)"
                    if t is not None
                    else "Frostschutz (Außentemperatur unbekannt)"
                )
                if h.hat_vorlauf_entity:
                    sp.vorlauf_c = float(round(h.vlt_min_c))
            else:
                sp.status = "kuehlen"
                sp.grund = "Kühlbetrieb — nur Überschussregelung"
            anlagen.append(sp)
            continue

        if h.betriebsart == BETRIEBSART_FREMD:
            # Modus nicht zugeordnet (`heat_cool`/`auto`): HEMS weiß nicht, ob
            # die Anlage heizt oder kühlt, und lässt eine laufende in Ruhe.
            sp.status = "fremdmodus"
            sp.nicht_abschalten = True
            sp.grund = "Betriebsart nicht zugeordnet"
            res.flags.frost[h.id] = inp.flags.frost.get(h.id, False)
            res.flags.heizen[h.id] = inp.flags.heizen.get(h.id, False)
            anlagen.append(sp)
            continue

        if t is None:
            # Blind: nichts erzwingen, nichts sperren — aber auch nichts
            # abschalten. Die Latches bleiben stehen, damit ein kurzer
            # Sensor-Aussetzer den Frostschutz nicht zurücksetzt.
            sp.status = "unbekannt"
            sp.nicht_abschalten = True
            sp.grund = "Außentemperatur unbekannt"
            res.flags.frost[h.id] = inp.flags.frost.get(h.id, False)
            res.flags.heizen[h.id] = inp.flags.heizen.get(h.id, False)
            # Ein bereits aktiver Frostschutz bleibt aktiv: fällt der Sensor
            # während des Frosts aus, wäre sein Wegfall die gefährlichste
            # Auslegung des fehlenden Messwerts.
            if res.flags.frost[h.id]:
                sp.zwang_an = True
                sp.status = "frostschutz"
                sp.grund = "Frostschutz (Außentemperatur unbekannt)"
            anlagen.append(sp)
            continue

        frost_ein, frost_aus = _ordnung(h.frost_on_c, h.frost_off_c)
        heiz_ein, heiz_aus = _ordnung(h.heat_on_c, h.heat_off_c)
        frost = _latch(
            inp.flags.frost.get(h.id, False), t, on=frost_ein, off=frost_aus
        )
        heizen = _latch(
            inp.flags.heizen.get(h.id, False), t, on=heiz_ein, off=heiz_aus
        )
        res.flags.frost[h.id] = frost
        res.flags.heizen[h.id] = heizen
        gesperrt = _gesperrt(h.month, h.heat_lock_from_month, h.heat_lock_to_month)

        if frost:
            sp.zwang_an = True
            sp.status = "frostschutz"
            sp.grund = f"Frostschutz ({t:.0f} °C, notfalls Netz)"
            # Nur umwälzen, nicht auf Komfort heizen: der Frostschutz soll das
            # Haus nicht warm machen, sondern den Kreis in Bewegung halten.
            if h.hat_vorlauf_entity:
                sp.vorlauf_c = float(round(h.vlt_min_c))
        elif gesperrt:
            sp.sperre = True
            sp.status = "sommersperre"
            sp.grund = "Sommersperre"
        elif not heizen:
            sp.sperre = True
            sp.status = "heizgrenze"
            sp.grund = f"über Heizgrenze ({t:.0f} °C)"
        else:
            sp.status = "heizen"
            sp.grund = f"witterungsgeführt ({t:.0f} °C)"
            if h.hat_vorlauf_entity:
                sp.vorlauf_c = _vorlauf_c(h, t)

        anlagen.append(sp)

    return HeatingResult(anlagen=anlagen)
