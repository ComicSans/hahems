"""Die Naht zwischen Aktuierung und Anzeige bei der Warmwasser-Freigabe.

`plan_ww_action` entscheidet HA-frei und ist oberhalb voll getestet
(`test_ww_actuation.py`). Was sie entscheiden kann, hängt aber an drei Dingen,
die nur in `actuator.py` stehen — und die Datei importiert Home Assistant, ist
für diese Suite also unsichtbar:

- `last_written_on` muss überhaupt hineingereicht werden, sonst ist der Rückweg
  tot und die Meldung kommt nie.
- Gebucht werden darf nur ein Aufruf, der tatsächlich rausging. `_call`
  drosselt identische Aufrufe fünf Minuten lang und meldet das über seinen
  Rückgabewert; würde blind gebucht, hielte HEMS einen verworfenen Aufruf für
  geschrieben — es verbrauchte den einmaligen Rückweg und meldete eine
  Nicht-Übernahme, die niemand geschrieben hat.
- Die Beobachtung muss zurück in den Plan, sonst erreicht sie weder den Sensor
  noch den Entscheidungs-Log.

Die Datei wird deshalb über den Syntaxbaum gelesen statt importiert.

Anlass ist eine Messung vom 01.08.2026 an einer LG Therma V: HEMS schrieb die
Warmwasser-Freigabe sechsmal ein, die Anlage fiel jedes Mal nach Sekunden
zurück, und zu sehen war davon nichts.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hems.actuation import WwPlan
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


def _attribut_ziele(knoten: ast.AST) -> set[str]:
    """Alle `a.b`-Ziele von Zuweisungen im Teilbaum, als Punkt-Pfad."""
    ziele: set[str] = set()
    for k in ast.walk(knoten):
        if not isinstance(k, ast.Assign):
            continue
        for ziel in k.targets:
            teile = []
            while isinstance(ziel, ast.Attribute):
                teile.append(ziel.attr)
                ziel = ziel.value
            if isinstance(ziel, ast.Name) and teile:
                ziele.add(".".join([ziel.id, *reversed(teile)]))
    return ziele


def test_felder_existieren_auf_beiden_seiten():
    # Die Aktuierung entscheidet es, das Planergebnis trägt es zur Anzeige.
    assert WwPlan().nicht_uebernommen is False
    assert PlanResult().warmwasser_nicht_uebernommen is False


def test_actuator_reicht_die_geschriebene_freigabe_hinein():
    fn = _funktion("actuator.py", "_apply_ww")
    aufrufe = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Name)
        and k.func.id == "plan_ww_action"
    ]
    assert len(aufrufe) == 1
    assert "last_written_on" in {kw.arg for kw in aufrufe[0].keywords}


def test_actuator_schreibt_die_beobachtung_in_den_plan():
    fn = _funktion("actuator.py", "_apply_ww")
    assert "plan.warmwasser_nicht_uebernommen" in _attribut_ziele(fn)


def _ist_last_ww_buchung(knoten: ast.AST) -> bool:
    return isinstance(knoten, ast.Assign) and any(
        isinstance(z, ast.Subscript)
        and isinstance(z.value, ast.Attribute)
        and z.value.attr == "_last_ww"
        for z in knoten.targets
    )


def _enthaelt_call(knoten: ast.AST) -> bool:
    """Ob im Teilbaum ein `self._call(...)` steht."""
    return any(
        isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "_call"
        for k in ast.walk(knoten)
    )


def test_geschriebene_freigabe_wird_nur_nach_echtem_aufruf_gebucht():
    # Die Buchung muss im Zweig einer Bedingung stehen, die am Ergebnis von
    # `self._call(...)` hängt — ob direkt im if-Test oder über einen Namen, der
    # daraus befüllt wurde. Stünde sie eine Ebene höher (nur unter "es ist zu
    # schalten"), zählte auch ein gedrosselter Aufruf als geschrieben; genau
    # den macht der Rückgabewert von `_call` unterscheidbar.
    fn = _funktion("actuator.py", "_apply_ww")
    buchungen = [k for k in ast.walk(fn) if _ist_last_ww_buchung(k)]
    assert buchungen, "keine Buchung der geschriebenen Freigabe gefunden"

    # Namen, die im Funktionskörper aus einem `self._call(...)` befüllt werden.
    aus_call: set[str] = set()
    for k in ast.walk(fn):
        if not isinstance(k, ast.Assign):
            continue
        wert = k.value
        if isinstance(wert, ast.Await):
            wert = wert.value
        if (
            isinstance(wert, ast.Call)
            and isinstance(wert.func, ast.Attribute)
            and wert.func.attr == "_call"
        ):
            aus_call.update(z.id for z in k.targets if isinstance(z, ast.Name))

    for zweig in ast.walk(fn):
        if not isinstance(zweig, ast.If):
            continue
        test_haengt_am_call = _enthaelt_call(zweig.test) or (
            isinstance(zweig.test, ast.Name) and zweig.test.id in aus_call
        )
        if not test_haengt_am_call:
            continue
        if any(_ist_last_ww_buchung(k) for k in ast.walk(zweig)):
            return
    raise AssertionError(
        "Die Buchung hängt nicht am Rückgabewert von _call — ein gedrosselter "
        "Aufruf würde als geschrieben gelten"
    )


def test_quittung_haengt_an_einer_frist_und_nicht_an_einem_update():
    # Der naheliegende Weg wäre „es gab seit dem Schreiben einen neuen
    # Ist-Wert". Er ist falsch: Ein Entity, das den Befehl ignoriert, ändert
    # seinen Zustand nicht — und ein unveränderter Zustand wird nicht neu
    # veröffentlicht. Die Bedingung wäre genau im Fehlerfall nie erfüllt.
    fn = _funktion("actuator.py", "_quittierte_ww")
    namen = {k.attr for k in ast.walk(fn) if isinstance(k, ast.Attribute)}
    assert "last_updated" not in namen
    assert "WARMWASSER_QUITTUNG_FRIST" in {
        k.id for k in ast.walk(fn) if isinstance(k, ast.Name)
    }


def test_call_meldet_ob_der_aufruf_rausging():
    fn = _funktion("actuator.py", "_call")
    werte = [
        k.value for k in ast.walk(fn) if isinstance(k, ast.Return) and k.value is not None
    ]
    konstanten = {k.value for k in werte if isinstance(k, ast.Constant)}
    assert konstanten == {True, False}


def test_entscheidungslog_fuehrt_die_quittung():
    quelle = (BASIS / "changelog.py").read_text(encoding="utf-8")
    assert "warmwasser_nicht_uebernommen" in quelle
    assert "warmwasser_quittung" in quelle


def test_sensor_zeigt_die_quittung():
    quelle = (BASIS / "sensor.py").read_text(encoding="utf-8")
    assert "freigabe_nicht_uebernommen" in quelle
