"""Config-Sanity-Check: prüft die Rollen-Konfiguration gegen den Auto-Modus.

Läuft jeden Zyklus im Coordinator und speist den Diagnose-Sensor
`binary_sensor.hems_konfiguration`. Beantwortet die Scharfschalt-Frage:
Was schaltet der Auto-Modus, existieren alle Steuer-Entitäten, passen die
Domains — und (heuristisch) schreibt eine aktive Automation auf dieselbe
Steuer-Entität wie HEMS (Überlappung, die im Auto-Modus zum Kampf führt)?

Reine Prüf-Logik ohne Seiteneffekte; der Automations-Scan ist defensiv
gekapselt (fällt bei HA-interner Änderung auf "nicht verfügbar" zurück, statt
den Sensor zu reißen).

**Während Home Assistant hochfährt wird nicht geprüft.** Die Prüfung fragt
`hass.states` ab, und die füllt sich erst, während die Integrationen der Reihe
nach laden. Ein Lauf zu früh meldet jede fremde Entität als "existiert nicht" —
gemessen am 01.08.2026 einundzwanzig Fehler, von denen keiner einer war, samt
Meldung an den Nutzer. Wer das ein paarmal sieht, liest den Sensor nicht mehr.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.core import CoreState, HomeAssistant

from .const import DEFAULT_SWITCHABLE_EXPECTED_W, MODE_AUTO
from .models import DeviceRegistry


@dataclass
class ConfigCheck:
    errors: list[str] = field(default_factory=list)  # Auto-Modus würde scheitern
    warnings: list[str] = field(default_factory=list)  # funktioniert, aber Vorsicht
    info: list[str] = field(default_factory=list)  # rein informativ
    overlaps: list[str] = field(default_factory=list)  # Entity ⇄ aktive Automation
    actuated: list[str] = field(default_factory=list)  # Rollen, die auto schaltet
    scan_ok: bool = True  # Automations-Überlappungsprüfung lief
    # Falsch, solange Home Assistant hochfährt. Ohne dieses Feld läse sich ein
    # ungeprüfter Zustand wie ein geprüfter: `errors` ist leer, und
    # `bereit_fuer_auto` stünde auf wahr, ohne dass irgendetwas geprüft wurde.
    geprueft: bool = True

    def problem(self, mode: str) -> bool:
        """Sensor-Zustand: harte Fehler immer; Überlappung nur im Auto-Modus
        (im Beobachten-/Aus-Modus sind aktive Automationen erwünscht)."""
        return bool(self.errors) or (mode == MODE_AUTO and bool(self.overlaps))

    def signature(self) -> tuple:
        return (
            tuple(self.errors),
            tuple(self.warnings),
            tuple(self.overlaps),
        )


#: Meldung, solange nicht geprüft werden kann. Als `info`, nicht als
#: `warnings`: Es ist kein Befund, sondern seine Abwesenheit.
START_HINWEIS = (
    "Home Assistant startet noch — geprüft wird, sobald alle Integrationen "
    "geladen sind."
)


def pruefung_moeglich(core_state) -> bool:
    """Sind alle Integrationen geladen?

    Nur im Zustand `running` steht fest, dass eine fehlende Entität wirklich
    fehlt. Davor heißt „nicht da" bloß „noch nicht da", und die Prüfung
    unterscheidet das nicht.
    """
    return core_state is CoreState.running


def check_beim_start() -> ConfigCheck:
    """Das Ergebnis, solange nicht geprüft werden kann.

    Bewusst leer statt optimistisch oder pessimistisch: keine Fehler, keine
    Warnungen, `scan_ok=False`. Damit meldet der Sensor kein Problem, behauptet
    aber auch nicht, geprüft zu haben — `bereit_fuer_auto` steht auf wahr, weil
    `errors` leer ist, und der Hinweis daneben sagt, warum das nichts wert ist.
    """
    return ConfigCheck(info=[START_HINWEIS], scan_ok=False, geprueft=False)


def _domain(entity: str | None) -> str | None:
    return entity.split(".")[0] if entity else None


def _exists(hass: HomeAssistant, entity: str | None) -> bool:
    return bool(entity) and hass.states.get(entity) is not None


# `weather` ist die global konfigurierte Wetter-Entität. Sie steht nicht in der
# Registry, ist aber die Rückfall-Quelle für die Außentemperatur der Heizung —
# ohne sie und ohne eigenen Sensor bleibt der Frostschutz blind. Bewusst als
# Kommentar und nicht als Docstring: Die Start-Wache unten muss die erste
# Anweisung der Funktion bleiben (siehe tests/test_config_check_start.py).
def check_config(
    hass: HomeAssistant, reg: DeviceRegistry, weather: str | None = None
) -> ConfigCheck:
    if not pruefung_moeglich(hass.state):
        return check_beim_start()

    c = ConfigCheck()
    control_entities: set[str] = set()

    def _need(entity: str | None, domains: tuple[str, ...], ctx: str, label: str):
        """Steuer-Entity prüfen: Existenz + Domain, und für den Overlap-Scan
        vormerken."""
        if not entity:
            return
        control_entities.add(entity)
        if _domain(entity) not in domains:
            c.errors.append(
                f"{ctx}: {label} {entity} hat falsche Domain "
                f"(erwartet {'/'.join(domains)})"
            )
        elif not _exists(hass, entity):
            c.errors.append(f"{ctx}: {label} {entity} existiert nicht")

    def _mark(role: str):
        if role not in c.actuated:
            c.actuated.append(role)

    # --- Speicher -----------------------------------------------------------
    for s in reg.storages:
        ctx = f"Speicher '{s.name}'"
        if not _exists(hass, s.soc_entity):
            c.errors.append(f"{ctx}: SoC-Entity {s.soc_entity} existiert nicht")
        ch, dis = s.charge_setpoint_entity, s.discharge_setpoint_entity
        _need(ch, ("number", "input_number"), ctx, "Lade-Setpoint")
        _need(dis, ("number", "input_number"), ctx, "Entlade-Setpoint")
        if bool(ch) != bool(dis):
            c.warnings.append(
                f"{ctx}: nur ein Setpoint gesetzt — Laden oder Entladen wird "
                f"im Auto-Modus nicht gestellt"
            )
        if ch or dis:
            _mark("Speicher")
        me = s.mode_entity
        _need(me, ("select", "input_select"), ctx, "Richtungs-Select")
        if me and not (s.mode_charge_option and s.mode_discharge_option):
            c.errors.append(
                f"{ctx}: Richtungs-Select gesetzt, aber mode_charge_option/"
                f"mode_discharge_option fehlt"
            )
        if (s.mode_charge_option or s.mode_discharge_option) and not me:
            c.warnings.append(
                f"{ctx}: mode_charge/discharge_option ohne Richtungs-Select — "
                f"wirkungslos"
            )
        # Freitext-Falle: mode_charge_option/mode_discharge_option müssen exakt
        # (Groß-/Kleinschreibung) einer echten Option des Richtungs-Select
        # entsprechen — sonst schlägt select_option im Auto-Modus lautlos fehl
        # (HA loggt einen Service-Fehler, aber der Config-Check bliebe grün).
        # Nur prüfbar, wenn die Entity existiert und ihre Optionen kennt.
        me_state = hass.states.get(me) if me else None
        options = me_state.attributes.get("options") if me_state else None
        if options is not None:
            for opt, label in (
                (s.mode_charge_option, "mode_charge_option"),
                (s.mode_discharge_option, "mode_discharge_option"),
            ):
                if opt and opt not in options:
                    c.errors.append(
                        f"{ctx}: {label} '{opt}' ist keine gültige Option von "
                        f"{me} (verfügbar: {', '.join(options)})"
                    )
        _need(
            s.soc_set_entity, ("number", "input_number"), ctx, "Ziel-SoC (soc_set)"
        )

    # --- Warmwasser ---------------------------------------------------------
    for t in reg.thermals:
        ctx = f"Warmwasser '{t.name}'"
        if t.control_entity:
            _mark("Warmwasser")
            _need(
                t.control_entity,
                ("water_heater", "switch", "input_boolean"),
                ctx,
                "Steuer-Entity",
            )
            # Schalter-Variante (kein water_heater): der Sollwert läuft über eine
            # separate Number. Fehlt sie, wird nur geschaltet, nie die Temperatur
            # gestellt — das gehört sichtbar gemacht, nicht still hingenommen.
            if _domain(t.control_entity) in ("switch", "input_boolean"):
                _need(
                    t.setpoint_entity,
                    ("number", "input_number"),
                    ctx,
                    "Sollwert-Number",
                )
                if not t.setpoint_entity:
                    c.warnings.append(
                        f"{ctx}: Schalter ohne Sollwert-Number — WW wird im "
                        f"Auto-Modus nur ein-/ausgeschaltet, die Temperatur "
                        f"nicht gestellt"
                    )
            elif t.setpoint_entity:
                # water_heater trägt den Sollwert selbst; eine zusätzliche
                # Number wäre wirkungslos.
                c.warnings.append(
                    f"{ctx}: Sollwert-Number gesetzt, aber Steuer-Entity ist ein "
                    f"water_heater — die Number bleibt ungenutzt"
                )
            if not (t.block_start and t.block_end and t.block_start != t.block_end):
                c.warnings.append(
                    f"{ctx}: kein Sperrfenster gesetzt — WW wird im Auto-Modus "
                    f"rund um die Uhr gehalten (kein Nacht-Aus)"
                )
        else:
            c.info.append(f"{ctx}: kein Steuer-Entity — nur Beobachtung")

    # --- Schaltbare Lasten --------------------------------------------------
    for s in reg.switchables:
        ctx = f"Schaltbare Last '{s.name}'"
        _mark("Schaltbare Lasten")
        _need(
            s.switch_entity,
            ("switch", "climate", "input_boolean"),
            ctx,
            "Schalter",
        )
        if not s.power_entity:
            # Ohne Messung lässt sich die Leistungsaufnahme nicht lernen; es
            # bleibt beim konservativ hohen Fallback. Eine kleine Last wird so
            # praktisch nie eingeschaltet.
            c.warnings.append(
                f"{ctx}: keine Leistungsmessung — HEMS rechnet dauerhaft mit "
                f"{DEFAULT_SWITCHABLE_EXPECTED_W:.0f} W und schaltet die Last "
                f"erst ab so viel Überschuss ein"
            )

    # --- Heizung ------------------------------------------------------------
    for h in reg.heatings:
        ctx = f"Heizung '{h.name}'"
        _mark("Heizung (Frostschutz + Heizkurve)")
        _need(
            h.switch_entity,
            ("switch", "climate", "input_boolean"),
            ctx,
            "Schalter",
        )
        # Ohne Temperatur greift weder Frostschutz noch Heizkurve — und HEMS
        # rührt die Anlage dann gar nicht mehr an. Das ist die sichere, aber
        # eben auch die wirkungslose Auslegung; sie gehört gesagt.
        if not h.outdoor_temp_entity and not weather:
            c.errors.append(
                f"{ctx}: keine Außentemperatur (weder eigener Sensor noch "
                f"Wetter-Entität) — Frostschutz und Heizkurve sind wirkungslos, "
                f"HEMS schaltet die Anlage weder ein noch aus"
            )
        if h.outdoor_temp_entity:
            _need(
                h.outdoor_temp_entity,
                ("sensor", "input_number", "number"),
                ctx,
                "Außentemperatur",
            )
        if h.flow_setpoint_entity:
            _need(
                h.flow_setpoint_entity,
                ("number", "input_number"),
                ctx,
                "Vorlauf-Sollwert",
            )
        # HEMS ordnet vertauschte Schwellen selbst (siehe strategies/heating.py
        # `_ordnung`) — sonst kehrte sich die Wirkung des Frostschutzes still
        # um. Gemeldet wird es trotzdem: Der eingetragene Wert gilt dann nicht
        # so, wie er dasteht, und das darf nicht stumm passieren.
        if h.frost_off_c <= h.frost_on_c:
            c.warnings.append(
                f"{ctx}: Frostschutz-Aus ({h.frost_off_c:.1f} °C) liegt nicht über "
                f"Frostschutz-Ein ({h.frost_on_c:.1f} °C) — HEMS rechnet mit "
                f"{max(h.frost_off_c, h.frost_on_c + 1):.1f} °C, damit die Wirkung "
                f"sich nicht umkehrt"
            )
        if h.heat_off_c <= h.heat_on_c:
            c.warnings.append(
                f"{ctx}: Heizgrenze-Aus ({h.heat_off_c:.1f} °C) liegt nicht über "
                f"Heizgrenze-Ein ({h.heat_on_c:.1f} °C) — HEMS rechnet mit "
                f"{max(h.heat_off_c, h.heat_on_c + 1):.1f} °C"
            )
        if h.vlt_max_c <= h.vlt_min_c:
            c.warnings.append(
                f"{ctx}: Vorlauf-Maximum ({h.vlt_max_c:.0f} °C) liegt nicht über "
                f"dem Minimum ({h.vlt_min_c:.0f} °C) — die Heizkurve ist damit flach"
            )
        if h.switch_entity.split(".")[0] == "climate" and not h.mode_heat_option:
            c.info.append(
                f"{ctx}: climate-Entität ohne Heiz-Modus — HEMS schaltet auf 'heat'"
            )
        if not h.power_entity:
            c.warnings.append(
                f"{ctx}: keine Leistungsmessung — HEMS rechnet dauerhaft mit "
                f"{DEFAULT_SWITCHABLE_EXPECTED_W:.0f} W und schaltet die Anlage "
                f"erst ab so viel Überschuss ein"
            )

    # --- E-Auto (Modulierbare Last) ----------------------------------------
    for m in reg.modulateds:
        ctx = f"E-Auto '{m.name}'"
        if m.current_entity:
            _mark("E-Auto (Überschuss + Zwang)")
            _need(m.current_entity, ("number", "input_number"), ctx, "Strom-Entity")
            if not m.power_entity:
                c.warnings.append(
                    f"{ctx}: keine Leistungsmessung (power_now) — die "
                    f"Überschussregelung braucht die Ist-Ladeleistung, um sie aus "
                    f"dem Saldo herauszurechnen; ohne sie regelt HEMS den Strom "
                    f"nicht (die externe Ladeautomation bleibt zuständig)"
                )
            if not m.switch_entity:
                c.info.append(
                    f"{ctx}: kein Schalter — Wallbox kann bei zu wenig Überschuss "
                    f"nur auf {m.min_a:.0f} A gedrosselt, nicht abgeschaltet werden"
                )
        _need(m.switch_entity, ("switch", "input_boolean"), ctx, "Schalter")

    # --- Überlappung: aktive Automationen auf HEMS-Steuer-Entities ----------
    _scan_overlaps(hass, control_entities, c)
    return c


def _scan_overlaps(
    hass: HomeAssistant, control_entities: set[str], c: ConfigCheck
) -> None:
    """Aktive Automationen nach Referenzen auf HEMS-Steuer-Entities scannen.

    Nutzt die `referenced_entities` der Automation-Entitäten (HA-intern, daher
    defensiv). Templates/indirekte Referenzen entgehen der Heuristik — sie
    fängt den häufigen Fall 'abgelöste Automation noch aktiv' ab.
    """
    if not control_entities:
        return
    try:
        component = hass.data.get("automation")
        entities = list(getattr(component, "entities", []) or [])
    except Exception:  # noqa: BLE001
        c.scan_ok = False
        return
    if component is None:
        c.scan_ok = False
        return
    for auto in entities:
        try:
            if not getattr(auto, "is_on", False):
                continue
            refs = set(getattr(auto, "referenced_entities", set()) or set())
        except Exception:  # noqa: BLE001
            continue
        hit = refs & control_entities
        if not hit:
            continue
        name = getattr(auto, "name", None) or getattr(auto, "entity_id", "?")
        for entity in sorted(hit):
            c.overlaps.append(f"{entity} ⇄ Automation „{name}“")
    if c.overlaps:
        c.warnings.append(
            "Überlappung: aktive Automationen schreiben auf HEMS-Steuer-Entities "
            "(siehe Attribut 'ueberlappung') — vor dem Auto-Modus deaktivieren"
        )
