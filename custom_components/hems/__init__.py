"""HEMS - Home Energy Management System."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import HemsCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SELECT]

FRONTEND_URL = "/hems-frontend"
FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Die HEMS-Karten als Lovelace-Ressourcen ausliefern (einmalig)."""
    if hass.data.get(FRONTEND_REGISTERED):
        return

    # .js zwingend als text/javascript ausliefern. Auf Systemen, deren
    # mimetypes-Datenbank .js nicht (korrekt) kennt, liefert der Static-
    # Handler sonst text/plain o. Ä.; der Browser blockt dann das ES-Modul
    # ("Strict MIME type checking"), sodass customElements.define nie läuft
    # und die Karten als "Custom element doesn't exist" scheitern.
    mimetypes.add_type("text/javascript", ".js")

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                FRONTEND_URL,
                str(Path(__file__).parent / "frontend"),
                cache_headers=True,
            )
        ]
    )
    integration = await async_get_integration(hass, DOMAIN)
    for card in ("hems-flow-card.js", "hems-plan-card.js"):
        add_extra_js_url(hass, f"{FRONTEND_URL}/{card}?v={integration.version}")

    # Erst nach erfolgreicher Registrierung markieren, damit ein Fehler oben
    # beim nächsten Setup-Versuch erneut registriert (statt still zu blockieren).
    hass.data[FRONTEND_REGISTERED] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _async_register_frontend(hass)

    coordinator = HemsCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    # Quellen sind nach einem Neustart evtl. noch nicht bereit: sofort neu
    # rechnen, sobald sie verfügbar werden, statt bis zum nächsten Poll zu warten.
    coordinator.async_setup_source_tracking()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Optionen geändert (z.B. Gerät hinzugefügt) → Integration neu laden."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
