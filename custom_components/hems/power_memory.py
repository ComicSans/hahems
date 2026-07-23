"""Gelernte Leistungsaufnahme schaltbarer Lasten, über Neustarts hinweg.

Die erwartete Leistung einer schaltbaren Last (`erwartet_w`) wird aus ihrem
Leistungssensor gelernt: der letzte nennenswerte Messwert im An-Zustand. Ohne
Persistenz fiele dieser Wert bei jedem Neustart auf den konservativen Fallback
`DEFAULT_SWITCHABLE_EXPECTED_W` (2000 W) zurück — eine kleine Last (z. B. ein
300-W-Luftentfeuchter) bräuchte dann erst wieder 2,2 kW Überschuss, um
überhaupt eingeschaltet zu werden, und würde ohne dieses Einschalten nie neu
gelernt. Deshalb liegt der Wert je Last-ID in einem `Store`.

Persistiert wird verzögert (`async_delay_save`), damit der Minutentakt des
Coordinators nicht zum Schreibtakt wird; beim Entladen der Integration wird
gebündelt geschrieben.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

_STORE_VERSION = 1
_STORE_KEY = "hems_power_memory"
# Verzögertes Sichern: eine Last ändert ihre Leistungsaufnahme selten, ein
# verlorener Wert kostet höchstens ein erneutes Lernen.
_SAVE_DELAY = 300


class PowerMemory:
    """Gelernte An-Leistung je Last-ID mit Persistenz."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, _STORE_VERSION, _STORE_KEY)
        self._watt: dict[str, float] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data or not isinstance(data.get("watt"), dict):
            return
        self._watt = {
            str(key): float(value)
            for key, value in data["watt"].items()
            if isinstance(value, (int, float))
        }

    @callback
    def get(self, load_id: str) -> float | None:
        return self._watt.get(load_id)

    @callback
    def learn(self, load_id: str, watt: float) -> None:
        """Neuen Messwert übernehmen und (verzögert) sichern."""
        if self._watt.get(load_id) == watt:
            return
        self._watt[load_id] = watt
        self._store.async_delay_save(self._data, _SAVE_DELAY)

    async def async_flush(self) -> None:
        """Ausstehende Werte sofort schreiben (z. B. beim Entladen)."""
        await self._store.async_save(self._data())

    @callback
    def _data(self) -> dict:
        return {"watt": self._watt}
