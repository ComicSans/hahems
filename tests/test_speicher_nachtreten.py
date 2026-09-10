"""Nachtreten an einem stummen Speicher — und warum „einmal schreiben" nicht reicht.

Anlass ist die Nacht vom 09. auf den 10.09.2026, die erste Nacht mit
Speicher-Zwangsladung (Release 2.9.0). Um 23:04 stellte HEMS beide Zendure
Hyper 2000 auf „laden": Richtungs-Select auf `input`, Input-Limit auf 1200 W.
Zwanzig Minuten lang floss 0 W, das Haus zog derweil 1,3 kW aus dem Netz, und
HEMS schrieb in dieser ganzen Zeit kein einziges Mal nach. Zwei Ursachen, beide
hier abgesichert:

- **Die Deduplizierung schweigt genau dann, wenn sie sprechen müsste.**
  `_set_number` vergleicht gegen den Zustand der Number-Entität, und der ist
  der Echo-Wert des Geräts. Steht dort der kommandierte Wert, gilt der Befehl
  als angekommen — obwohl das Gerät nichts tut. Der Docstring der Quittung
  behauptete bis dahin, „die Setpoints gehen ohnehin jeden Zyklus erneut raus";
  das war schlicht falsch.
- **Der Zustand des Richtungs-Selects ist nach einem Reload wertlos.** Die
  Zendure-Integration legt `acMode` ohne Restore mit dem Default „input" an
  (`ZendureSelect(self, "acMode", {1: "input", 2: "output"}, …, 1)`), also
  zeigt die Entität „input", bis das Gerät von sich aus acMode meldet. In
  derselben Nacht meldete L2 um 23:08:57 und um 23:23:16 jeweils „output" —
  HEMS korrigierte beide Male erst durch diese Meldung. L3 meldete nach dem
  Reload gar nichts mehr und stand für HEMS unverrückbar auf „input".
  `self._state(mode_entity) != want` ist in dieser Lage blind.

Die Historie derselben Nacht widerlegt zwei naheliegende Erklärungen, die
deshalb bewusst NICHT eingebaut sind:

- „Das Gerät braucht einen niedrigen Anlaufwert." Nein: Am 08.09. um 11:04
  startete L2 aus dem Stillstand bei Limit 1200 mit 1041 W, am 08.09. um 07:10
  bei Limit 745 mit 745 W. Ein Anlaufwert wäre eine Konstante gegen einen
  Befund, den es nicht gibt.
- „Das Gerät lädt nur Überschuss." Nein: Nach dem Lösen zog es 1,32 kW bei
  Netzbezug, und im Code der Zendure-Integration ist der Hyper 2000 ein
  `ZendureLegacy` in `auto_model = 0` — `acMode` + `inputLimit` IST der
  Befehlspfad.

`speicher_stumm_schaden` ist HA-frei und direkt testbar. Die Naht zum Actuator
(Rückgabewert der Quittung, Schreiben ohne Vergleich und ohne Drossel) steht in
`actuator.py`, die Home Assistant importiert; sie wird deshalb über den
Syntaxbaum gelesen — dieselbe Bauart wie `test_speicher_quittung.py`.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hems.actuation import speicher_stumm_schaden

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


def _quelle(knoten: ast.AST) -> str:
    return ast.unparse(knoten)


# --- Der Nachsatz der Warnung ----------------------------------------------


def test_zwangsladung_meldet_nicht_den_ueberschuss():
    # Der Satz aus der Nacht vom 09.09.2026: 1,3 kW Netzbezug, kein Überschuss
    # weit und breit — „der Überschuss geht ins Netz" beschreibt das Gegenteil
    # dessen, was passiert.
    assert (
        speicher_stumm_schaden(laden=True, zwang_aktiv=True)
        == "die Zwangsladung bleibt liegen"
    )


def test_ueberschussladen_meldet_weiter_den_ueberschuss():
    assert (
        speicher_stumm_schaden(laden=True, zwang_aktiv=False)
        == "der Überschuss geht ins Netz"
    )


def test_entladen_meldet_den_bezug_unabhaengig_vom_zwang():
    # Die Zwangsladung ist eine Lade-Eigenschaft; im Entlade-Zweig darf sie den
    # Text nicht anfassen.
    for zwang in (True, False):
        assert (
            speicher_stumm_schaden(laden=False, zwang_aktiv=zwang)
            == "der Bezug kommt aus dem Netz"
        )


# --- Die Naht im Actuator ---------------------------------------------------


def test_quittung_meldet_den_nachtritt_zurueck():
    # Die Quittung kennt als Einzige die Stummheits-Uhr. Gäbe sie nichts
    # zurück, müsste der Aufrufer die Frist ein zweites Mal rechnen.
    quittung = _funktion("actuator.py", "_quittung_speicher")
    assert isinstance(quittung.returns, ast.Name) and quittung.returns.id == "bool"
    quelle = _quelle(quittung)
    assert "SPEICHER_NACHTRETEN_FRIST" in quelle
    assert "return nachtreten" in quelle


def test_voller_speicher_wird_nicht_nachgetreten():
    # Ein Akku am Ladeschluss nimmt nichts mehr an — Nachschreiben im
    # Minutentakt wäre Bus-Spam gegen die Physik. Der Ladeschluss-Zweig muss
    # deshalb hart False melden, nicht in die Frist-Rechnung fallen.
    quelle = _quelle(_funktion("actuator.py", "_quittung_speicher"))
    ladeschluss = quelle.split("ladeauftrag_am_ladeschluss")[1]
    kopf = ladeschluss.split("now = ")[0]
    assert "return False" in kopf


def test_nachtritt_schreibt_setpoints_am_dedup_vorbei():
    # Der Kern des Befunds: Ohne `erzwingen` vergleicht `_set_number` gegen den
    # Echo-Wert des Geräts und schweigt dauerhaft.
    quelle = _quelle(_funktion("actuator.py", "_apply_battery"))
    assert "erzwingen=nachtreten" in quelle
    # Beide Richtungen, nicht nur die, die in der Nacht auffiel.
    assert quelle.count("erzwingen=nachtreten") == 2


def test_nachtritt_schreibt_die_richtung_ohne_zustandsvergleich():
    # `self._state(mode_entity) != want` ist nach einem Reload der
    # Geräte-Integration blind (Select ohne Restore, Default „input"). Beim
    # Nachtritt muss der Select darum bedingungslos rausgehen.
    quelle = _quelle(_funktion("actuator.py", "_apply_battery"))
    bedingung = quelle.split("select_option")[0].rsplit("if ", 1)[1]
    assert "nachtreten" in bedingung
    assert "self._state" in bedingung
    # Und die 5-Minuten-Drossel in `_call` darf den Nachtritt nicht fressen.
    assert "ohne_drossel=nachtreten" in quelle


def test_set_number_dedupliziert_nur_ohne_erzwingen():
    quelle = _quelle(_funktion("actuator.py", "_set_number"))
    assert "if not erzwingen" in quelle
    # Erzwingen heißt beides: kein Dedup UND keine Drossel.
    assert "ohne_drossel or erzwingen" in quelle


def test_nachtreten_frist_ist_kuerzer_als_die_meldefrist():
    # Nachtreten ist billig und still, eine Warnung ins Log ist es nicht — die
    # Reihenfolge ist Absicht und darf nicht versehentlich kippen.
    baum = ast.parse((BASIS / "actuator.py").read_text(encoding="utf-8"))
    werte: dict[str, ast.Call] = {}
    for knoten in baum.body:
        if isinstance(knoten, ast.Assign) and isinstance(knoten.value, ast.Call):
            ziel = knoten.targets[0]
            if isinstance(ziel, ast.Name):
                werte[ziel.id] = knoten.value

    def _sekunden(name: str) -> float:
        aufruf = werte[name]
        args = {kw.arg: ast.literal_eval(kw.value) for kw in aufruf.keywords}
        return args.get("minutes", 0) * 60 + args.get("seconds", 0)

    assert 0 < _sekunden("SPEICHER_NACHTRETEN_FRIST") < _sekunden(
        "SPEICHER_QUITTUNG_FRIST"
    )
    # Und lang genug, dass ein gesunder Anlauf nicht nachgetreten wird: In der
    # Nacht vom 09.09.2026 lagen zwischen Schreiben (23:23:52) und erster
    # Leistung (23:24:53) 61 Sekunden.
    assert _sekunden("SPEICHER_NACHTRETEN_FRIST") >= 61
