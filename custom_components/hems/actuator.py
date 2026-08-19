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
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import entity_domain
from .actuation import (
    ladeauftrag_in_frist_erfuellbar,
    plan_soc_set,
    plan_ww_action,
    speicher_folgt,
    speicher_modus_option,
)
from .models import DeviceRegistry, Storage
from .strategies.types import PlanResult

_LOGGER = logging.getLogger(__name__)

# Warmwasser: Mindestabstand zwischen zwei Schaltvorgängen. Gilt in BEIDE
# Richtungen — die Sperre schützt vor Takten, und Takten entsteht aus dem
# Wechsel, nicht aus einer Richtung. Bis dahin galt eine Mindestlaufzeit von 15
# Minuten allein vor dem Abschalten; Einschalten war ungebremst, ein Gerät
# konnte also unmittelbar nach dem Abschalten wieder anlaufen.
#
# Gemessen wird über `last_changed` des Steuer-Entitys, also die letzte echte
# Schaltkante, gleich wer sie ausgelöst hat: Verschleiß entsteht am Gerät, nicht
# am Urheber. Sollwert-Schreibvorgänge setzen die Uhr nicht zurück — ein
# `set_temperature` berührt nur Attribute, und der Sollwert soll dem Überschuss
# weiter im Minutentakt folgen dürfen.
WARMWASSER_MIN_SCHALTABSTAND = timedelta(minutes=30)

# Toleranz, ab der ein Zahl-Sollwert als "geändert" gilt (W bzw. A/°C: <1).
_EPS = 1.0

# Frist, nach der eine geschriebene Warmwasser-Freigabe im Ist-Zustand
# angekommen sein muss. Gemessen am 01.08.2026 an einer LG Therma V: Der Coil
# fiel 4 bis 30 s nach jedem Schreibversuch wieder auf "aus" zurück, während die
# Anlage stand. Zwei Minuten liegen weit über dem 30-s-Abfragetakt des Geräts,
# melden also keinen regulären Schaltvorgang als Nicht-Übernahme.
#
# Die Frist und nicht "es gab seit dem Schreiben einen neuen Ist-Wert": Ein
# Entity, das den Befehl ignoriert, ändert seinen Zustand nicht, und ein
# unveränderter Zustand wird nicht neu veröffentlicht. Genau im Fehlerfall wäre
# diese Bedingung nie erfüllt.
WARMWASSER_QUITTUNG_FRIST = timedelta(minutes=2)

# Dieselbe Frist für den Wärmeerzeuger. Am 04.08.2026 an einer LG Therma V
# gemessen: HEMS schrieb `set_hvac_mode: off`, die climate-Entität übernahm es,
# und die Anlage kühlte trotzdem weiter — Verdichter und Außeneinheit liefen,
# 784 W. Ein Aus, das die Anlage nicht ausführt, ist eine Falschmeldung im
# Lastfluss und ein Rätsel für den, der davorsteht.
#
# Gemeldet, nicht wiederholt: Nachtreten hilft einem Gerät nicht, das den
# Befehl entgegennimmt und ignoriert, und ein Schaltbefehl je Zyklus wäre für
# den Verdichter das Gegenteil von Anti-Takt.
HEIZUNG_QUITTUNG_FRIST = timedelta(minutes=2)

# Throttle für identische, wiederholte Service-Aufrufe. Alle Aufrufer prüfen
# den Ist-Zustand vor jedem Aufruf (siehe Klassendoc) — _call wird also nur
# dann Zyklus für Zyklus mit denselben Parametern erneut erreicht, wenn das
# Zielgerät den Befehl dauerhaft ablehnt (z. B. tote Cloud-Anbindung). Ohne
# Drossel spammt das jede Minute dieselbe Fehlermeldung ins HA-Log.
#
# Die Ausnahme dazu (`ohne_drossel`) gilt Werten, die das Gerät ANNIMMT und von
# selbst wieder verwirft — dort ist die Annahme oben schlicht falsch. Am
# 14.08.2026 an drei Zendure Hyper 2000 gemessen: Der geschriebene Ziel-SoC
# hielt 10 bis 70 s, dann zog das Gerät ihn auf seinen eigenen Wert zurück
# (100 → 27 bzw. 100 → 70). Mit Drossel schreibt HEMS erst fünf Minuten später
# nach, und so lange steht ein Ladedeckel auf Höhe des Ist-SoC — der Speicher
# lädt nicht. Nachschreiben ist da kein Spam, sondern die einzige Art, den Wert
# zu halten. Für Schaltbefehle (WW, Heizung) bleibt „einmal schreiben, dann
# melden" richtig: Ein Verdichter braucht kein Nachtreten im Minutentakt.
_CALL_THROTTLE = timedelta(minutes=5)

