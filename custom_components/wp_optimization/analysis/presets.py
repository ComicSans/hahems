"""Presets laden und die Erwartung aus der Kennlinie berechnen.

Ein Preset ist Daten, kein Code — ein weiteres Geraet ist eine weitere Datei
und kein Eingriff in die Logik. Das Format ist JSON und nicht YAML, damit
dieses Modul mit der Standardbibliothek auskommt und ohne Home Assistant
testbar bleibt.
"""
from __future__ import annotations

import json
from pathlib import Path

from .types import Preset

# Der Fit stammt aus Pruefstandspunkten; ausserhalb des gefitteten Bereichs
# extrapoliert das Polynom teils ins Absurde. Beides begrenzen.
COP_UNTERGRENZE = 1.0
COP_OBERGRENZE = 8.0

_PFLICHTFELDER = ("schluessel", "anzeigename", "quelle", "cop_polynom")


def erwarteter_cop(
    preset: Preset, t_aussen_c: float | None, t_vorlauf_c: float | None
) -> float | None:
    """COP, den das Datenblatt bei diesen Bedingungen verspricht.

    Fuer die Vorlauftemperatur wird der **gemessene** Wert eingesetzt, nicht
    die sonst uebliche Rekonstruktion aus Ruecklauf plus 5 K — genau diese
    Annahme soll die Spreizungsdiagnose ja pruefen.

    Das Ergebnis wird auf einen plausiblen Bereich begrenzt. Eine Waermepumpe
    mit COP 0,4 oder 14 gibt es nicht; solche Werte waeren reine
    Extrapolationsartefakte und wuerden als Vergleichsmassstab in die Irre
    fuehren.
    """
    if t_aussen_c is None or t_vorlauf_c is None:
        return None
    roh = (
        preset.p1 * t_aussen_c
        + preset.p2 * t_vorlauf_c
        + preset.p3
        + preset.p4 * t_aussen_c
    )
    return max(COP_UNTERGRENZE, min(COP_OBERGRENZE, roh))


def ausserhalb_kennfeld(preset: Preset, t_aussen_c: float | None) -> bool:
    """Liegt die Aussentemperatur ausserhalb des gefitteten Bereichs?

    Kein Fehler, aber ein Grund zur Abwertung der Datenbasis: die Erwartung
    ist dort eine Hochrechnung und kein Messwert.
    """
    if t_aussen_c is None:
        return True
    return not (preset.gueltig_ab_c <= t_aussen_c <= preset.gueltig_bis_c)


def aus_dict(roh: dict) -> Preset:
    """Ein Preset aus geladenem JSON bauen."""
    fehlend = [f for f in _PFLICHTFELDER if f not in roh]
    if fehlend:
        raise ValueError(f"Preset unvollstaendig, es fehlt: {', '.join(fehlend)}")
    poly = roh["cop_polynom"]
    fehlend_poly = [k for k in ("p1", "p2", "p3", "p4") if k not in poly]
    if fehlend_poly:
        raise ValueError(
            f"COP-Polynom unvollstaendig, es fehlt: {', '.join(fehlend_poly)}"
        )
    return Preset(
        schluessel=roh["schluessel"],
        anzeigename=roh["anzeigename"],
        quelle=roh["quelle"],
        p1=float(poly["p1"]),
        p2=float(poly["p2"]),
        p3=float(poly["p3"]),
        p4=float(poly["p4"]),
        modellfehler_prozent=float(roh.get("cop_modellfehler_prozent", 0.0)),
        generisch=bool(roh.get("generisch", False)),
        spreizung_min_gueltig_k=float(roh.get("spreizung_min_gueltig_k", 2.0)),
        waermetraeger_faktor=float(roh.get("waermetraeger_faktor", 1.163)),
        gueltig_ab_c=float(roh.get("gueltig_ab_c", -20.0)),
        gueltig_bis_c=float(roh.get("gueltig_bis_c", 20.0)),
    )


def lade_presets(verzeichnis: str | Path) -> dict[str, Preset]:
    """Alle Presets eines Verzeichnisses laden, Schluessel -> Preset.

    Eine defekte Datei laesst die uebrigen stehen: ein Tippfehler in einem
    Geraeteprofil darf nicht die ganze Integration lahmlegen.
    """
    pfad = Path(verzeichnis)
    gefunden: dict[str, Preset] = {}
    for datei in sorted(pfad.glob("*.json")):
        try:
            preset = aus_dict(json.loads(datei.read_text(encoding="utf-8")))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        gefunden[preset.schluessel] = preset
    return gefunden
