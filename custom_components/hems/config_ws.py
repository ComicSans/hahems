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

from .config_flow import ROLE_LABELS, ROLE_SCHEMAS
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
        return {"type": "select", "options": norm}
    return {"type": "text"}


def _schema_to_fields(schema: vol.Schema, labels: dict) -> list[dict]:
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


def _read_labels(hass: HomeAssistant) -> dict:
    """Feld-Labels je Rolle aus den Übersetzungsdateien (Sprache, dann en)."""
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
    steps = data.get("options", {}).get("step", {})
    per_role = {
        role: steps.get(f"add_{role}", {}).get("data", {}) for role in ROLE_SCHEMAS
    }
    cache[lang] = per_role
    return per_role


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
    labels = await hass.async_add_executor_job(_read_labels, hass)
    connection.send_result(
        msg["id"],
        {
            "entry_id": entry.entry_id,
            "roles": [
                {"role": r, "label": ROLE_LABELS.get(r, r)} for r in ROLE_SCHEMAS
            ],
            "schema": {
                r: _schema_to_fields(ROLE_SCHEMAS[r], labels.get(r, {}))
                for r in ROLE_SCHEMAS
            },
            "devices": list(entry.options.get(CONF_DEVICES, [])),
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