# Frist, nach der eine kommandierte Speicherleistung gemessen sein muss —
# dieselbe Klasse wie WARMWASSER_QUITTUNG_FRIST und HEIZUNG_QUITTUNG_FRIST.
# Ein Speicher, der die zugeteilte Leistung entgegennimmt und nichts zieht,
# steht in Empfehlung und Lastfluss als ladend, während der Überschuss ins Netz
# geht: am 14.08.2026 26 Minuten lang unbemerkt, rund 1,2 kW.
#
# Gilt in BEIDE Richtungen. Bis dahin war nur das Laden quittiert, und der
# Entladefall blieb strukturell unsichtbar — am 15.08.2026 fünfeinhalb Stunden
# lang: Die Zuteilung stand auf einem Hyper 2000, dessen Telemetrie seit 12:54
# eingefroren war, die anderen beiden bekamen 0 W, und das Haus zog derweil
# 1,1 kW aus dem Netz bei 95,7 % gemeldetem SoC. Wer nur eine Richtung prüft,
# meldet genau die Hälfte der Fälle.
#
# Fünf Minuten, weil das Anlaufen eines Speichers Sekunden dauert, ein
# Deadband-Durchgang der Regelung aber kurz auf 0 W führen kann — die Frist
# soll den Dauerfall melden, nicht jede Lücke.
SPEICHER_QUITTUNG_FRIST = timedelta(minutes=5)

