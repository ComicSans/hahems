"""Options-Migration (migration.py): alte Gerätelisten auf Schema 4 heben.

Die riskanteste Stelle der Integration: Hier schreibt HEMS bestehende
Nutzerkonfiguration um. Was hier verlorengeht, rekonstruiert niemand — deshalb
prüfen die Tests nicht nur, dass die Rolle wechselt, sondern auch, dass alles
andere den Wechsel unverändert übersteht.
"""
from __future__ import annotations

from hems.migration import migriere_geraete


def _wp(**kw) -> dict:
    return {
        "id": "abc123",
        "role": "switchable_load",
        "name": "Wärmepumpe",
        "switch_entity": "climate.wp",
        "power_entity": "sensor.wp_leistung",
        "min_on_min": 45,
        "min_off_min": 25,
        "max_block_min": 90,
        "priority": 2,
        **kw,
    }


# --- 3 → 4: heat_coupled wird zur Rolle Heizung -------------------------------
def test_heizungsgekoppelte_last_wird_zur_heizung():
    (geraet,) = migriere_geraete([_wp(heat_coupled=True)], 3)
    assert geraet["role"] == "heating"


def test_die_id_bleibt_erhalten():
    """An ihr hängt die gelernte Leistungsaufnahme (power_memory). Eine neue id
    würfe die Anlage auf den 2-kW-Fallback zurück."""
    (geraet,) = migriere_geraete([_wp(heat_coupled=True)], 3)
    assert geraet["id"] == "abc123"


def test_alle_uebrigen_einstellungen_ueberstehen_den_rollenwechsel():
    (geraet,) = migriere_geraete([_wp(heat_coupled=True)], 3)
    assert geraet["switch_entity"] == "climate.wp"
    assert geraet["power_entity"] == "sensor.wp_leistung"
    assert (geraet["min_on_min"], geraet["min_off_min"]) == (45, 25)
    assert (geraet["max_block_min"], geraet["priority"]) == (90, 2)


def test_das_flag_verschwindet():
    """Es bedeutet nichts mehr und stünde sonst als Altlast in den Optionen."""
    (geraet,) = migriere_geraete([_wp(heat_coupled=True)], 3)
    assert "heat_coupled" not in geraet


def test_last_ohne_flag_bleibt_schaltlast():
    (geraet,) = migriere_geraete([_wp(name="Pool", heat_coupled=False)], 3)
    assert geraet["role"] == "switchable_load"
    assert "heat_coupled" not in geraet


def test_fremde_rollen_bleiben_unberuehrt():
    speicher = {"id": "s", "role": "storage", "name": "Akku", "capacity_kwh": 10}
    (geraet,) = migriere_geraete([speicher], 3)
    assert geraet == speicher


def test_die_eingabe_wird_nicht_veraendert():
    """Der Aufrufer reicht die Options-Liste des ConfigEntry herein."""
    original = _wp(heat_coupled=True)
    migriere_geraete([original], 3)
    assert original["role"] == "switchable_load"
    assert original["heat_coupled"] is True


# --- 2 → 3: entfallene Rollen -------------------------------------------------
def test_entfallene_rollen_fliegen_raus():
    geraete = migriere_geraete(
        [
            {"id": "h", "role": "heating_circuit", "name": "Heizkreis"},
            {"id": "a", "role": "heat_pump_analysis", "name": "Analyse"},
            _wp(),
        ],
        2,
    )
    assert [g["role"] for g in geraete] == ["switchable_load"]


# --- 1 → 2: das Flag entsteht -------------------------------------------------
def test_aus_version_1_wird_jede_schaltlast_zur_heizung():
    """Bis Schema 1 galt jede schaltbare Last als Wärmepumpe. Schritt 1 → 2 hat
    das als Flag festgeschrieben, Schritt 3 → 4 macht daraus die Rolle — der
    Sprung von 1 direkt auf 4 muss beides hintereinander tun."""
    (geraet,) = migriere_geraete([_wp()], 1)
    assert geraet["role"] == "heating"


def test_ab_version_2_bleibt_eine_last_ohne_flag_eine_last():
    (geraet,) = migriere_geraete([_wp()], 2)
    assert geraet["role"] == "switchable_load"
