"""Aktuierung (schalten): reagiert im Auto-Modus auf die Planner-Empfehlung.

Bewusst getrennt von *denken* (planner.py) und *messen* (coordinator.py). Der
Actuator übersetzt die fertige `PlanResult`-Empfehlung in Service-Aufrufe auf
die real konfigurierten Steuer-Entitäten — orientiert an den drei abgelösten
Automationen (WW, Wärmepumpe, Zendure-Saldo) plus E-Auto-Zwangsladung.

Prinzipien (wie die Referenz-Automationen):
- Nur schreiben, wenn ein Steuer-Entity konfiguriert ist (sonst reine
  Beobachtung, auch im Auto-Modus).
- Idempotent: nur schreiben, wenn sich der Zielwert vom Ist unterscheidet —
  kein Bus-Spam, und Geräte-Warmup/Hysterese bleiben unangetastet.
- Nie auf fehlende/unbekannte Empfehlung schreiben (Gerät wird übersprungen).
- Fehler je Gerät isoliert: ein hängendes Gerät blockiert die übrigen nicht.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .actuation import plan_ww_action
from .models import DeviceRegistry
from .strategies.types import PlanResult

_LOGGER = logging.getLogger(__name__)

# HEMS-Modus (deutsch) → Home-Assistant climate hvac_mode und zurück.

# Warmwasser: Mindestlaufzeit vor dem Abschalten (gegen Takten), wie in der
# abgelösten WW-Automation. Der Warmup nach dem Einschalten ergibt sich von
# selbst — der Sollwert wird erst im Folgezyklus (~60 s später) gesetzt.
WARMWASSER_MIN_RUNTIME = timedelta(minutes=15)

# Toleranz, ab der ein Zahl-Sollwert als "geändert" gilt (W bzw. A/°C: <1).
_EPS = 1.0

# Throttle für identische, wiederholte Service-Aufrufe. Alle Aufrufer prüfen
# den Ist-Zustand vor jedem Aufruf (siehe Klassendoc) — _call wird also nur
# dann Zyklus für Zyklus mit denselben Parametern erneut erreicht, wenn das
# Zielgerät den Befehl dauerhaft ablehnt (z. B. tote Cloud-Anbindung). Ohne
# Drossel spammt das jede Minute dieselbe Fehlermeldung ins HA-Log.
_CALL_THROTTLE = timedelta(minutes=5)

class Actuator:
    """Schaltet die Empfehlung im Auto-Modus auf die konfigurierten Geräte."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._last_call: dict[tuple, object] = {}

    async def apply(self, reg: DeviceRegistry, plan: PlanResult) -> None:
        """Reihenfolge WW → Akku → modulierbare Lasten. Jedes Gerät
        gekapselt. Die Zwangsladung ist bereits in der Empfehlung kodiert
        (plan.ev_regelung.zwang → jede Last läuft, mit dem dort berechneten
        Sollstrom zwischen Unter- und Obergrenze)."""
        await self._guard(self._apply_ww, reg, plan, name="Warmwasser")
        await self._guard(self._apply_battery, reg, plan, name="Speicher")
        await self._guard(self._apply_modulated, reg, plan, name="Lasten")
        await self._guard(self._apply_switchable, reg, plan, name="Schaltlasten")

    async def release_battery(self, reg: DeviceRegistry) -> None:
        """Akku-Setpoints einmalig auf 0/0 (passiv) setzen — beim Verlassen des
        Auto-Modus, damit der Speicher nicht mit der zuletzt kommandierten Rate
        blind weiterläuft. WW/EV bleiben unangetastet (ein Sollwert ist
        ungefährlich); ihre letzte Einstellung übernimmt der Nutzer."""
        for s in reg.storages:
            try:
                await self._set_number(s.charge_setpoint_entity, 0.0)
                await self._set_number(s.discharge_setpoint_entity, 0.0)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "HEMS-Actuator: Akku-Freigabe %s fehlgeschlagen: %s", s.name, err
                )

    async def _guard(self, fn, reg, plan, *, name) -> None:
        try:
            await fn(reg, plan)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("HEMS-Actuator: %s fehlgeschlagen: %s", name, err)

    # --- Hilfen -------------------------------------------------------------

    def _state(self, entity: str | None) -> str | None:
        if not entity:
            return None
        s = self.hass.states.get(entity)
        return s.state if s else None

    def _num_attr(self, entity: str, attr: str) -> float | None:
        s = self.hass.states.get(entity)
        if not s:
            return None
        try:
            return float(s.attributes.get(attr))
        except (TypeError, ValueError):
            return None

    async def _call(self, domain: str, service: str, entity: str, **data) -> bool:
        """Service aufrufen; ``False``, wenn die Drossel den Aufruf verworfen hat."""
        key = (domain, service, entity, tuple(sorted(data.items())))
        now = dt_util.utcnow()
        last = self._last_call.get(key)
        if last is not None and now - last < _CALL_THROTTLE:
            return False
        self._last_call[key] = now
        await self.hass.services.async_call(
            domain, service, {"entity_id": entity, **data}, blocking=False
        )
        return True

    async def _turn(self, entity: str, on: bool) -> None:
        """turn_on/turn_off auf der Domain des Entitys (switch/input_boolean …),
        nur wenn der Zustand nicht schon passt."""
        want = "on" if on else "off"
        if self._state(entity) == want:
            return
        await self._call(entity.split(".")[0], f"turn_{want}", entity)

    def _num_state(self, entity: str | None) -> float | None:
        """Zustand einer Number-Entität als float (deren Wert IST der Zustand)."""
        s = self._state(entity)
        try:
            return float(s) if s is not None else None
        except ValueError:
            return None

    async def _set_number(self, entity: str | None, value: float) -> None:
        """number.set_value, dedupliziert gegen den Ist-Wert."""
        if not entity:
            return
        cur = self._state(entity)
        try:
            if cur is not None and abs(float(cur) - value) < _EPS:
                return
        except ValueError:
            pass
        await self._call("number", "set_value", entity, value=round(value))

    # --- Warmwasser ---------------------------------------------------------

    async def _apply_ww(self, reg: DeviceRegistry, plan: PlanResult) -> None:
        """Ist-Zustand lesen, Entscheidung an die HA-freie ``plan_ww_action``
        delegieren, das Ergebnis domain-abhängig schalten. water_heater trägt
        On/Off + Sollwert selbst; ein Schalter schaltet nur, der Sollwert läuft
        dann über die separate Number-Entität (setpoint_entity)."""
        if not reg.thermals:
            return
        t = reg.thermals[0]
        ent = t.control_entity
        if not ent:
            return
        domain = ent.split(".")[0]
        state = self._state(ent)
        # Ist-Sollwert je nach Gerätetyp: water_heater trägt ihn als Attribut,
        # die Schalter-Variante als Zustand der Number-Entität.
        if domain == "water_heater":
            current_setpoint = self._num_attr(ent, "temperature")
        else:
            current_setpoint = self._num_state(t.setpoint_entity)
        s = self.hass.states.get(ent)
        min_runtime_elapsed = (
            s is None or self._age(s) >= WARMWASSER_MIN_RUNTIME
        )
        action = plan_ww_action(
            status=plan.warmwasser_status,
            soll_c=plan.warmwasser_soll_c,
            domain=domain,
            state=state,
            min_runtime_elapsed=min_runtime_elapsed,
            current_setpoint=current_setpoint,
            has_setpoint_entity=bool(t.setpoint_entity),
        )
        if action is None:
            return
        if action.kind == "turn_off":
            await self._call(domain, "turn_off", ent)
        elif action.kind == "turn_on":
            await self._call(domain, "turn_on", ent)
        elif action.kind == "set_temperature":
            await self._call(
                "water_heater", "set_temperature", ent, temperature=int(action.value)
            )
        elif action.kind == "set_number":
            await self._set_number(t.setpoint_entity, action.value)

    def _age(self, state) -> timedelta:
        return dt_util.utcnow() - state.last_changed

    # --- Speicher (Akku) ----------------------------------------------------

    async def _apply_battery(self, reg: DeviceRegistry, plan: PlanResult) -> None:
        ctrl = plan.regelung
        if ctrl is None:
            return
        alloc = {z.name: z.watt for z in ctrl.zuteilung}
        for s in reg.storages:
            # Geräteseitigen Ladedeckel setzen (z. B. Zendure soc_set): der
            # Planner deckelt das Laden über die Leistungs-Zuteilung (0 W über
            # dem Deckel), aber manche Geräte laden im Lademodus nach ihrem
            # EIGENEN Ziel-SoC weiter und ignorieren den 0-W-Setpoint. Erst der
            # auf den Deckel gezogene Ziel-SoC stoppt sie zuverlässig. Der Deckel
            # rampt abends selbst auf 100 % — die Nachtdeckung bleibt erhalten.
            if s.soc_set_entity and plan.lade_deckel_soc is not None:
                await self._set_number(s.soc_set_entity, plan.lade_deckel_soc)
            if not s.charge_setpoint_entity and not s.discharge_setpoint_entity:
                continue
            watt = alloc.get(s.name, 0.0) or 0.0
            if ctrl.modus == "laden":
                charge_w, discharge_w = watt, 0.0
            elif ctrl.modus == "entladen":
                charge_w, discharge_w = 0.0, watt
            else:  # "pausiert"
                charge_w = discharge_w = 0.0
            # Richtungs-Select (optional, z. B. Zendure ac_mode) nur beim
            # tatsächlichen Laden/Entladen stellen — in der Pause den zuletzt
            # gesetzten Modus stehen lassen. Sonst flippt der Select bei jedem
            # Deadband-Durchgang (laden ⇄ pausiert) zwischen den Optionen und
            # lässt das Gerät takten. Die 0/0-Setpoints halten den Speicher in
            # der Pause ohnehin passiv, egal in welcher Richtung der Select steht.
            if (
                s.mode_entity
                and s.mode_charge_option
                and s.mode_discharge_option
                and ctrl.modus in ("laden", "entladen")
            ):
                want = (
                    s.mode_charge_option
                    if ctrl.modus == "laden"
                    else s.mode_discharge_option
                )
                if self._state(s.mode_entity) != want:
                    await self._call(
                        s.mode_entity.split(".")[0],
                        "select_option",
                        s.mode_entity,
                        option=want,
                    )
            await self._set_number(s.charge_setpoint_entity, charge_w)
            await self._set_number(s.discharge_setpoint_entity, discharge_w)

    # --- E-Auto (nur Zwangsladung) -----------------------------------------

    async def _apply_modulated(self, reg: DeviceRegistry, plan: PlanResult) -> None:
        """Alle modulierbaren Lasten (Wallboxen) auf ihren empfohlenen Sollstrom
        stellen. Ohne Empfehlung (kein Saldo/keine Leistungsmessung) bleiben sie
        unangetastet — die externe Automation bleibt dann zuständig."""
        rec = plan.ev_regelung
        if rec is None or not reg.modulateds:
            return
        by_id = {sp.id: sp for sp in rec.lasten}
        for m in reg.modulateds:
            sp = by_id.get(m.id)
            if sp is None:
                continue
            try:
                await self._apply_one_load(m, sp)
            except Exception as err:  # noqa: BLE001 – eine Last reißt nie die andern
                _LOGGER.warning(
                    "HEMS-Actuator: Last %s fehlgeschlagen: %s", m.name, err
                )

    async def _apply_one_load(self, m, sp) -> None:
        if sp.laden and sp.strom_a is not None:
            # Laden: erst den Sollstrom stellen, dann freigeben.
            await self._set_number(m.current_entity, sp.strom_a)
            if m.switch_entity:
                await self._turn(m.switch_entity, True)
            return
        # Nicht laden: erst auf den Mindeststrom drosseln (senkt den Bezug
        # sofort, auch während einer laufenden Mindestlaufzeit), dann abschalten,
        # sobald Schalter und Mindestlaufzeit (gegen Schützflattern) es zulassen.
        # Ohne Schalter bleibt es bei der Drosselung auf den Mindeststrom.
        await self._set_number(m.current_entity, m.min_a)
        if m.switch_entity:
            s = self.hass.states.get(m.switch_entity)
            if (
                s is None
                or s.state != "on"
                or self._age(s) >= timedelta(minutes=m.min_on_min)
            ):
                await self._turn(m.switch_entity, False)

    async def _apply_switchable(self, reg: DeviceRegistry, plan: PlanResult) -> None:
        """Schaltbare Lasten auf die empfohlene An/Aus-Lage schalten. Die
        Anti-Takt-Sperren (min_on/min_off/max_block) sind bereits im Planner
        verrechnet — die Empfehlung ist endgültig; _turn schaltet nur, wenn der
        Ist-Zustand abweicht."""
        rec = plan.schaltbare
        if rec is None or not reg.switchables:
            return
        by_id = {sp.id: sp for sp in rec.lasten}
        for s in reg.switchables:
            sp = by_id.get(s.id)
            if sp is None:
                continue
            try:
                await self._turn(s.switch_entity, sp.an)
            except Exception as err:  # noqa: BLE001 – eine Last reißt nie die andern
                _LOGGER.warning(
                    "HEMS-Actuator: Schaltlast %s fehlgeschlagen: %s", s.name, err
                )
