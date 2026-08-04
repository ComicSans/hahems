"""Die Naht zwischen Betriebsart-Erkennung und Aktuierung.

Ob eine Anlage heizt oder kühlt, entscheidet `entity_domain.betriebsart` und ist
dort HA-frei getestet (`test_entity_domain.py`); was daraus folgt, entscheidet
`strategies/heating.py` (`test_heating.py`). Dazwischen liegen drei Stellen, die
nur in `actuator.py` und `coordinator.py` stehen — beide importieren Home
Assistant und sind für diese Suite unsichtbar:

- Der Actuator muss die Aus-Richtung selbst absichern. Die Planungsschicht
  schützt nur die Rolle Heizung; eine Schaltlast an einer climate-Entität hat
  gar keine Witterungsführung und käme dort nie vorbei.
- Er muss beim Einschalten die Betriebsart mitgeben, sonst kommt eine im
  Kühlbetrieb abgeschaltete Anlage als Heizung wieder hoch.
- Der Coordinator muss die zuletzt gesehene Betriebsart über das Abschalten
  hinweg halten, denn eine ausgeschaltete climate-Entität sagt nur noch `off`.

Die Dateien werden deshalb über den Syntaxbaum gelesen statt importiert — wie in
`test_ww_verdrahtung.py`, aus demselben Grund.

Anlass ist der 04.08.2026: HEMS schaltete eine Anlage ab, die im Modus
`heat_cool` bei 39 °C Außentemperatur kühlte. Die Sommersperre hielt sie für
eine Heizung im August.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hems.entity_domain import BETRIEBSART_FREMD, BETRIEBSART_HEIZEN
from hems.strategies.types import HeatingSetpoint, HeatingState, PlanResult

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


def _namen(knoten: ast.AST) -> set[str]:
    return {k.id for k in ast.walk(knoten) if isinstance(k, ast.Name)} | {
        k.attr for k in ast.walk(knoten) if isinstance(k, ast.Attribute)
    }


def _ruft(knoten: ast.AST, name: str) -> bool:
    return any(
        isinstance(k, ast.Call)
        and (
            (isinstance(k.func, ast.Name) and k.func.id == name)
            or (isinstance(k.func, ast.Attribute) and k.func.attr == name)
        )
        for k in ast.walk(knoten)
    )


# --- Felder auf beiden Seiten -------------------------------------------------
def test_felder_existieren_auf_beiden_seiten():
    assert HeatingState(name="x").betriebsart == BETRIEBSART_HEIZEN
    assert HeatingSetpoint(name="x").betriebsart == BETRIEBSART_HEIZEN
    assert PlanResult().heizung_nicht_uebernommen == []


def test_heating_state_default_ist_das_alte_verhalten():
    """Ein `switch` hat keinen Modus. Wäre der Default etwas anderes als
    `heizen`, verlöre jede bestehende Anlage ihre Witterungsführung."""
    assert HeatingState(name="x").betriebsart == "heizen"


# --- Der Actuator sichert die Aus-Richtung ------------------------------------
def test_turn_schaltet_einen_fremden_modus_nicht_ab():
    """Der Schutz muss in `_turn` liegen, nicht nur im Planner: Über `_turn`
    laufen auch die Schaltlasten, die keine Betriebsart kennen."""
    fn = _funktion("actuator.py", "_turn")
    assert "betriebsart" in _namen(fn)
    assert "BETRIEBSART_FREMD" in _namen(fn)


def test_turn_prueft_den_fremdmodus_nur_beim_ausschalten():
    """Einschalten trifft immer einen zugeordneten Modus — `schalt_service`
    schreibt genau die konfigurierten. Ein Guard in beide Richtungen würde
    dagegen den Frostschutz aussperren."""
    fn = _funktion("actuator.py", "_turn")
    zweige = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.If) and "BETRIEBSART_FREMD" in _namen(k.test)
    ]
    assert len(zweige) == 1
    # Der Test des Zweigs muss die Aus-Richtung fordern (`not on`).
    assert any(
        isinstance(k, ast.UnaryOp)
        and isinstance(k.op, ast.Not)
        and isinstance(k.operand, ast.Name)
        and k.operand.id == "on"
        for k in ast.walk(zweige[0].test)
    )


def test_turn_reicht_kuehl_modus_und_betriebsart_an_schalt_service():
    fn = _funktion("actuator.py", "_turn")
    aufrufe = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "schalt_service"
    ]
    assert len(aufrufe) == 1
    argumente = {
        a.id for a in aufrufe[0].args if isinstance(a, ast.Name)
    }
    assert {"cool_mode", "art"} <= argumente


def test_heizung_wird_mit_ihrer_betriebsart_geschaltet():
    """Sonst schriebe HEMS beim Wiedereinschalten den Heiz-Modus auf eine
    Anlage, die es aus dem Kühlbetrieb genommen hat."""
    fn = _funktion("actuator.py", "_apply_heating")
    aufrufe = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "_turn_heizung"
    ]
    assert len(aufrufe) == 2  # Frostschutz und reguläre Lage
    for a in aufrufe:
        assert any(
            isinstance(arg, ast.Attribute) and arg.attr == "betriebsart"
            for arg in a.args
        )


# --- Übernahme-Kontrolle ------------------------------------------------------
def test_nicht_uebernommene_lage_wird_gemeldet_und_nicht_nachgeschrieben():
    """Am 04.08.2026 nahm die Anlage `set_hvac_mode: off` entgegen und kühlte
    weiter. Nachtreten hilft dort nicht und ist für den Verdichter das Gegenteil
    von Anti-Takt."""
    fn = _funktion("actuator.py", "_turn_heizung")
    namen = _namen(fn)
    assert "HEIZUNG_QUITTUNG_FRIST" in namen
    assert "heizung_nicht_uebernommen" in namen
    assert _ruft(fn, "warning")

    # Nach der Meldung darf kein Schreibvorgang mehr folgen: der Zweig, der
    # `plan.heizung_nicht_uebernommen` befüllt, endet mit `return`.
    for zweig in ast.walk(fn):
        if not isinstance(zweig, ast.If):
            continue
        if "heizung_nicht_uebernommen" not in _namen(zweig):
            continue
        assert any(isinstance(k, ast.Return) for k in zweig.body)
        return
    raise AssertionError("kein Zweig gefunden, der die Nicht-Übernahme bucht")


def test_buchung_haengt_am_rueckgabewert_von_call():
    """Ein gedrosselter Aufruf darf keine Nicht-Übernahme melden, die niemand
    geschrieben hat — denselben Fehler vermeidet die Warmwasser-Buchführung."""
    fn = _funktion("actuator.py", "_turn_heizung")
    for zweig in ast.walk(fn):
        if not isinstance(zweig, ast.If):
            continue
        if not _ruft(zweig.test, "_call"):
            continue
        if "_last_heizung" in _namen(zweig.body[0] if zweig.body else zweig):
            return
    raise AssertionError(
        "Die Buchung hängt nicht am Rückgabewert von _call — ein gedrosselter "
        "Aufruf würde als geschrieben gelten"
    )


def test_erreichte_lage_loescht_buchung_und_meldung():
    """Sonst bliebe eine einmal gemeldete Anlage für immer als gestört
    verbucht, auch nachdem sie den Befehl doch ausgeführt hat."""
    fn = _funktion("actuator.py", "_turn_heizung")
    namen = _namen(fn)
    assert "pop" in namen
    assert "discard" in namen


# --- Das Gedächtnis des Coordinators ------------------------------------------
def test_coordinator_merkt_sich_die_betriebsart_ueber_das_abschalten_hinweg():
    """Eine ausgeschaltete climate-Entität steht auf `off` und sagt nicht mehr,
    was sie vorher tat. Ohne Gedächtnis fiele sie auf „heizen" zurück — und
    HEMS beurteilte eine Anlage, die es selbst aus dem Kühlbetrieb genommen
    hat, nach der Sommersperre."""
    fn = _funktion("coordinator.py", "_betriebsart")
    namen = _namen(fn)
    assert "_letzte_betriebsart" in namen
    assert "ist_an" in namen
    assert "mode_cool_option" in namen


def test_gedaechtnis_wird_nur_aus_dem_an_zustand_gefuellt():
    """Gemerkt wird der zuletzt *gesehene* aktive Modus. Würde auch `off`
    gebucht, überschriebe der erste Abschaltvorgang genau die Information, für
    die das Gedächtnis existiert."""
    fn = _funktion("coordinator.py", "_betriebsart")
    for zweig in ast.walk(fn):
        if isinstance(zweig, ast.If) and _ruft(zweig.test, "ist_an"):
            assert "_letzte_betriebsart" in _namen(zweig.body[0])
            return
    raise AssertionError("kein an-Zweig gefunden, der das Gedächtnis füllt")


def test_heating_states_reicht_die_betriebsart_hinein():
    fn = _funktion("coordinator.py", "_heating_states")
    aufrufe = [
        k
        for k in ast.walk(fn)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Name)
        and k.func.id == "HeatingState"
    ]
    assert len(aufrufe) == 1
    assert "betriebsart" in {kw.arg for kw in aufrufe[0].keywords}


# --- Anzeige ------------------------------------------------------------------
def test_sensor_zeigt_nicht_uebernommene_lagen():
    quelle = (BASIS / "sensor.py").read_text(encoding="utf-8")
    assert "heizung_nicht_uebernommen" in quelle


def test_config_check_meldet_den_fehlenden_kuehl_modus():
    quelle = (BASIS / "config_check.py").read_text(encoding="utf-8")
    assert "mode_cool_option" in quelle


def test_fremdmodus_ist_kein_zustand_den_der_planner_erfindet():
    """`fremd` beschreibt immer eine laufende Anlage: `off` ist `heizen`. Der
    Planner darf daraus deshalb nur „nicht abschalten" ableiten, nie einen
    Einschaltzwang."""
    fn = _funktion("strategies/heating.py", "heating_control")
    for zweig in ast.walk(fn):
        if not isinstance(zweig, ast.If) or "BETRIEBSART_FREMD" not in _namen(
            zweig.test
        ):
            continue
        gesetzt = {
            z.attr
            for k in ast.walk(zweig)
            if isinstance(k, ast.Assign)
            for z in k.targets
            if isinstance(z, ast.Attribute)
        }
        assert "nicht_abschalten" in gesetzt
        assert "zwang_an" not in gesetzt
        return
    raise AssertionError(f"kein {BETRIEBSART_FREMD}-Zweig in heating_control")


def test_gedaechtnis_merkt_sich_den_fremdmodus_nicht():
    """`fremd` heißt „HEMS lässt die Anlage in Ruhe" und beschreibt immer eine
    laufende. Aus dem Gedächtnis heraus träfe es eine abgeschaltete — und die
    bekäme keinen Frostschutz mehr, weil die Witterungsführung im Fremdmodus
    gar nicht erst rechnet."""
    fn = _funktion("coordinator.py", "_betriebsart")
    for zweig in ast.walk(fn):
        if not isinstance(zweig, ast.If) or "BETRIEBSART_FREMD" not in _namen(
            zweig.test
        ):
            continue
        # Im Fremd-Zweig wird verworfen, nicht gebucht.
        assert _ruft(zweig, "pop")
        assert not any(
            isinstance(k, ast.Assign)
            and any(isinstance(z, ast.Subscript) for z in k.targets)
            for k in ast.walk(ast.Module(body=zweig.body, type_ignores=[]))
        )
        return
    raise AssertionError("kein Fremd-Zweig in _betriebsart gefunden")


def test_config_check_warnt_vor_einem_mehrdeutigen_heiz_modus():
    """`mode_heat_option: heat_cool` macht den ganzen Schutz wirkungslos: Der
    Modus gilt dann als Heizen, und die Sommersperre greift wieder."""
    quelle = (BASIS / "config_check.py").read_text(encoding="utf-8")
    assert '"heat_cool", "auto"' in quelle


def test_entscheidungslog_und_panel_kennen_die_neuen_lagen():
    """Ein Status ohne Label rutscht als roher Slug durch die Anzeige."""
    quelle = (BASIS / "changelog.py").read_text(encoding="utf-8")
    assert '"kuehlen"' in quelle
    assert '"fremdmodus"' in quelle
    assert "heizung_quittung" in quelle
    panel = (BASIS / "frontend" / "hems-panel.js").read_text(encoding="utf-8")
    assert "kuehlen:" in panel
    assert "fremdmodus:" in panel
