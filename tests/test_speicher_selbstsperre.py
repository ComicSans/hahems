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
import inspect
from pathlib import Path

from factories import plan_input, storage, zuteilung
from hems import actuation as A
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
    # Entlade-Befehl an, dem er nicht gefolgt wäre. Wer nichts befohlen bekam,
    # kann nichts verweigert haben. Seit dem 07.09.2026 ist eine
    # Lade-Verweigerung ab jetzt derselbe Fall: Sie zählt hier so wenig wie gar
    # kein Befehl, weil `entladen_verweigert` ausschließlich aus dem
    # Entlade-Zweig der Quittung kommt (siehe `_quittung_speicher`).
    verriegelt: set[str] = set()
    for _ in range(50):
        assert not speicher_stumm_latch(
            verriegelt, "L1", schweigt=True, entladen_verweigert=False
        )
    assert verriegelt == set()


def test_schweigen_und_nichtausfuehrung_verriegeln():
    # Der 15.08.: eingefrorene 100 %, volle Anforderung, keine Leistung.
    verriegelt: set[str] = set()
    assert speicher_stumm_latch(
        verriegelt, "L1", schweigt=True, entladen_verweigert=True
    )


def test_verriegelung_haelt_ohne_weiteren_befehl():
    """Der Kern: Die Abmeldung darf ihren eigenen Beweis nicht löschen.

    Sobald L1 abgemeldet ist, teilt ihm die Regelung 0 W zu — und ohne Befehl
    quittiert der Actuator nicht mehr, `entladen_verweigert` fällt also auf
    False. Ohne Verriegelung käme der ausgefallene Speicher damit im nächsten
    Zyklus zurück in die Zuteilung, gewönne mit seinen eingefrorenen 100 %
    erneut die Rangfolge und flackerte im 5-Minuten-Takt.
    """
    verriegelt: set[str] = set()
    speicher_stumm_latch(verriegelt, "L1", schweigt=True, entladen_verweigert=True)
    for _ in range(50):
        assert speicher_stumm_latch(
            verriegelt, "L1", schweigt=True, entladen_verweigert=False
        ), "abgemeldet bleibt abgemeldet, solange keine Meldung kommt"


def test_eine_frische_meldung_entriegelt_sofort():
    # Der Rückweg, und der einzige: Meldet das Gerät wieder, regelt HEMS im
    # nächsten Zyklus mit — ohne Neustart, ohne Quittierung von Hand.
    verriegelt: set[str] = set()
    speicher_stumm_latch(verriegelt, "L1", schweigt=True, entladen_verweigert=True)
    assert not speicher_stumm_latch(
        verriegelt, "L1", schweigt=False, entladen_verweigert=False
    )
    assert verriegelt == set()
    # Und die Verriegelung greift danach wieder, wenn der Ausfall zurückkommt.
    assert speicher_stumm_latch(
        verriegelt, "L1", schweigt=True, entladen_verweigert=True
    )


