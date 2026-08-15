"""Der Ladedeckel am Gerät und die Quittung des Ladens.

Anlass ist eine Messung vom 14.08.2026 an drei Zendure Hyper 2000. Nach einem
HA-Neustart stand die Regelung 26 Minuten auf „laden", HEMS rampte den
Leistungs-Sollwert bis 914 W hoch — und real floss 0 W, rund 1,2 kW gingen
derweil ins Netz. Zwei Ursachen, beide hier abgesichert:

- Der geräteseitige Ziel-SoC stand auf dem Ladedeckel, und der Deckel liegt
  vormittags per Definition auf dem Planungsstand, also auf dem Ist-SoC. Für
  das Gerät heißt „Ziel = Ist" fertig; die zugeteilte Leistung verfällt.
  Belegt am selben Zyklus: L1 mit Ziel-SoC 27 bei Ist-SoC 27 zog 0 W, L2 und
  L3 mit demselben 581-W-Sollwert und Ziel-SoC 70 zogen die volle Leistung.
- Das Gerät nimmt den Ziel-SoC an und zieht ihn nach 10 bis 70 s auf seinen
  eigenen zurück. Die 5-Minuten-Drossel in `_call` hielt das Nachschreiben auf,
  weil sie einen wiederholten identischen Aufruf für ein dauerhaft ablehnendes
  Gerät hält — hier trifft das nicht zu.

`plan_soc_set` und `speicher_laedt` sind HA-frei und direkt testbar. Die Naht
zum Actuator (Drossel-Ausnahme, Rückweg in den Plan) steht in `actuator.py`,
die Home Assistant importiert; sie wird deshalb über den Syntaxbaum gelesen —
dieselbe Bauart wie `test_ww_verdrahtung.py`.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hems.actuation import (
    SOC_SET_KOPFRAUM,
    SPEICHER_LADEN_MIN_W,
    plan_soc_set,
    speicher_entlaedt,
    speicher_folgt,
    speicher_laedt,
)
from hems.strategies.types import PlanResult

BASIS = Path(__file__).resolve().parents[1] / "custom_components" / "hems"


def _funktion(datei: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    baum = ast.parse((BASIS / datei).read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if (
            isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef))
            and knoten.name == name
        ):
            return knoten
    raise AssertionError(f"{name} nicht in {datei} gefunden")


# --- Ziel-SoC ---------------------------------------------------------------


def test_ohne_laden_bleibt_der_deckel_der_deckel():
    # Der Kopfraum darf die Grenze nicht aufheben, für die er da ist: Wer
    # nichts zugeteilt bekommt (voll, über dem Deckel, Kaltreserve), behält
    # den Deckel unverändert.
    assert (
        plan_soc_set(
            deckel_soc=64.0, laden_statt_einspeisen=False, laedt=False, ist_soc=63.5
        )
        == 64.0
    )


def test_deckel_auf_hoehe_des_ist_soc_bekommt_kopfraum():
    # Der Fall vom 14.08.2026: Deckel 26.7 bei Ist-SoC 26.7. Ohne Kopfraum
    # schreibt HEMS dem Gerät „du bist fertig" und wundert sich über 0 W.
    ziel = plan_soc_set(
        deckel_soc=26.7, laden_statt_einspeisen=False, laedt=True, ist_soc=26.7
    )
    assert ziel == 26.7 + SOC_SET_KOPFRAUM
    # Und der Kopfraum überlebt das ganzzahlige Schreiben an der Number.
    assert round(ziel) > round(26.7)


def test_kopfraum_hebt_einen_hohen_deckel_nicht_an():
    # Liegt der Deckel weit über dem Ist, ist er die bindende Grenze — der
    # Kopfraum ist ein Mindestabstand, kein Aufschlag.
    assert (
        plan_soc_set(
            deckel_soc=64.0, laden_statt_einspeisen=False, laedt=True, ist_soc=26.7
        )
        == 64.0
    )


def test_laden_statt_einspeisen_hebt_den_deckel_ganz_auf():
    assert (
        plan_soc_set(
            deckel_soc=26.7, laden_statt_einspeisen=True, laedt=True, ist_soc=26.7
        )
        == 100.0
    )


def test_ziel_soc_bleibt_in_den_grenzen_der_entitaet():
    # Kopfraum auf einen fast vollen Speicher darf nicht über 100 laufen; die
    # Number-Entität nähme den Wert nicht an.
    assert (
        plan_soc_set(
            deckel_soc=99.5, laden_statt_einspeisen=False, laedt=True, ist_soc=99.5
        )
        == 100.0
    )


def test_ohne_ist_soc_bleibt_es_beim_deckel():
    # Kein SoC lesbar heißt kein Kopfraum — geraten wird nicht.
    assert (
        plan_soc_set(
            deckel_soc=26.7, laden_statt_einspeisen=False, laedt=True, ist_soc=None
        )
        == 26.7
    )


# --- „lädt wirklich?" -------------------------------------------------------


def test_laden_ist_negative_leistung_oberhalb_des_rauschens():
    assert speicher_laedt(-581.0) is True
    assert speicher_laedt(-SPEICHER_LADEN_MIN_W) is True
    assert speicher_laedt(-10.0) is False
    assert speicher_laedt(0.0) is False
    # Positiv ist entladen, nicht laden.
    assert speicher_laedt(581.0) is False
    assert speicher_laedt(None) is False


def test_entladen_ist_die_gespiegelte_schwelle():
    assert speicher_entlaedt(581.0) is True
    assert speicher_entlaedt(SPEICHER_LADEN_MIN_W) is True
    assert speicher_entlaedt(10.0) is False
    assert speicher_entlaedt(0.0) is False
    assert speicher_entlaedt(-581.0) is False
    assert speicher_entlaedt(None) is False


def test_das_rauschband_gilt_in_beide_richtungen():
    # Der Grund für zwei Funktionen statt einer Negation: Zwischen den
    # Schwellen tut der Speicher gar nichts — und genau dieser Zustand ist der
    # Fehlerfall, den die Quittung sucht. `not speicher_laedt(10)` wäre True
    # und würde ihn als „entlädt" durchwinken.
    assert speicher_folgt(10.0, laden=True) is False
    assert speicher_folgt(10.0, laden=False) is False
    assert speicher_folgt(-581.0, laden=True) is True
    assert speicher_folgt(581.0, laden=False) is True


# --- Naht zum Actuator ------------------------------------------------------


def test_planergebnis_traegt_den_rueckweg():
    assert PlanResult().speicher_nicht_uebernommen == []


def test_ziel_soc_wird_ohne_drossel_geschrieben():
    # Ohne diese Ausnahme steht der zurückgezogene Ziel-SoC bis zu fünf
    # Minuten lang auf dem Gerätewert — und der Speicher lädt so lange nicht.
    fn = _funktion("actuator.py", "_apply_battery")
    treffer = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "_set_number"
        and any(kw.arg == "ohne_drossel" for kw in k.keywords)
    ]
    assert len(treffer) == 1, "genau der Ziel-SoC gehört an der Drossel vorbei"


def test_leistungs_setpoints_bleiben_gedrosselt():
    # Die Ausnahme gilt dem Wert, den das Gerät von selbst verwirft — nicht
    # allem. Die Lade-/Entlade-Sollwerte ändern sich ohnehin jeden Zyklus.
    fn = _funktion("actuator.py", "_apply_battery")
    ohne = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "_set_number"
        and not any(kw.arg == "ohne_drossel" for kw in k.keywords)
    ]
    assert len(ohne) == 2


def test_kopfraum_haengt_am_ist_soc_des_speichers():
    # Der Kopfraum ist nur so gut wie der Ist-Wert, gegen den er rechnet: Wird
    # `soc_entity` nicht hineingereicht, ist `plan_soc_set` wirkungslos.
    fn = _funktion("actuator.py", "_apply_battery")
    aufrufe = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Name)
        and k.func.id == "plan_soc_set"
    ]
    assert len(aufrufe) == 1
    assert {"deckel_soc", "laden_statt_einspeisen", "laedt", "ist_soc"} == {
        kw.arg for kw in aufrufe[0].keywords
    }
    quelle = ast.unparse(aufrufe[0])
    assert "soc_entity" in quelle


def test_quittung_haengt_an_einer_frist():
    # Wie bei Warmwasser und Heizung: „es gab seit dem Schreiben einen neuen
    # Ist-Wert" wäre genau im Fehlerfall nie erfüllt.
    fn = _funktion("actuator.py", "_quittung_speicher")
    namen = {k.id for k in ast.walk(fn) if isinstance(k, ast.Name)}
    assert "SPEICHER_QUITTUNG_FRIST" in namen
    assert "speicher_folgt" in namen


def test_quittung_bekommt_beide_richtungen():
    # Der Fall vom 15.08.2026: Die Zuteilung stand fünfeinhalb Stunden auf
    # einem stummen Speicher, und weil die Quittung nur `laedt_soll` kannte,
    # stieg sie beim Entladen sofort wieder aus.
    aufruf = [
        k
        for k in ast.walk(_funktion("actuator.py", "_apply_battery"))
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "_quittung_speicher"
    ]
    assert len(aufruf) == 1
    quelle = ast.unparse(aufruf[0])
    assert "laedt_soll" in quelle and "entlaedt_soll" in quelle


def test_quittung_schreibt_die_beobachtung_in_den_plan():
    quelle = ast.unparse(_funktion("actuator.py", "_quittung_speicher"))
    assert "plan.speicher_nicht_uebernommen" in quelle


def test_sensor_zeigt_die_quittung():
    quelle = (BASIS / "sensor.py").read_text(encoding="utf-8")
    assert "speicher_nicht_uebernommen" in quelle


def test_entscheidungslog_fuehrt_die_quittung():
    quelle = (BASIS / "changelog.py").read_text(encoding="utf-8")
    assert "speicher_nicht_uebernommen" in quelle
    assert "akku_quittung" in quelle
