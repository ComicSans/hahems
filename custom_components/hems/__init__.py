"""HEMS - Home Energy Management System."""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .changelog import ChangeLog
from .config_ws import async_register_ws
from .const import CONF_DEVICES, DOMAIN, ROLE_SWITCHABLE
from .coordinator import HemsCoordinator
from .power_memory import PowerMemory

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
]

FRONTEND_URL = "/hems-frontend"
FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"

# Alle vom Panel/den Karten geladenen JS-Assets. Der Cache-Buster (?v=…) wird
# aus dem Datei-Inhalt abgeleitet, NICHT aus der manifest-Version: sonst
# serviert der Browser (Cache-Control: max-age=31d) nach jeder JS-Änderung, die
# die manifest-Version nicht anfasst, weiter die alte Datei — das Panel bleibt
# dann mit veraltetem/inkonsistentem JS leer, bis manuell hart neugeladen wird.
_FRONTEND_ASSETS = ("hems-panel.js", "hems-flow-card.js", "hems-plan-card.js")


def _asset_versions(frontend_dir: Path) -> dict[str, str]:
    """Content-Hash je Asset (blocking IO, im Executor aufrufen)."""
    versions: dict[str, str] = {}
    for name in _FRONTEND_ASSETS:
        try:
            data = (frontend_dir / name).read_bytes()
            versions[name] = hashlib.sha1(data).hexdigest()[:12]
        except OSError:
            versions[name] = "0"
    return versions


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

    frontend_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                FRONTEND_URL,
                str(frontend_dir),
                cache_headers=True,
            )
        ]
    )
    # Cache-Buster aus dem Datei-Inhalt (nicht der manifest-Version) ableiten,
    # damit jede JS-Änderung die URL ändert und der Browser sie neu holt.
    versions = await hass.async_add_executor_job(_asset_versions, frontend_dir)
    for card in ("hems-flow-card.js", "hems-plan-card.js"):
        add_extra_js_url(hass, f"{FRONTEND_URL}/{card}?v={versions[card]}")

    # Eigenes HEMS-Panel in der Seitenleiste (Übersicht, Steuerung, Diagnose,
    # Konfiguration, Logs).
    # Der Static-Handler liefert hems-panel.js aus derselben frontend/-Ablage.
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path="hems",
        webcomponent_name="hems-panel",
        module_url=f"{FRONTEND_URL}/hems-panel.js?v={versions['hems-panel.js']}",
        sidebar_title="HEMS",
        sidebar_icon="mdi:home-lightning-bolt",
        require_admin=False,
        embed_iframe=False,
    )

    # Erst nach erfolgreicher Registrierung markieren, damit ein Fehler oben
    # beim nächsten Setup-Versuch erneut registriert (statt still zu blockieren).
    hass.data[FRONTEND_REGISTERED] = True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Optionen auf das aktuelle Schema heben.

    1 → 2: Schaltbare Lasten kennen `heat_coupled`. Bis dahin galten *alle*
    schaltbaren Lasten als Wärmepumpe (ihre Leistung floss gesammelt ins
    Heizgradstunden-Modell). Bestehende Einträge bekommen das Flag deshalb auf
    True — sonst verlöre eine laufende Installation ihr WP-Modell stillschweigend
    und würde das Nachtdefizit unterschätzen. Neue Lasten starten auf False.
    """
    if entry.version >= 2:
        return True

    devices = [dict(d) for d in entry.options.get(CONF_DEVICES, [])]
    for device in devices:
        if device.get("role") == ROLE_SWITCHABLE and "heat_coupled" not in device:
            device["heat_coupled"] = True
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_DEVICES: devices}, version=2
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _async_register_frontend(hass)
    async_register_ws(hass)

    coordinator = HemsCoordinator(hass, entry)
    # Persistenten Entscheidungs-Log laden und anhängen, bevor der erste
    # Planlauf die Baseline setzt.
    changelog = ChangeLog(hass)
    await changelog.async_load()
    coordinator.changelog = changelog
    # Gelernte Schaltlast-Leistungen laden, bevor der erste Plan rechnet —
    # sonst startet jede Last wieder beim 2-kW-Fallback.
    power_memory = PowerMemory(hass)
    await power_memory.async_load()
    coordinator.power_memory = power_memory
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
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        # Ausstehende Log-Einträge und gelernte Leistungen vor dem
        # Reload/Entladen sichern.
        if coordinator.changelog is not None:
            await coordinator.changelog.async_flush()
        if coordinator.power_memory is not None:
            await coordinator.power_memory.async_flush()
    return unloaded
