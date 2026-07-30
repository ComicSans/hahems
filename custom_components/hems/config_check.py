"""Config-Sanity-Check: prüft die Rollen-Konfiguration gegen den Auto-Modus.

Läuft jeden Zyklus im Coordinator und speist den Diagnose-Sensor
`binary_sensor.hems_konfiguration`. Beantwortet die Scharfschalt-Frage:
Was schaltet der Auto-Modus, existieren alle Steuer-Entitäten, passen die
Domains — und (heuristisch) schreibt eine aktive Automation auf dieselbe
Steuer-Entität wie HEMS (Überlappung, die im Auto-Modus zum Kampf führt)?

Reine Prüf-Logik ohne Seiteneffekte; der Automations-Scan ist defensiv
gekapselt (fällt bei HA-interner Änderung auf "nicht verfügbar" zurück, statt
den Sensor zu reißen).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant

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


def _domain(entity: str | None) -> str | None:
    return entity.split(".")[0] if entity else None


def _exists(hass: HomeAssistant, entity: str | None) -> bool:
    return bool(entity) and hass.states.get(entity) is not None


def check_config(hass: HomeAssistant, reg: DeviceRegistry) -> ConfigCheck:
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

    # --- Heizkreis (Wärmepumpe) --------------------------------------------
    for h in reg.heatings:
        ctx = f"Heizkreis '{h.name}'"
        # Warmwasser-Rückmeldung: reine Leserolle, deshalb kein _need: sie
        # gehört nicht in den Overlap-Scan (eine Automation, die darauf
        # schreibt, ist kein Konflikt mit HEMS). Antwortet sie nicht, setzt
        # HEMS die Heizkreis-Regelung während der Ladung nicht mehr aus; das
        # ist eine Warnung wert, aber kein Fehler; ohne die Rolle lief HEMS
        # vorher genauso.
        if h.dhw_active_entity and not _exists(hass, h.dhw_active_entity):
            c.warnings.append(
                f"{ctx}: Warmwasser-Rückmeldung {h.dhw_active_entity} existiert "
                f"nicht: HEMS erkennt laufende Speicherladungen nicht und "
                f"stellt den Heizkreis auch währenddessen"
            )
        # Verdichter-Rückmeldung: ebenfalls reine Leserolle. Fehlt sie, zählt
        # HEMS keine Starts und legt nie eine Zwangspause ein.
        if h.compressor_entity and not _exists(hass, h.compressor_entity):
            c.warnings.append(
                f"{ctx}: Verdichter-Rückmeldung {h.compressor_entity} existiert "
                f"nicht: der Taktschutz zählt keine Starts und pausiert nie"
            )
        if h.control_entity:
            _mark("Wärmepumpe")
            _need(
                h.control_entity,
                ("climate", "select", "input_select"),
                ctx,
                "Steuer-Entity",
            )
            if _domain(h.control_entity) in ("select", "input_select"):
                # Select-Steuerung: der Vorlauf-Soll läuft über eine Number, und
                # HEMS muss wissen, welche Select-Optionen heizen/aus bedeuten.
                _need(
                    h.setpoint_entity,
                    ("number", "input_number"),
                    ctx,
                    "Vorlauf-Sollwert-Number",
                )
                if not (h.mode_heat_option and h.mode_off_option):
                    c.errors.append(
                        f"{ctx}: Modus-Select ohne mode_heat_option/"
                        f"mode_off_option — HEMS kann heizen/aus nicht stellen"
                    )
                # Freitext-Falle: die Optionen müssen exakt echten Optionen des
                # Select entsprechen, sonst schlägt select_option lautlos fehl
                # (wie beim Speicher-Richtungs-Select).
                ce_state = hass.states.get(h.control_entity)
                options = ce_state.attributes.get("options") if ce_state else None
                if options is not None:
                    for opt, label in (
                        (h.mode_heat_option, "mode_heat_option"),
                        (h.mode_cool_option, "mode_cool_option"),
                        (h.mode_off_option, "mode_off_option"),
                    ):
                        if opt and opt not in options:
                            c.errors.append(
                                f"{ctx}: {label} '{opt}' ist keine gültige Option "
                                f"von {h.control_entity} "
                                f"(verfügbar: {', '.join(options)})"
                            )
            elif (
                h.setpoint_entity
                or h.mode_heat_option
                or h.mode_cool_option
                or h.mode_off_option
            ):
                # climate trägt Modus und Sollwert selbst.
                c.warnings.append(
                    f"{ctx}: Vorlauf-Number/Modus-Optionen gesetzt, aber "
                    f"Steuer-Entity ist ein climate — diese Felder bleiben "
                    f"ungenutzt"
                )
            _need(
                h.silent_switch_entity,
                ("switch", "input_boolean"),
                ctx,
                "Silent-Schalter",
            )
            _need(
                h.season_select_entity,
                ("input_select", "select"),
                ctx,
                "Saison-Select",
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
        if s.heat_coupled and not reg.heatings:
            c.info.append(
                f"{ctx}: als heizungsgekoppelt markiert, aber kein Heizkreis "
                f"konfiguriert — ohne Außentemperatur gibt es kein "
                f"Verbrauchsmodell"
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
