"""Der Invers-Modus: derselbe Auto-Betrieb, nur der Richtungs-Select gedreht.

Anlass sind Geräte bzw. Integrationen, deren Ein-/Ausgangsmodus vertauscht
beschriftet sind: HEMS stellt „Eingangsmodus", und das Gerät entlädt. Der
Invers-Modus dreht genau diese eine Zuordnung — und sonst nichts.

Das „sonst nichts" ist der Punkt, an dem die Sache kippen könnte: Die Regelung
schließt über die *gemessene* Speicherleistung (`soll = bat_ist + fehler ×
gain` in `strategies/battery.py`). Würden auch die Leistungs-Sollwerte oder das
Vorzeichen der Messung gedreht, liefe der Regler gegen sich selbst. Die Tests
unten pinnen deshalb beide Seiten: die vertauschte Option *und* die unberührten
Sollwerte.

`speicher_modus_option` ist HA-frei und direkt testbar. Die Naht zum Coordinator
importiert Home Assistant und wird wie in `test_speicher_quittung.py` über den
Syntaxbaum gelesen.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hems.actuation import speicher_modus_option
from hems.const import MODE_AUTO, MODE_INVERS_AUTO, MODE_OFF, MODES_ACTUATING

BASIS = Path(__file__).resolve().parents[1] / "custom_components" / "hems"

EIN = "Eingangsmodus"
AUS = "Ausgangsmodus"


def _funktion(datei: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    baum = ast.parse((BASIS / datei).read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if (
            isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef))
            and knoten.name == name
        ):
            return knoten
    raise AssertionError(f"{name} nicht in {datei} gefunden")


# --- Die vertauschte Zuordnung ----------------------------------------------


def test_normal_laedt_im_eingangsmodus_und_entlaedt_im_ausgangsmodus():
    assert (
        speicher_modus_option("laden", lade_option=EIN, entlade_option=AUS) == EIN
    )
    assert (
        speicher_modus_option("entladen", lade_option=EIN, entlade_option=AUS) == AUS
    )


def test_invers_dreht_beide_richtungen():
    # Genau die Forderung: Laden → Ausgangsmodus, Einspeisen → Eingangsmodus.
    assert (
        speicher_modus_option("laden", lade_option=EIN, entlade_option=AUS, invers=True)
        == AUS
    )
    assert (
        speicher_modus_option(
            "entladen", lade_option=EIN, entlade_option=AUS, invers=True
        )
        == EIN
    )


def test_pause_stellt_auch_invers_nichts():
    # In der Pause bleibt der zuletzt gesetzte Modus stehen, sonst flippt der
    # Select bei jedem Deadband-Durchgang — der Invers-Modus ändert daran nichts.
    for invers in (False, True):
        assert (
            speicher_modus_option(
                "pausiert", lade_option=EIN, entlade_option=AUS, invers=invers
            )
            is None
        )


def test_ohne_vollstaendiges_optionspaar_wird_nicht_gestellt():
    # Ein halbes Paar ist keine Zuordnung — erst recht keine, die sich drehen
    # ließe.
    assert speicher_modus_option("laden", lade_option=EIN, entlade_option=None) is None
    assert (
        speicher_modus_option("entladen", lade_option=None, entlade_option=AUS) is None
    )
    assert (
        speicher_modus_option(
            "laden", lade_option=EIN, entlade_option=None, invers=True
        )
        is None
    )


# --- Die Leistung bleibt unberührt ------------------------------------------


def test_leistungs_setpoints_haengen_nur_am_regelmodus():
    """Die Sollwert-Zweige in `_apply_battery` dürfen `invers` nicht kennen.

    Der Regler schließt über die gemessene Speicherleistung; drehte man auch
    die Sollwerte, regelte er gegen sich selbst. Geprüft am Syntaxbaum, weil
    `actuator.py` Home Assistant importiert.
    """
    fn = _funktion("actuator.py", "_apply_battery")
    zuweisungen = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Assign)
        and any(
            isinstance(z, ast.Tuple)
            and {getattr(e, "id", None) for e in z.elts} == {"charge_w", "discharge_w"}
            for z in k.targets
        )
    ]
    assert zuweisungen, "die Sollwert-Zweige wurden umbenannt — Test nachziehen"
    for zuw in zuweisungen:
        namen = {k.id for k in ast.walk(zuw) if isinstance(k, ast.Name)}
        assert "invers" not in namen


def test_invers_erreicht_genau_die_modus_option():
    # `invers` darf im Actuator nur an einer Stelle ankommen: der Option des
    # Richtungs-Selects.
    fn = _funktion("actuator.py", "_apply_battery")
    aufrufe = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Name)
        and k.func.id == "speicher_modus_option"
    ]
    assert len(aufrufe) == 1
    assert any(kw.arg == "invers" for kw in aufrufe[0].keywords)


# --- Naht zum Coordinator ---------------------------------------------------


def test_invers_auto_schaltet_wie_auto():
    assert MODE_AUTO in MODES_ACTUATING
    assert MODE_INVERS_AUTO in MODES_ACTUATING
    assert MODE_OFF not in MODES_ACTUATING


def test_beide_modus_pruefungen_laufen_ueber_die_liste():
    """Auch das *Verlassen* muss den Invers-Modus kennen.

    Die zweite Prüfung (`_prev_mode`) gibt den Akku frei, damit er nicht mit
    der zuletzt kommandierten Rate blind weiterläuft. Bliebe sie auf
    `MODE_AUTO` stehen, liefe genau das nach invers-auto → beobachten/aus —
    der Fall, für den `release_battery` existiert.
    """
    fn = _funktion("coordinator.py", "_async_update_data")
    vergleiche = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Compare)
        and isinstance(k.left, ast.Attribute)
        and k.left.attr in ("mode", "_prev_mode")
        and isinstance(getattr(k.left.value, "id", None), str)
    ]
    gegen_liste = [
        k
        for k in vergleiche
        if isinstance(k.ops[0], ast.In)
        and getattr(k.comparators[0], "id", None) == "MODES_ACTUATING"
    ]
    assert len(gegen_liste) == 2, "self.mode und self._prev_mode gehören an die Liste"
    assert not [
        k
        for k in vergleiche
        if any(getattr(c, "id", None) == "MODE_AUTO" for c in k.comparators)
    ], "kein direkter Vergleich gegen MODE_AUTO mehr"


def test_der_actuator_bekommt_den_invers_modus_gereicht():
    fn = _funktion("coordinator.py", "_async_update_data")
    aufrufe = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "apply"
    ]
    assert len(aufrufe) == 1
    assert any(kw.arg == "invers" for kw in aufrufe[0].keywords)