def test_verriegelung_trennt_die_speicher():
    # Ein Ausfall darf nicht die gesunden Nachbarn mitnehmen.
    verriegelt: set[str] = set()
    speicher_stumm_latch(verriegelt, "L1", schweigt=True, entladen_verweigert=True)
    assert not speicher_stumm_latch(
        verriegelt, "L2", schweigt=True, entladen_verweigert=False
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


def test_offen_kommt_aus_der_entladen_verweigert_liste():
    """Vierter Fund derselben Ursache (07.09.2026): Nur eine
    Entlade-Verweigerung darf den Latch füttern.

    Vorher bildete `offen` sich aus `speicher_nicht_uebernommen`, dem
    Sammelfeld für beide Richtungen — eine Lade-Verweigerung (voller,
    ruhender Speicher, Zuteilung unter der Ausnahme-Schwelle) landete darin
    genauso wie eine echte Entlade-Verweigerung, und beide verriegelten
    gleich. Diese Naht pinnt die neue Quelle, damit kein künftiger Umbau
    `offen` wieder aufs Sammelfeld zurückzieht.
    """
    quelle = (BASIS / "coordinator.py").read_text(encoding="utf-8")
    assert "self.data.plan.speicher_entladen_verweigert" in quelle
    assert "stale=self._stumm(s, offen)" in quelle


def test_nur_der_entlade_zweig_fuettert_den_latch():
    """Die Naht im Actuator, die zur obigen im Coordinator gehört.

    `plan.speicher_entladen_verweigert` darf ausschließlich unter
    `not laden_soll` beschrieben werden — schreibt auch der Lade-Zweig
    hierher, verriegelt ein voller ruhender Speicher sich wieder über einen
    Ladeauftrag, den er physisch nicht annehmen kann (derselbe Fehlalarm wie
    vorher über `speicher_nicht_uebernommen`, nur unter neuem Namen).
    """
    knoten = _funktion("actuator.py", "_quittung_speicher")
    treffer = [
        zweig
        for zweig in ast.walk(knoten)
        if isinstance(zweig, ast.If)
        and any(
            isinstance(aufruf, ast.Call)
            and isinstance(aufruf.func, ast.Attribute)
            and aufruf.func.attr == "append"
            and "speicher_entladen_verweigert" in ast.unparse(aufruf.func.value)
            for aufruf in ast.walk(zweig)
            if isinstance(aufruf, ast.Call)
        )
    ]
    assert len(treffer) == 1, "genau ein Append auf speicher_entladen_verweigert"
    bedingung = ast.unparse(treffer[0].test)
    assert "not laden_soll" in bedingung


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


# --- Der fertige Ladeauftrag: physische Schwelle statt Frist-Rechnung ------
#
# Der Fix vom 17.08. verlangt für die Verriegelung zwei Auslöser: Schweigen UND
# Nichtausführung. Das trägt nur, solange beide unabhängig sind — und genau das
# sind sie beim vollen Akku nicht. Er ruht (also schweigt sein push-Sensor) und
# nimmt keine Ladung mehr an (also „folgt er nicht"), beides aus derselben
# Ursache. Am Abend des 19.08. verriegelten so alle drei Hyper 2000 nacheinander,
# jeder exakt 15 Minuten nach seiner letzten Meldung, und das Haus zog 800 W.
#
# Der Fix vom 19.08. — `ladeauftrag_in_frist_erfuellbar`, bis 07.09.2026 hier
# getestet — verglich freie Kapazität bis zur Ladegrenze mit der Energie, die
# die zugeteilte Leistung in der Quittungsfrist liefern würde. Das modellierte
# den Akku als Verbraucher, der bis zur Grenze alles nimmt, was zugeteilt ist.
# Im CV-Taper nimmt er, was das BMS zulässt, unabhängig von der Zuteilung — die
# Zuteilung ist eine Obergrenze, keine Nachfrage. Die Ausnahme trug deshalb erst
# ab einer Mindestzuteilung und scheiterte darunter an der nächsten Zahl: Am
# 07.09.2026 (vierter Fund derselben Ursache,
# tasks/speicher-selbstsperre-ladepfad.md) standen L1/L3 bei 99 % im Taper,
# der Nachmittags-Restüberschuss lag unter der Mindestzuteilung, die Ausnahme
# griff nicht. Dazu rechnete sie gegen `plan.lade_deckel_soc`, nicht gegen das
# physische Ladeende — bei einem Deckel von 80 % und Ist-SoC 79 hätte sie
# „fertig" gesagt, obwohl der Akku nicht im Taper ist und ein echter Ausfall
# damit maskiert würde.
#
# Seit 07.09.2026 (Subtask C) gilt stattdessen die feste physische Schwelle
# `SPEICHER_VOLL_SOC = 99.0` (`ladeauftrag_am_ladeschluss`): Sie fragt nur
# noch, ob der Akku physisch am Ladeschluss steht — kein SoC-Abstand, keine
# Zuteilung, kein Deckel. Die Verriegelung ist davon ohnehin nicht mehr
# betroffen (Subtask A: nur eine Entlade-Verweigerung verriegelt) — hier geht
# es nur noch um die Warnung.


def test_voller_akku_meldet_keinen_ausfall():
    # 99 % ist physisch am Ladeschluss — unabhängig von der Zuteilung, die die
    # alte Frist-Rechnung noch gebraucht hätte.
    assert A.ladeauftrag_am_ladeschluss(99.0)


def test_halbvoller_akku_muss_weiter_quittieren():
    # Unter der Schwelle bleibt die Warnung scharf. Nimmt er hier nichts auf,
    # ist das ein Befund und kein Feierabend.
    assert not A.ladeauftrag_am_ladeschluss(90.0)


def test_ohne_soc_bleibt_es_bei_der_quittung():
    # Kein SoC-Sensor oder unbekannter Zustand heißt nicht „fertig" — sonst
    # fiele ein Speicher ohne Messung stillschweigend aus der Warnung.
    assert not A.ladeauftrag_am_ladeschluss(None)


def test_grenze_ist_physisch_nicht_der_deckel():
    """Szene 07.09.2026 gegen die Regel, die den Fund verursacht hat.

    L1/L3 standen bei `ist_soc = 99` physisch im Taper — unabhängig davon, ob
    die Nachmittags-Zuteilung 300 W oder 3000 W betrug, das ist am Ladeschluss.
    Bei `ist_soc = 79` und einem Ladedeckel von 80 % ist der Akku dagegen NICHT
    im Taper: Eine Regel, die wieder gegen `plan.lade_deckel_soc` rechnet,
    würde hier fälschlich „fertig" sagen und einen echten Ausfall maskieren —
    genau die Maskierung, die Frage 4 in
    tasks/speicher-selbstsperre-ladepfad.md ausdrücklich verbietet.

    `ladeauftrag_am_ladeschluss` nimmt gar keinen Deckel mehr entgegen — das
    ist keine Zufälligkeit der Beispielwerte, sondern die Eigenschaft, die
    hier gepinnt wird: Ein bloßer Wertetest (`ladeauftrag_am_ladeschluss(79.0)
    is False`) ginge auch dann noch grün, wenn jemand `grenze_soc` als
    optionalen Parameter wieder einführt und `plan.lade_deckel_soc` am
    Aufruf-Ort erneut hineinreicht — deshalb zusätzlich die strukturelle Probe
    auf Signatur und Aufruf-Ort.
    """
    assert A.ladeauftrag_am_ladeschluss(99.0)
    assert not A.ladeauftrag_am_ladeschluss(79.0)

    signatur = inspect.signature(A.ladeauftrag_am_ladeschluss)
    assert set(signatur.parameters) == {"ist_soc"}, (
        "keine Deckel-Parameter an der Funktion selbst"
    )

    # AST statt Text: Der Docstring erklärt bewusst, warum NICHT mehr gegen den
    # Deckel gerechnet wird und nennt beide Namen dabei selbst — eine reine
    # Textsuche über die unparste Quelle träfe also auch dort.
    attribute_namen = {
        knoten.attr
        for knoten in ast.walk(_funktion("actuator.py", "_quittung_speicher"))
        if isinstance(knoten, ast.Attribute)
    }
    assert "lade_deckel_soc" not in attribute_namen
    assert "laden_statt_einspeisen" not in attribute_namen


def test_die_ausnahme_gilt_nur_beim_laden():
    """Der Entlade-Zweig bleibt scharf — dort war der 15.08. echt.

    Ein Speicher mit eingefrorenen 100 %, der die volle Anforderung nicht
    bedient, ist der Ausfall, für den die Quittung gebaut wurde. Läge die
    Ausnahme vor der Richtungsprüfung, entschärfte sie genau ihn: „voll" ist
    beim Entladen die Bedingung, unter der er liefern MUSS.
    """
    quelle = ast.unparse(_funktion("actuator.py", "_quittung_speicher"))
    assert "laden_soll and ladeauftrag_am_ladeschluss" in quelle
