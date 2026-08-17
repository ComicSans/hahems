"""Stille ist kein Ausfall — ein ruhender Speicher darf sich nicht selbst aussperren.

Anlass ist der 17.08.2026. Die drei Hyper 2000 standen bei 92 % und liefen
einwandfrei, das Haus zog trotzdem 800 W aus dem Netz.
`sensor.hems_speicher_regelung` meldete `pausiert`, `soll_w = 0`,
`abgemeldet: [L1, L2, L3]` — bei drei Speichern, die in HA durchgehend
`available` waren.

Die Ursache steckt in einer Annahme über Home Assistant, nicht in der Anlage:
Die Zendure-Integration setzt `_attr_should_poll = False` und schreibt den
Zustand nur bei Wertänderung (`sensor.py`: `if new_value !=
self._attr_native_value: … schedule_update_ha_state()`). Dort bewegt sich
`last_reported` genauso wenig wie `last_changed` — es trennt „steht still"
NICHT von „ist stumm". Ein voller Akku, der ruht, ändert keinen Wert, meldet
nichts und war nach 15 Minuten „abgemeldet".

Daraus wurde eine Sperre, die sich selbst hält: abgemeldet → HEMS pausiert →
der Akku ruht weiter → nie wieder eine Wertänderung. Sichtbar an einem
Zufallsfenster um 06:19, in dem sich L1s SoC einmal bewegte: HEMS befahl
sofort `entladen 1200 W`, 15 Minuten später war er wieder abgemeldet.

Der Ausweg ist die Quittung des Actuators, die es längst gibt: Sie liest den
WERT des Leistungssensors gegen einen ausstehenden Befehl, nicht dessen Alter,
und ist damit unabhängig davon, wann eine Integration schreibt. Abgemeldet ist
seither nur, wer schweigt UND einem Befehl nicht folgt.
"""
from __future__ import annotations

import ast
from pathlib import Path

from factories import plan_input, storage, zuteilung
from hems import planner as P

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


# --- Die Konsequenz in der Regelung -----------------------------------------


def test_ruhender_voller_speicher_deckt_den_netzbezug():
    # Die Lage vom 17.08.2026: volle Speicher, Netzbezug, niemand ausgefallen.
    # Kein `stale` — und die Regelung muss entladen statt zu pausieren.
    res = P.compute_plan(
        plan_input(
            saldo_w=800.0,
            storage_states=[
                storage("L1", 97.0, power_w=0.0),
                storage("L2", 99.0, power_w=0.0),
                storage("L3", 81.0, power_w=0.0),
            ],
        )
    )
    assert res.regelung is not None
    assert res.regelung.modus == "entladen"
    assert res.regelung.abgemeldet_namen == []
    assert sum(zuteilung(res).values()) > 0


# --- Die Erkennung im Coordinator (HA-nah, über den Syntaxbaum) --------------


def test_stille_allein_meldet_niemanden_ab():
    """`_stumm` verlangt BEIDE Hälften.

    Fiele die Kopplung an `offen` weg, stünde exakt der 17.08. wieder da: Ein
    Speicher, dem nichts befohlen wurde, kann nichts verweigert haben.
    """
    knoten = _funktion("coordinator.py", "_stumm")
    rueckgaben = [k for k in ast.walk(knoten) if isinstance(k, ast.Return)]
    assert len(rueckgaben) == 1, "eine einzige Rückgabe, sonst greift die Prüfung daneben"
    ausdruck = rueckgaben[0].value
    assert isinstance(ausdruck, ast.BoolOp) and isinstance(ausdruck.op, ast.And), (
        "Abmelden muss eine UND-Verknüpfung sein: still UND einem Befehl nicht gefolgt"
    )
    quelle = ast.unparse(ausdruck)
    assert "offen" in quelle
    assert "_abgemeldet" in quelle


def test_offen_kommt_aus_der_quittung_des_actuators():
    # Die Quittung liest den Wert des Leistungssensors, nicht sein Alter —
    # genau deshalb trägt sie, wo `last_reported` nicht trägt. Kommt `offen`
    # aus einer anderen Quelle, ist die Kopplung wertlos.
    quelle = (BASIS / "coordinator.py").read_text(encoding="utf-8")
    assert "speicher_nicht_uebernommen" in quelle
    assert "stale=self._stumm(s, offen)" in quelle


def test_erster_zyklus_ohne_vorlauf_stuerzt_nicht_ab():
    # `speicher_nicht_uebernommen` schreibt der Actuator NACH `compute_plan` —
    # gelesen wird also der vorige Zyklus. Beim ersten Lauf gibt es keinen.
    knoten = _funktion("coordinator.py", "_async_update_data")
    quelle = ast.unparse(knoten)
    assert "self.data is not None" in quelle
    assert "self.data.plan is not None" in quelle


def test_ohne_leistungssensor_gilt_niemand_als_abgemeldet():
    """Ohne Messung ist eine Nichtausführung nicht feststellbar.

    Das ist bewusst so herum entschieden und steht im Docstring: Ein zu Unrecht
    abgemeldeter Speicher legt die ganze Regelung stumm, ein zu Unrecht
    mitgeführter kostet die Zeit bis zum nächsten Befehl. Der Actuator setzt es
    um, indem er ohne `power_entity` gar nicht erst quittiert — dieser Test
    hält die Begründung an der Entscheidung fest.
    """
    quittung = ast.unparse(_funktion("actuator.py", "_quittung_speicher"))
    assert "not s.power_entity" in quittung
    assert "Leistungssensor" in ast.get_docstring(_funktion("coordinator.py", "_stumm"))
