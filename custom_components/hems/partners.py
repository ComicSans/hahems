"""Partner-Integrationen erkennen, ohne dass etwas verdrahtet werden muss.

Derzeit genau eine: `wp-optimization`, die Effizienzanalyse der Wärmepumpe.
Sie ist ein eigenständiges Repository und beratend — sie schreibt nie an die
Anlage. HEMS liest von ihr, zeigt ihre Werte im Panel an und bleibt ohne sie
voll funktionsfähig.

Die Erkennung läuft über die **Kennung** aus der Entity-Registry und nicht
über `entity_id`: Nutzende benennen Entities um, und eine Erkennung über
`entity_id` bräche beim ersten Mal. Die Kennung lautet nach dem Kontrakt
`<eintrag-id>_<rolle>` und ist stabil.

Kontrakt: https://github.com/ComicSans/wp-optimization/blob/main/docs/kontrakt-v1.md
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

PARTNER_DOMAIN = "wp_optimization"

# Version des Kontrakts, gegen die HEMS geschrieben ist. Eine höhere Version
# auf der Gegenseite ist kein Fehler — nur ein Hinweis, dass dort etwas
# hinzugekommen sein kann, das hier nicht angezeigt wird.
KONTRAKT_VERSION = 1

# Rollen, die das Panel anzeigt. Die Reihenfolge ist die Anzeigereihenfolge.
# Fehlt eine, wird sie übersprungen statt einen Platzhalter zu erfinden.
ROLLEN: tuple[str, ...] = (
    "cop_momentan",
    "cop_soll",
    "cop_abweichung",
    "cop_soll_unsicherheit",
    "waermeleistung",
    "waermemenge",
    "spreizung",
    "waermeverlust_koeffizient",
    "takte",
    "laufzeit_summe",
    "laufzeit_mittel",
    "empfehlung_fusspunkt",
    "empfehlung_steilheit",
    "empfehlung_vorlauf_min",
    "datenbasis",
    "datenbasis_empfehlung",
    "verwerfungsgrund",
    "kontrakt_version",
)

# Hinweise als eigene Binärsensoren — je Art eine Entity mit stabiler Kennung,
# nicht eine Liste in einem Attribut.
HINWEISE: tuple[str, ...] = (
    "hinweis_temperaturen_identisch",
    "hinweis_spreizung_niedrig",
    "hinweis_spreizung_hoch",
    "hinweis_taktung_hoch",
    "hinweis_vorlauf_zu_hoch",
    "hinweis_effizienz_unter_erwartung",
    "durchfluss_geschaetzt",
)


@callback
def entdecke(hass: HomeAssistant) -> list[dict]:
    """Alle Einrichtungen von `wp-optimization` mit ihren Rollen finden.

    Gibt eine leere Liste zurück, wenn die Integration nicht installiert ist —
    das ist der Normalfall und kein Fehler. Das Panel blendet den Reiter dann
    aus, statt einen leeren anzuzeigen.
    """
    eintraege = hass.config_entries.async_entries(PARTNER_DOMAIN)
    if not eintraege:
        return []

    registry = er.async_get(hass)
    gefunden: list[dict] = []
    for eintrag in eintraege:
        rollen: dict[str, str] = {}
        praefix = f"{eintrag.entry_id}_"
        for entity in er.async_entries_for_config_entry(registry, eintrag.entry_id):
            if not entity.unique_id.startswith(praefix):
                # Nicht nach dem Kontrakt benannt — überspringen statt raten.
                continue
            rolle = entity.unique_id[len(praefix) :]
            if rolle in ROLLEN or rolle in HINWEISE:
                rollen[rolle] = entity.entity_id
        if rollen:
            gefunden.append(
                {
                    "entry_id": eintrag.entry_id,
                    "titel": eintrag.title,
                    "rollen": rollen,
                }
            )
    return gefunden


@callback
def ist_verfuegbar(hass: HomeAssistant) -> bool:
    """Kurzform für das Panel: gibt es überhaupt etwas anzuzeigen?"""
    return bool(entdecke(hass))
