# HEMS

Home Energy Management System als Home-Assistant-Custom-Integration.
Geräte-agnostisch: Akkus, PV-Prognosen, Warmwasser, Wärmepumpe und Wallbox werden
als Rollen über die UI konfiguriert, keine Entity-IDs im Code.

**Status: Phase 1 (Beobachten).** Die Integration rechnet Prognosen und
Empfehlungen, steuert aber noch nichts. Konzept und Phasenplan: [CONCEPT.md](CONCEPT.md).

## Installation

Variante HACS: dieses Repo als Custom Repository (Typ "Integration") hinzufügen.

Variante manuell: den Ordner `custom_components/hems/` in das
`config/custom_components/`-Verzeichnis der HA-Instanz kopieren und HA neu starten.

## Einrichtung

1. Einstellungen → Geräte & Dienste → Integration hinzufügen → "HEMS"
2. Zähler-Entität (Momentanleistung am Netzanschluss, W) und Grundlast angeben
3. Danach über "Konfigurieren" die Geräte anlegen:
   PV-Prognoseflächen, Speicher, Warmwasser, schaltbare/modulierbare Lasten

## Entitäten (Phase 1)

- `sensor.hems_pv_heute` / `hems_pv_rest_heute` / `hems_pv_morgen` (kWh, alle Flächen summiert)
- `sensor.hems_pv_leistung_jetzt` (W, geschätzt)
- `sensor.hems_saldo` (W, normalisiert: positiv = Netzbezug)
- `sensor.hems_nachtdefizit` (kWh, erwarteter Verbrauch Sonnenuntergang → Sonnenaufgang)
- `sensor.hems_ueberschuss_rest_heute` (kWh, Prognose)
- `sensor.hems_speicher_soc` / `hems_speicher_verfuegbar` / `hems_speicher_ziel_soc`
- `sensor.hems_empfehlung` (Text; Details als Attribute)
- `sensor.hems_lastfluss` (W, Hausverbrauch; alle Flusswerte als Attribute)
- `select.hems_modus` (beobachten / aus)

## Lastfluss-Karte

Die Integration liefert eine eigene Lovelace-Karte mit und registriert sie
automatisch — keine Ressourcen-Konfiguration nötig. Im Dashboard einfach
hinzufügen ("HEMS Lastfluss" im Karten-Picker) oder per YAML:

```yaml
type: custom:hems-flow-card
entity: sensor.hems_lastfluss   # optional, das ist der Default
title: Lastfluss                # optional
```

Die Karte zeigt animierte Flüsse zwischen PV, Netz, Batterie und Haus;
Wärmepumpe und Wallbox erscheinen als Chips, sobald für sie eine
Power-Entität konfiguriert ist. Die Aufteilung auf die Kanten folgt der
Merit-Order des Planners (PV → Haus → Akku → Einspeisung).

Konventionen: `netz_w` positiv = Netzbezug, `batterie_w` positiv = Entladen.
Liefert der Speicher ein umgekehrtes Vorzeichen, hilft ein Template-Sensor.
Die PV-Leistung stammt aus der Prognose und ist als "geschätzt" markiert.