class Actuator:
    """Schaltet die Empfehlung im Auto-Modus auf die konfigurierten Geräte."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._last_call: dict[tuple, object] = {}
        # Steuer-Entity → (zuletzt geschriebene Warmwasser-Freigabe, Zeitpunkt).
        # Nur was tatsächlich rausging, siehe _apply_ww. Nach einem Neustart
        # leer: dann verhält sich die Aktuierung wie vor dieser Buchführung.
        self._last_ww: dict[str, tuple[bool, datetime]] = {}
        # Steuer-Entity → (zuletzt geschriebene An/Aus-Lage, Zeitpunkt) für
        # Wärmeerzeuger. Der Eintrag verfällt, sobald die Anlage die Lage zeigt.
        self._last_heizung: dict[str, tuple[bool, datetime]] = {}
        # Entitäten, deren Nicht-Übernahme bereits im Log steht — die Meldung
        # gehört einmal je Vorfall ins Log, nicht in jeden Zyklus.
        self._heizung_gemeldet: set[str] = set()
        # Speichername → Zeitpunkt, seit dem Leistung kommandiert ist, ohne
        # dass sie gemessen wurde. Wird zurückgesetzt, sobald Leistung fließt,
        # die Richtung wechselt oder nichts mehr kommandiert ist.
        self._leistung_seit: dict[str, tuple[bool, datetime]] = {}
        self._speicher_gemeldet: set[str] = set()

    async def apply(
        self, reg: DeviceRegistry, plan: PlanResult, *, invers: bool = False
    ) -> None:
        """Reihenfolge WW → Akku → modulierbare Lasten. Jedes Gerät
        gekapselt. Die Zwangsladung ist bereits in der Empfehlung kodiert
        (plan.ev_regelung.zwang → jede Last läuft, mit dem dort berechneten
        Sollstrom zwischen Unter- und Obergrenze).

        ``invers`` reicht den Invers-Modus durch: nur der Richtungs-Select des
        Speichers wird vertauscht gestellt (siehe ``speicher_modus_option``).
        Bewusst als Argument je Aufruf und nicht als Feld am Actuator — der
        Betriebsmodus gehört dem Coordinator, eine Kopie hier ginge schal.
        """
        await self._guard(self._apply_ww, reg, plan, name="Warmwasser")
        await self._guard(self._apply_battery, reg, plan, name="Speicher", invers=invers)
        await self._guard(self._apply_modulated, reg, plan, name="Lasten")
        await self._guard(self._apply_switchable, reg, plan, name="Schaltlasten")
        await self._guard(self._apply_heating, reg, plan, name="Heizung")

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

    async def _guard(self, fn, reg, plan, *, name, **kwargs) -> None:
        try:
            await fn(reg, plan, **kwargs)
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

    async def _call(
        self,
        domain: str,
        service: str,
        entity: str,
        *,
        ohne_drossel: bool = False,
        **data,
    ) -> bool:
        """Service aufrufen; ``False``, wenn die Drossel den Aufruf verworfen hat.

        ``ohne_drossel`` ist für Werte, die das Zielgerät annimmt und von selbst
        wieder verwirft — dort ist Nachschreiben die Aufgabe, nicht der Fehler
        (siehe Kommentar bei ``_CALL_THROTTLE``).
        """
        key = (domain, service, entity, tuple(sorted(data.items())))
        now = dt_util.utcnow()
        last = self._last_call.get(key)
        if not ohne_drossel and last is not None and now - last < _CALL_THROTTLE:
            return False
        self._last_call[key] = now
        await self.hass.services.async_call(
            domain, service, {"entity_id": entity, **data}, blocking=False
        )
        return True

    async def _turn(
        self,
        entity: str,
        on: bool,
        heat_mode: str | None = None,
        cool_mode: str | None = None,
        art: str = entity_domain.BETRIEBSART_HEIZEN,
    ) -> None:
        """Steuer-Entität ein-/ausschalten, nur wenn die Lage nicht schon passt.

        Der Vergleich läuft über `entity_domain.ist_an` statt über den rohen
        Zustandsstring: Eine `climate`-Entität steht auf `heat`/`auto`/`off`,
        nie auf `on`. Mit einem `== "on"`-Vergleich wäre die Einschaltrichtung
        nie deckungsgleich — HEMS würde bei jeder Gelegenheit erneut schalten
        und dabei ausgerechnet den Service rufen (`climate.turn_on`), den viele
        Integrationen gar nicht anbieten.

        **Nicht abschalten, was HEMS nicht einordnen kann.** Läuft eine
        `climate`-Entität in einem Modus, den die Konfiguration weder als Heizen
        noch als Kühlen ausweist (typisch `heat_cool`/`auto`), bleibt sie in
        Ruhe. Das gilt hier und damit für beide Rollen — auch für eine
        Schaltlast, die gar keine Witterungsführung hat und deren Planungspfad
        den Unterschied nie zu sehen bekäme. Die Aus-Richtung ist die einzige,
        die Schaden anrichtet: Einschalten trifft immer einen zugeordneten
        Modus, weil `schalt_service` genau die konfigurierten schreibt.
        """
        ist = self._state(entity)
        if entity_domain.ist_an(entity, ist) == on:
            return
        if not on and (
            entity_domain.betriebsart(entity, ist, heat_mode, cool_mode)
            == entity_domain.BETRIEBSART_FREMD
        ):
            return
        domain, service, data = entity_domain.schalt_service(
            entity, on, heat_mode, cool_mode, art
        )
        await self._call(domain, service, entity, **data)

    def _num_state(self, entity: str | None) -> float | None:
        """Zustand einer Number-Entität als float (deren Wert IST der Zustand)."""
        s = self._state(entity)
        try:
            return float(s) if s is not None else None
        except ValueError:
            return None

    async def _set_number(
        self, entity: str | None, value: float, *, ohne_drossel: bool = False
    ) -> None:
        """number.set_value, dedupliziert gegen den Ist-Wert."""
        if not entity:
            return
        cur = self._state(entity)
        try:
            if cur is not None and abs(float(cur) - value) < _EPS:
                return
        except ValueError:
            pass
        await self._call(
            "number",
            "set_value",
            entity,
            ohne_drossel=ohne_drossel,
            value=round(value),
        )

    # --- Warmwasser ---------------------------------------------------------

    def _quittierte_ww(self, entity: str) -> bool | None:
        """Zuletzt geschriebene Freigabe, sobald sie angekommen sein müsste.

        ``None`` heißt „noch nichts zu sagen": entweder wurde nie geschrieben,
        oder die Frist läuft noch. Die Begründung für die Frist steht bei
        ``WARMWASSER_QUITTUNG_FRIST``.
        """
        letzte = self._last_ww.get(entity)
        if letzte is None:
            return None
        zustand, geschrieben_am = letzte
        if dt_util.utcnow() - geschrieben_am < WARMWASSER_QUITTUNG_FRIST:
            return None
        return zustand

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
        # `last_changed` ist nach einem HA-Neustart frisch gesetzt, obwohl die
        # Anlage seit Stunden unverändert läuft. Die Sperre allein daran zu
        # hängen, hieße: ein kalter Speicher bleibt nach jedem Neustart eine
        # halbe Stunde kalt. Solange HEMS in dieser Laufzeit noch gar nicht
        # geschaltet hat, ist der erste Schaltvorgang deshalb frei — danach
        # greift der Abstand wie beschrieben.
        schaltabstand_erreicht = (
            s is None
            or ent not in self._last_ww
            or self._age(s) >= WARMWASSER_MIN_SCHALTABSTAND
        )
        wp = plan_ww_action(
            status=plan.warmwasser_status,
            soll_c=plan.warmwasser_soll_c,
            domain=domain,
            state=state,
            schaltabstand_erreicht=schaltabstand_erreicht,
            current_setpoint=current_setpoint,
            has_setpoint_entity=bool(t.setpoint_entity),
            last_written_on=self._quittierte_ww(ent),
        )
        # Beobachtung aus der Aktuierung zurück in die Empfehlung: Sensor und
        # Entscheidungs-Log hängen am Plan, und der Coordinator liest ihn erst
        # nach diesem Aufruf.
        plan.warmwasser_nicht_uebernommen = wp.nicht_uebernommen
        if wp.nicht_uebernommen:
            _LOGGER.warning(
                "HEMS-Actuator: %s hat die Warmwasser-Freigabe nicht übernommen "
                "(zeigt weiter '%s') — HEMS schreibt erneut",
                ent,
                state,
            )
        action = wp.action
        if action is None:
            return
        if action.kind in ("turn_on", "turn_off"):
            an = action.kind == "turn_on"
            # Nur buchen, was tatsächlich rausging: ein gedrosselter Aufruf darf
            # weder den einmaligen Rückweg verbrauchen noch eine
            # Nicht-Übernahme melden, die niemand geschrieben hat.
            if await self._call(domain, action.kind, ent):
                self._last_ww[ent] = (an, dt_util.utcnow())
        elif action.kind == "set_temperature":
            await self._call(
                "water_heater", "set_temperature", ent, temperature=int(action.value)
            )
        elif action.kind == "set_number":
            await self._set_number(t.setpoint_entity, action.value)

    def _age(self, state) -> timedelta:
        return dt_util.utcnow() - state.last_changed

    # --- Speicher (Akku) ----------------------------------------------------

    async def _apply_battery(
        self, reg: DeviceRegistry, plan: PlanResult, *, invers: bool = False
    ) -> None:
        ctrl = plan.regelung
        if ctrl is None:
            return
        alloc = {z.name: z.watt for z in ctrl.zuteilung}
        for s in reg.storages:
            watt = alloc.get(s.name, 0.0) or 0.0
            # Soll dieser Speicher jetzt laden? Nicht „der Modus heißt laden":
            # Die Zuteilung kann für einen einzelnen Speicher 0 W sein (voll,
            # über dem Deckel, Kaltreserve), und dann gilt der Deckel für ihn
            # ungeschmälert.
            laedt_soll = ctrl.modus == "laden" and watt > 0
            entlaedt_soll = ctrl.modus == "entladen" and watt > 0
            # Geräteseitigen Ladedeckel setzen (z. B. Zendure soc_set): der
            # Planner deckelt das Laden über die Leistungs-Zuteilung (0 W über
            # dem Deckel), aber manche Geräte laden im Lademodus nach ihrem
            # EIGENEN Ziel-SoC weiter und ignorieren den 0-W-Setpoint. Erst der
            # auf den Deckel gezogene Ziel-SoC stoppt sie zuverlässig. Der Deckel
            # rampt abends selbst auf das Nacht-Ziel — die Deckung bleibt
            # erhalten. Warum der Ziel-SoC beim Laden Kopfraum über dem Ist
            # braucht und warum hier ohne Drossel geschrieben wird, steht bei
            # `plan_soc_set` bzw. `_CALL_THROTTLE`.
            if s.soc_set_entity and plan.lade_deckel_soc is not None:
                await self._set_number(
                    s.soc_set_entity,
                    plan_soc_set(
                        deckel_soc=plan.lade_deckel_soc,
                        laden_statt_einspeisen=ctrl.laden_statt_einspeisen,
                        laedt=laedt_soll,
                        ist_soc=self._num_state(s.soc_entity),
                    ),
                    ohne_drossel=True,
                )
            self._quittung_speicher(
                s, plan, laedt_soll, entlaedt_soll, zugeteilt_w=watt
            )
            if not s.charge_setpoint_entity and not s.discharge_setpoint_entity:
                continue
            if ctrl.modus == "laden":
                charge_w, discharge_w = watt, 0.0
            elif ctrl.modus == "entladen":
                charge_w, discharge_w = 0.0, watt
            else:  # "pausiert"
                charge_w = discharge_w = 0.0
            # Richtungs-Select (optional, z. B. Zendure ac_mode). Welche Option
            # fällig ist — und wann gar keine — entscheidet die HA-freie
            # `speicher_modus_option`; dort steht auch, warum der Invers-Modus
            # ausschließlich hier wirkt.
            want = speicher_modus_option(
                ctrl.modus,
                lade_option=s.mode_charge_option,
                entlade_option=s.mode_discharge_option,
                invers=invers,
            )
            if s.mode_entity and want and self._state(s.mode_entity) != want:
                await self._call(
                    s.mode_entity.split(".")[0],
                    "select_option",
                    s.mode_entity,
                    option=want,
                )
            await self._set_number(s.charge_setpoint_entity, charge_w)
            await self._set_number(s.discharge_setpoint_entity, discharge_w)

    def _quittung_speicher(
        self,
        s: Storage,
        plan: PlanResult,
        laden_soll: bool,
        entladen_soll: bool,
        *,
        zugeteilt_w: float = 0.0,
    ) -> None:
        """Kommandierte Speicherleistung gegen die gemessene halten.

        Der Gegenpart zu den Quittungen bei Warmwasser und Heizung, und aus
        demselben Anlass: Ein Gerät, das den Befehl entgegennimmt und nichts
        tut, ist von einem arbeitenden Gerät nur an der Messung zu
        unterscheiden. Ohne Leistungssensor gibt es nichts zu quittieren.

        **Beide Richtungen.** Ein nicht ausgeführtes Entladen kostet dasselbe
        wie ein nicht ausgeführtes Laden, nur andersherum: Der Bezug, den der
        Speicher decken sollte, kommt aus dem Netz. Und weil die Zuteilung
        greedy bündelt, hängt an einem stummen Speicher regelmäßig die GANZE
        Anforderung — die übrigen stehen dann mit 0 W daneben.

        Die Uhr merkt sich die Richtung mit: Ein Wechsel laden ⇄ entladen ist
        ein neuer Befehl und startet die Frist neu, statt die Wartezeit der
        alten Richtung zu erben.

        Gemeldet, nicht nachgetreten: Die Setpoints gehen ohnehin jeden Zyklus
        erneut raus, und ein Speicher, der sie ignoriert, braucht kein
        zusätzliches Schreiben, sondern jemanden, der hinschaut.

        **Nur beim Laden gilt die Ausnahme für den fertigen Auftrag**
        (`ladeauftrag_in_frist_erfuellbar`). Ein voller Akku, der keine Ladung
        mehr nimmt, ist fertig, kein Ausfall — der 19.08.2026 steht bei jener
        Funktion. Der Entlade-Zweig bleibt unangetastet: Dort war der Befund vom
        15.08.2026 echt (eingefrorene 100 %, volle Anforderung, keine Leistung),
        und ein „voller" Speicher ist genau der, der entladen können muss.
        """
        if not (laden_soll or entladen_soll) or not s.power_entity:
            self._leistung_seit.pop(s.name, None)
            self._speicher_gemeldet.discard(s.name)
            return
        if laden_soll and ladeauftrag_in_frist_erfuellbar(
            ist_soc=self._num_state(s.soc_entity),
            # Über den Deckel hinaus geladen wird nur, wenn der Überschuss sonst
            # einspeisen würde — dann ist 100 % die Grenze, gegen die zu rechnen
            # ist (siehe `laden_statt_einspeisen` in der Speicher-Strategie).
            grenze_soc=100.0
            if plan.regelung and plan.regelung.laden_statt_einspeisen
            else plan.lade_deckel_soc,
            capacity_kwh=s.capacity_kwh,
            zugeteilt_w=zugeteilt_w,
            frist_h=SPEICHER_QUITTUNG_FRIST.total_seconds() / 3600,
        ):
            # Wie „kein Befehl": Uhr und Meldeflagge zurück, damit ein späterer
            # echter Ladeauftrag mit voller Frist neu anläuft.
            self._leistung_seit.pop(s.name, None)
            self._speicher_gemeldet.discard(s.name)
            return
        now = dt_util.utcnow()
        vorher = self._leistung_seit.get(s.name)
        if vorher is None or vorher[0] != laden_soll:
            # Richtungswechsel ist ein neuer Befehl: Uhr UND Meldeflagge zurück.
            # Ohne das Zurücksetzen bliebe die Meldung der alten Richtung stehen
            # und unterdrückte die Warnung für die neue.
            vorher = (laden_soll, now)
            self._leistung_seit[s.name] = vorher
            self._speicher_gemeldet.discard(s.name)
        gemessen = self._num_state(s.power_entity)
        if speicher_folgt(gemessen, laden=laden_soll):
            # Es fließt — die Uhr läuft erst wieder ab der nächsten Lücke.
            self._leistung_seit[s.name] = (laden_soll, now)
            self._speicher_gemeldet.discard(s.name)
            return
        if now - vorher[1] < SPEICHER_QUITTUNG_FRIST:
            return
        if s.name not in self._speicher_gemeldet:
            self._speicher_gemeldet.add(s.name)
            _LOGGER.warning(
                "HEMS-Actuator: Speicher %s %s nicht, obwohl seit %d min "
                "Leistung zugeteilt ist (gemessen: %s W) — %s",
                s.name,
                "lädt" if laden_soll else "entlädt",
                SPEICHER_QUITTUNG_FRIST.total_seconds() // 60,
                gemessen,
                "der Überschuss geht ins Netz"
                if laden_soll
                else "der Bezug kommt aus dem Netz",
            )
        if s.name not in plan.speicher_nicht_uebernommen:
            plan.speicher_nicht_uebernommen.append(s.name)

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
                # Eine Schaltlast kennt keine Betriebsart; ein climate-Modus,
                # den `mode_heat_option` nicht abdeckt, bleibt darum
                # unangetastet (siehe _turn).
                await self._turn(s.switch_entity, sp.an, s.mode_heat_option)
            except Exception as err:  # noqa: BLE001 – eine Last reißt nie die andern
                _LOGGER.warning(
                    "HEMS-Actuator: Schaltlast %s fehlgeschlagen: %s", s.name, err
                )

    # --- Heizung ------------------------------------------------------------

    async def _turn_heizung(self, h, on: bool, art: str, plan: PlanResult) -> None:
        """Wärmeerzeuger schalten — mit Übernahme-Kontrolle.

        Wie `_turn`, plus die Buchführung darüber, ob der Befehl gewirkt hat.
        Sie liegt hier und nicht in `_turn`, weil nur der Wärmeerzeuger einen
        Verdichter hat, den wiederholtes Schalten beschädigt — eine Steckdose
        darf ein verlorenes „aus" jederzeit neu bekommen.

        Zeigt die Anlage die geschriebene Lage nach `HEIZUNG_QUITTUNG_FRIST`
        immer noch nicht, wird das gemeldet und **nicht** nachgeschrieben. Wer
        einen Befehl entgegennimmt und ignoriert, tut es beim zweiten Mal auch.
        """
        ent = h.switch_entity
        ist = self._state(ent)
        if entity_domain.ist_an(ent, ist) == on:
            # Lage erreicht — Buchung und Meldung sind erledigt.
            self._last_heizung.pop(ent, None)
            self._heizung_gemeldet.discard(ent)
            return
        if not on and (
            entity_domain.betriebsart(
                ent, ist, h.mode_heat_option, h.mode_cool_option
            )
            == entity_domain.BETRIEBSART_FREMD
        ):
            return

        now = dt_util.utcnow()
        letzt = self._last_heizung.get(ent)
        if letzt is not None and letzt[0] == on:
            if now - letzt[1] < HEIZUNG_QUITTUNG_FRIST:
                return
            if ent not in self._heizung_gemeldet:
                self._heizung_gemeldet.add(ent)
                _LOGGER.warning(
                    "HEMS-Actuator: %s hat '%s' nicht übernommen (zeigt weiter "
                    "'%s') — HEMS schreibt nicht erneut",
                    ent,
                    "an" if on else "aus",
                    ist,
                )
            if h.name not in plan.heizung_nicht_uebernommen:
                plan.heizung_nicht_uebernommen.append(h.name)
            return

        domain, service, data = entity_domain.schalt_service(
            ent, on, h.mode_heat_option, h.mode_cool_option, art
        )
        # Nur buchen, was tatsächlich rausging: ein gedrosselter Aufruf darf
        # keine Nicht-Übernahme melden, die niemand geschrieben hat.
        if await self._call(domain, service, ent, **data):
            self._last_heizung[ent] = (on, now)

    async def _apply_heating(self, reg: DeviceRegistry, plan: PlanResult) -> None:
        """Wärmeerzeuger stellen: Vorlauf-Sollwert und An/Aus-Lage.

        Bewusst NICHT an `plan.schaltbare` gebunden. Die Überschuss-Empfehlung
        fehlt, sobald der Netzzähler unerreichbar ist (`saldo_w is None`) — und
        genau dann liefe eine zuvor abgeschaltete Heizung ohne diesen eigenen
        Weg unbegrenzt weiter aus. Der Frostschutz hängt allein an der
        Temperatur, also wird er hier auch allein daraus gestellt.

        Reguläres Ein-/Ausschalten bleibt dagegen Sache der Überschussregelung:
        liegt keine Empfehlung vor, rührt HEMS die Lage nicht an. Zwischen
        beiden steht `nicht_abschalten` (keine Außentemperatur bekannt) — dann
        wird gar nichts geschaltet, in keine Richtung.
        """
        rec = plan.heizung
        if rec is None or not reg.heatings:
            return
        empfehlung = (
            {sp.id: sp for sp in plan.schaltbare.lasten}
            if plan.schaltbare is not None
            else {}
        )
        for h in reg.heatings:
            sp = rec.by_id(h.id)
            if sp is None:
                continue
            try:
                if sp.vorlauf_c is not None:
                    await self._set_number(h.flow_setpoint_entity, sp.vorlauf_c)
                if not h.switch_entity:
                    continue
                if sp.zwang_an:
                    await self._turn_heizung(h, True, sp.betriebsart, plan)
                elif sp.nicht_abschalten:
                    continue
                elif (lage := empfehlung.get(h.id)) is not None:
                    await self._turn_heizung(h, lage.an, sp.betriebsart, plan)
            except Exception as err:  # noqa: BLE001 – eine Anlage reißt nie die andern
                _LOGGER.warning(
                    "HEMS-Actuator: Heizung %s fehlgeschlagen: %s", h.name, err
                )
