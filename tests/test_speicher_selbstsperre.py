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

Die Verriegelung ist der zweite Teil und wiegt schwerer als der erste: Ein
abgemeldeter Speicher bekommt 0 W, und ohne Befehl quittiert der Actuator gar
nicht mehr (`_apply_battery` leitet `laden_soll`/`entladen_soll` aus der
Zuteilung ab, `_quittung_speicher` steigt bei 0 W sofort aus). Ohne Verriegelung
löschte die Abmeldung also ihren eigenen Beweis und der Ausfall käme im
5-Minuten-Takt zurück in die Zuteilung — der Schaden vom 15.08.2026, nur
getaktet. Deshalb prüft dieser Test die Übergänge über mehrere Zyklen und nicht
bloß die Form des Ausdrucks.
"""
from __future__ import annotations

import ast
from pathlib import Path

from factories import plan_input, storage, zuteilung
from hems import planner as P
from hems.strategies.types import speicher_stumm_latch

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


def test_stille_allein_verriegelt_niemanden():
    # Der 17.08.: Der Speicher schweigt seit Stunden, aber es lag nie ein
    # Befehl an, dem er nicht gefolgt wäre. Wer nichts befohlen bekam, kann
    # nichts verweigert haben.
    verriegelt: set[str] = set()
    for _ in range(50):
        assert not speicher_stumm_latch(
            verriegelt, "L1", schweigt=True, nicht_gefolgt=False
        )
    assert verriegelt == set()


def test_schweigen_und_nichtausfuehrung_verriegeln():
    # Der 15.08.: eingefrorene 100 %, volle Anforderung, keine Leistung.
    verriegelt: set[str] = set()
    assert speicher_stumm_latch(verriegelt, "L1", schweigt=True, nicht_gefolgt=True)


def test_verriegelung_haelt_ohne_weiteren_befehl():
    """Der Kern: Die Abmeldung darf ihren eigenen Beweis nicht löschen.

    Sobald L1 abgemeldet ist, teilt ihm die Regelung 0 W zu — und ohne Befehl
    quittiert der Actuator nicht mehr, `nicht_gefolgt` fällt also auf False.
    Ohne Verriegelung käme der ausgefallene Speicher damit im nächsten Zyklus
    zurück in die Zuteilung, gewönne mit seinen eingefrorenen 100 % erneut die
    Rangfolge und flackerte im 5-Minuten-Takt.
    """
    verriegelt: set[str] = set()
    speicher_stumm_latch(verriegelt, "L1", schweigt=True, nicht_gefolgt=True)
    for _ in range(50):
        assert speicher_stumm_latch(
            verriegelt, "L1", schweigt=True, nicht_gefolgt=False
        ), "abgemeldet bleibt abgemeldet, solange keine Meldung kommt"


def test_eine_frische_meldung_entriegelt_sofort():
    # Der Rückweg, und der einzige: Meldet das Gerät wieder, regelt HEMS im
    # nächsten Zyklus mit — ohne Neustart, ohne Quittierung von Hand.
    verriegelt: set[str] = set()
    speicher_stumm_latch(verriegelt, "L1", schweigt=True, nicht_gefolgt=True)
    assert not speicher_stumm_latch(
        verriegelt, "L1", schweigt=False, nicht_gefolgt=False
    )
    assert verriegelt == set()
    # Und die Verriegelung greift danach wieder, wenn der Ausfall zurückkommt.
    assert speicher_stumm_latch(verriegelt, "L1", schweigt=True, nicht_gefolgt=True)


def test_verriegelung_trennt_die_speicher():
    # Ein Ausfall darf nicht die gesunden Nachbarn mitnehmen.
    verriegelt: set[str] = set()
    speicher_stumm_latch(verriegelt, "L1", schweigt=True, nicht_gefolgt=True)
    assert not speicher_stumm_latch(
        verriegelt, "L2", schweigt=True, nicht_gefolgt=False
    )
    assert verriegelt == {"L1"}


# --- Die Nähte (HA-nah, über den Syntaxbaum) --------------------------------


def test_coordinator_verriegelt_ueber_die_gemeinsame_funktion():
    # Sonst stünde die Übergangslogik zweimal da und die Tests oben prüften
    # eine Kopie, die im Betrieb gar nicht läuft.
    quelle = ast.unparse(_funktion("coordinator.py", "_stumm"))
    assert "speicher_stumm_latch" in quelle
    assert "self._speicher_stumm" in quelle
    assert "STORAGE_STALE_MIN" in quelle


def test_offen_kommt_aus_der_quittung_des_actuators():
    # Die Quittung liest den Wert des Leistungssensors, nicht sein Alter —
    # genau deshalb trägt sie, wo `last_reported` nicht trägt. Kommt `offen`
    # aus einer anderen Quelle, ist die Kopplung wertlos.
    quelle = (BASIS / "coordinator.py").read_text(encoding="utf-8")
    assert "speicher_nicht_uebernommen" in quelle
    assert "stale=self._stumm(s, offen)" in quelle


def test_verriegelung_ueberlebt_die_zyklen():
    # Als Instanzzustand angelegt, nicht als lokale Variable — eine pro Zyklus
    # neu gebaute Menge wäre die Verriegelung, die nichts verriegelt.
    quelle = (BASIS / "coordinator.py").read_text(encoding="utf-8")
    assert "self._speicher_stumm: set[str] = set()" in quelle


def test_erster_zyklus_ohne_vorlauf_stuerzt_nicht_ab():
    # `speicher_nicht_uebernommen` schreibt der Actuator NACH `compute_plan` —
    # gelesen wird also der vorige Zyklus. Beim ersten Lauf gibt es keinen.
    quelle = ast.unparse(_funktion("coordinator.py", "_async_update_data"))
    assert "self.data is not None" in quelle
    assert "self.data.plan is not None" in quelle


def test_ohne_leistungssensor_quittiert_der_actuator_nicht():
    """Ohne Messung ist eine Nichtausführung nicht feststellbar.

    Damit verriegelt ein Speicher ohne `power_entity` nie — bewusst so herum:
    Ein zu Unrecht abgemeldeter Speicher legt die ganze Regelung still, ein zu
    Unrecht mitgeführter kostet die Zeit bis zum nächsten Befehl. Geprüft wird
    der Wächter im Actuator, nicht die Prosa daneben.
    """
    knoten = _funktion("actuator.py", "_quittung_speicher")
    rueckgaben = [k for k in ast.walk(knoten) if isinstance(k, ast.Return)]
    assert rueckgaben, "die Quittung muss früh aussteigen können"
    quelle = ast.unparse(knoten)
    assert "not s.power_entity" in quelle
