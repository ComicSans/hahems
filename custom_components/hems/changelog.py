"""Persistenter Änderungs-Log der HEMS-Entscheidungen.

Der Coordinator baut nach jedem Planlauf eine Momentaufnahme der Entscheidungs-
felder (Betriebsmodus, Ziel, Akku-Regelung, Warmwasser, Wärmepumpe, Wallbox)
und vergleicht sie mit der des Vorlaufs. Nur die tatsächlichen Änderungen werden
als lesbare Einträge fortgeschrieben — kontinuierliche Werte (Watt, Vorlauf-
temperatur) fließen kategorial ein (Modus bzw. auf volle Grad gerundet), damit
der 60-s-Takt den Log nicht flutet.

Persistiert über `helpers.storage.Store`, damit der Verlauf einen Neustart
überlebt; Einträge älter als eine Woche (oder über der Obergrenze) werden bei
jedem Schreiben verworfen — „Daten älter einer Woche sind nicht mehr relevant".
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_STORE_VERSION = 1
_STORE_KEY = f"{DOMAIN}_changelog"
_SAVE_DELAY = 30  # s: gebündeltes Schreiben statt eines Writes je Eintrag
_MAX_AGE_S = 7 * 24 * 3600
_MAX_ENTRIES = 5000  # harte Obergrenze als Notnagel gegen Ausreißer

# Anzeigetexte der Rohwerte (die Roh-States sind bereits deutsch, aber teils
# klein/uneinheitlich geschrieben).
_MODE_LABEL = {"beobachten": "beobachten", "auto": "auto", "aus": "aus"}
_GOAL_LABEL = {
    "eigenverbrauch": "Eigenverbrauch",
    "nulleinspeisung": "Nulleinspeisung",
    "vollladen": "Vollladen",
}
_AKKU_LABEL = {"laden": "Laden", "entladen": "Entladen", "pausiert": "Pausiert"}
_WW_LABEL = {
    "aus": "aus (Sperrzeit)",
    "legionellenschutz": "Legionellenschutz",
    "pv_boost": "PV-Boost",
    "basis": "Basis",
}
_WP_LABEL = {"heizen": "heizen", "kuehlen": "kühlen", "aus": "aus", "unbekannt": "unbekannt"}

# key → (Titel, Kategorie). Reihenfolge bestimmt die Ausgabe je Zyklus.
_DECISION_FIELDS: dict[str, tuple[str, str]] = {
    "modus": ("Betriebsmodus", "modus"),
    "ziel": ("Optimierungsziel", "ziel"),
    "ev_force": ("E-Auto Zwangsladung", "ev"),
    "akku_modus": ("Akku-Regelung", "akku"),
    "akku_reserve": ("Akku-Kaltreserve", "akku"),
    "ww_status": ("Warmwasser", "ww"),
    "ww_soll": ("Warmwasser-Sollwert", "ww"),
    "wp_modus": ("Wärmepumpe", "wp"),
    "wp_vlt": ("WP-Vorlauf", "wp"),
}


def decision_snapshot(mode: str, goal: str, ev_force: bool, plan: Any) -> dict:
    """Momentaufnahme der Entscheidungsfelder als ``key -> (vergleich, anzeige)``.

    ``vergleich`` steuert die Änderungserkennung (kategorial, damit ein
    driftender Watt-/Grad-Wert nicht jeden Zyklus auslöst), ``anzeige`` ist der
    lesbare Text für den Log-Eintrag.
    """
    snap: dict[str, tuple] = {
        "modus": (mode, _MODE_LABEL.get(mode, mode)),
        "ziel": (goal, _GOAL_LABEL.get(goal, goal)),
        "ev_force": (bool(ev_force), "an" if ev_force else "aus"),
    }

    reg = getattr(plan, "regelung", None)
    if reg is not None:
        disp = _AKKU_LABEL.get(reg.modus, reg.modus)
        if reg.modus != "pausiert":
            disp = f"{disp} {round(abs(reg.soll_w))} W"
        snap["akku_modus"] = (reg.modus, disp)
        snap["akku_reserve"] = (
            bool(reg.reserve_aktiv),
            "Kaltreserve aktiv" if reg.reserve_aktiv else "Kaltreserve inaktiv",
        )

    if getattr(plan, "ww_status", ""):
        snap["ww_status"] = (
            plan.ww_status,
            _WW_LABEL.get(plan.ww_status, plan.ww_status),
        )
    if getattr(plan, "ww_soll_c", None) is not None:
        soll = round(plan.ww_soll_c)
        snap["ww_soll"] = (soll, f"{soll} °C")

    heiz = getattr(plan, "heizung", None)
    if heiz is not None:
        snap["wp_modus"] = (heiz.modus, _WP_LABEL.get(heiz.modus, heiz.modus))
        if heiz.vlt_ziel_c is not None:
            vlt = round(heiz.vlt_ziel_c)
            snap["wp_vlt"] = (vlt, f"{vlt} °C")

    # Die Wallbox-Sofortladung wird bewusst nicht separat geführt: sie folgt
    # 1:1 dem Nutzer-Schalter „E-Auto Zwangsladung" (ev_force) und würde je
    # Schaltvorgang eine zweite, redundante Zeile erzeugen.
    return snap


def diff_snapshots(prev: dict, snap: dict, ts: float) -> list[dict]:
    """Geänderte Felder als Log-Einträge; neu auftauchende Felder ohne „vorher"."""
    entries: list[dict] = []
    for key, (titel, cat) in _DECISION_FIELDS.items():
        if key not in snap:
            continue
        new_cmp, new_disp = snap[key]
        old = prev.get(key)
        if old is not None and old[0] == new_cmp:
            continue
        text = new_disp if old is None else f"{new_disp} (vorher {old[1]})"
        entries.append({"ts": ts, "cat": cat, "titel": titel, "text": text})
    return entries


class ChangeLog:
    """In-Memory-Ringpuffer der Entscheidungsänderungen mit Persistenz."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, _STORE_VERSION, _STORE_KEY)
        self._entries: list[dict] = []

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data and isinstance(data.get("entries"), list):
            self._entries = data["entries"]
            self._prune()

    @callback
    def add(self, entries: list[dict]) -> None:
        """Fertige Einträge anhängen, prunen und gebündelt sichern."""
        if not entries:
            return
        self._entries.extend(entries)
        self._prune()
        self._store.async_delay_save(self._data, _SAVE_DELAY)

    @callback
    def entries(self) -> list[dict]:
        """Aufsteigend gespeicherte Einträge (das Panel filtert clientseitig)."""
        return self._entries

    async def async_flush(self) -> None:
        """Ausstehende Einträge sofort schreiben (z. B. beim Entladen)."""
        await self._store.async_save(self._data())

    @callback
    def _data(self) -> dict:
        return {"entries": self._entries}

    def _prune(self) -> None:
        cutoff = dt_util.utcnow().timestamp() - _MAX_AGE_S
        kept = [e for e in self._entries if float(e.get("ts", 0)) >= cutoff]
        if len(kept) > _MAX_ENTRIES:
            kept = kept[-_MAX_ENTRIES:]
        self._entries = kept
