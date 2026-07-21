"""WebSocket-Befehle für den Config-Editor im HEMS-Panel.

Der Editor im Panel liest/schreibt die Geräteliste (`entry.options[devices]`)
direkt — ohne den Options-Flow-Wizard. Die Feld-Deskriptoren für das Formular
werden aus den **bestehenden** voluptuous-Schemas des Config-Flows abgeleitet
(Single Source of Truth: kein zweiter Feld-Katalog), die Labels aus den
vorhandenen Übersetzungsdateien. Geschrieben wird über
`async_update_entry(options=…)`, was den bestehenden Reload-Listener auslöst.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .config_flow import GENERAL_SCHEMA, ROLE_LABELS, ROLE_SCHEMAS
from .const import CONF_DEVICES, DOMAIN

_WS_REGISTERED = f"{DOMAIN}_ws_registered"
_LABELS_CACHE = f"{DOMAIN}_field_labels"


def async_register_ws(hass: HomeAssistant) -> None:
    """WS-Befehle einmalig global registrieren."""
    if hass.data.get(_WS_REGISTERED):
        return
    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_upsert)
    websocket_api.async_register_command(hass, ws_remove)
    websocket_api.async_register_command(hass, ws_set_general)
    websocket_api.async_register_command(hass, ws_logs)
    hass.data[_WS_REGISTERED] = True


# --- Schema-Introspektion ---------------------------------------------------


def _selector_descriptor(sel) -> dict:
    cfg = dict(getattr(sel, "config", {}) or {})
    name = type(sel).__name__
    if name == "EntitySelector":
        dom = cfg.get("domain")
        return {
            "type": "entity",
            "domain": [dom] if isinstance(dom, str) else (dom or []),
            "device_class": cfg.get("device_class"),
        }
    if name == "NumberSelector":
        return {
            "type": "number",
            "min": cfg.get("min"),
            "max": cfg.get("max"),
            "step": cfg.get("step"),
            "unit": cfg.get("unit_of_measurement"),
        }
    if name == "BooleanSelector":
        return {"type": "boolean"}
    if name == "TimeSelector":
        return {"type": "time"}
    if name == "SelectSelector":
        opts = cfg.get("options") or []
        norm = [o if isinstance(o, str) else o.get("value") for o in opts]
        return {
            "type": "select",
            "options": norm,
            "translation_key": cfg.get("translation_key"),
        }
    return {"type": "text"}


def _schema_to_fields(schema: vol.Schema, labels: dict, selectors: dict) -> list[dict]:
    fields = []
    for marker, sel in schema.schema.items():
        key = marker.schema
        default = None
        raw = getattr(marker, "default", vol.UNDEFINED)
        if raw is not vol.UNDEFINED:
            try:
                default = raw() if callable(raw) else raw
            except Exception:  # noqa: BLE001
                default = None
        desc = _selector_descriptor(sel)
        # Klartext-Labels für Select-Optionen aus dem selector-Block der
        # Übersetzung (z. B. Wochentag "0" → "Montag", priority_mode …).
        tk = desc.get("translation_key")
        if desc.get("type") == "select" and tk:
            opt_labels = (selectors.get(tk, {}) or {}).get("options", {})
            if opt_labels:
                desc["option_labels"] = opt_labels
        desc.update(
            {
                "key": key,
                "required": isinstance(marker, vol.Required),
                "default": default,
                "label": labels.get(key, key),
            }
        )
        fields.append(desc)
    return fields


def _read_translations(hass: HomeAssistant) -> dict:
    """Übersetzungsdatei (Sprache, dann en) laden und cachen — liefert das
    ganze Dict, aus dem Feld- und Select-Optionslabels abgeleitet werden."""
    cache = hass.data.setdefault(_LABELS_CACHE, {})
    lang = hass.config.language or "en"
    if lang in cache:
        return cache[lang]
    base = Path(__file__).parent / "translations"

    def _load(name: str) -> dict:
        p = base / f"{name}.json"
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    data = _load(lang) or _load("en")
    cache[lang] = data
    return data


def _step_labels(tr: dict, top: str, step: str) -> dict:
    return tr.get(top, {}).get("step", {}).get(step, {}).get("data", {}) or {}


def _entry(hass: HomeAssistant, entry_id: str | None) -> ConfigEntry | None:
    entries = hass.config_entries.async_entries(DOMAIN)
    if entry_id:
        return next((e for e in entries if e.entry_id == entry_id), None)
    return entries[0] if entries else None


# --- WS-Handler -------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hems/config/get",
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_get(hass, connection, msg):
    entry = _entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Keine HEMS-Instanz gefunden")
        return
    tr = await hass.async_add_executor_job(_read_translations, hass)
    selectors = tr.get("selector", {})
    general_labels = _step_labels(tr, "options", "general") or _step_labels(
        tr, "config", "user"
    )
    general_fields = _schema_to_fields(GENERAL_SCHEMA, general_labels, selectors)
    current = {**entry.data, **entry.options}
    general_values = {
        f["key"]: current.get(f["key"], f["default"]) for f in general_fields
    }
    connection.send_result(
        msg["id"],
        {
            "entry_id": entry.entry_id,
            "roles": [
                {"role": r, "label": ROLE_LABELS.get(r, r)} for r in ROLE_SCHEMAS
            ],
            "schema": {
                r: _schema_to_fields(
                    ROLE_SCHEMAS[r], _step_labels(tr, "options", f"add_{r}"), selectors
                )
                for r in ROLE_SCHEMAS
            },
            "devices": list(entry.options.get(CONF_DEVICES, [])),
            "general": {"fields": general_fields, "values": general_values},
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hems/config/upsert",
        vol.Optional("entry_id"): str,
        vol.Required("device"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_upsert(hass, connection, msg):
    entry = _entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Keine HEMS-Instanz gefunden")
        return
    device = msg["device"]
    role = device.get("role")
    if role not in ROLE_SCHEMAS:
        connection.send_error(msg["id"], "invalid_role", f"Unbekannte Rolle {role}")
        return
    fields = {k: v for k, v in device.items() if k not in ("id", "role")}
    try:
        validated = ROLE_SCHEMAS[role](fields)
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    devices = list(entry.options.get(CONF_DEVICES, []))
    dev_id = device.get("id") or uuid4().hex
    new = {"id": dev_id, "role": role, **validated}
    if any(d.get("id") == dev_id for d in devices):
        devices = [new if d.get("id") == dev_id else d for d in devices]
    else:
        devices.append(new)
    options = dict(entry.options)
    options[CONF_DEVICES] = devices
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"devices": devices, "id": dev_id})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hems/config/remove",
        vol.Optional("entry_id"): str,
        vol.Required("device_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_remove(hass, connection, msg):
    entry = _entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Keine HEMS-Instanz gefunden")
        return
    devices = [
        d
        for d in entry.options.get(CONF_DEVICES, [])
        if d.get("id") != msg["device_id"]
    ]
    options = dict(entry.options)
    options[CONF_DEVICES] = devices
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"devices": devices})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hems/config/set_general",
        vol.Optional("entry_id"): str,
        vol.Required("values"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_general(hass, connection, msg):
    entry = _entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Keine HEMS-Instanz gefunden")
        return
    try:
        validated = GENERAL_SCHEMA(msg["values"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    # Grundwerte liegen wie im Options-Flow in entry.options (überschreiben data).
    options = dict(entry.options)
    options.update(validated)
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"values": validated})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hems/logs/get",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_logs(hass, connection, msg):
    """Entscheidungs-Log der letzten Woche liefern (Panel filtert clientseitig).

    Bewusst ohne require_admin (nur Lesen, wie ws_get) und synchron: der Log
    liegt bereits in-memory beim Coordinator.
    """
    entry = _entry(hass, msg.get("entry_id"))
    coordinator = (
        hass.data.get(DOMAIN, {}).get(entry.entry_id) if entry is not None else None
    )
    changelog = getattr(coordinator, "changelog", None)
    entries = list(changelog.entries()) if changelog is not None else []
    connection.send_result(msg["id"], {"entries": entries})
