# HEMS

<img src="assets/icon.png" alt="HEMS Icon" width="128" align="right">

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
- `sensor.hems_empfehlung` (Text; Details als Attribute, u.a. das gelernte
  24-h-Lastprofil je Wochentagstyp und dessen Quelle `lastprofil_quelle`)
- `sensor.hems_lastfluss` (W, Hausverbrauch; alle Flusswerte als Attribute)
- `sensor.hems_einspeiseplan` (W, geplante Einspeisung jetzt; Stunden-Slots,
  SoC-Prognose, PV-Stundenkurve und Warmwasser-Sperrzeiten als Attribute)
- `select.hems_modus` (beobachten / aus)

## Lastfluss-Karte

Die Integration liefert eine eigene Lovelace-Karte mit und registriert sie
automatisch — keine Ressourcen-Konfiguration nötig. Im Dashboard einfach
hinzufügen ("HEMS Lastfluss" im Karten-Picker) oder per YAML:

```yaml
type: custom:hems-flow-card
entity: sensor.hems_lastfluss   # optional, das ist der Default
title: Lastfluss                # optional
height: 440                     # optional, px; "auto" = inhaltsabhängig
```

Die Karte zeigt animierte Flüsse zwischen PV, Netz, Batterie und Haus;
Wärmepumpe und Wallbox erscheinen als Chips, sobald für sie eine
Power-Entität konfiguriert ist. Die Aufteilung auf die Kanten folgt der
Merit-Order des Planners (PV → Haus → Akku → Einspeisung).

Konventionen: `netz_w` positiv = Netzbezug, `batterie_w` positiv = Entladen.
Liefert der Speicher ein umgekehrtes Vorzeichen, hilft ein Template-Sensor.
Die PV-Leistung stammt aus der Prognose und ist als "geschätzt" markiert.

## Einspeiseplan-Karte

Zweite mitgelieferte Karte ("HEMS Einspeiseplan" im Karten-Picker):

```yaml
type: custom:hems-plan-card
entity: sensor.hems_einspeiseplan   # optional, das ist der Default
title: Einspeiseplan                # optional
height: 440                         # optional, px; "auto" = inhaltsabhängig
```

Beide Karten haben dieselbe Standardhöhe (440 px) und sind damit in jedem
Dashboard-Layout gleich hoch — auch im Masonry-Layout, wo sich Karten sonst
nach ihrem eigenen Seitenverhältnis richten. Wer das nicht will, setzt
`height: auto` (oder einen eigenen Wert) in beiden Karten.

Der Zeitstrahl umfasst den kompletten heutigen und den kompletten morgigen
Kalendertag: orange die geschätzte PV-Stundenkurve (Tagesenergie sinusförmig
über das Sonnenfenster verteilt), grün die geplante nächtliche Einspeisung.
Eine rote Linie markiert "jetzt".

Der bereits vergangene Teil des heutigen Tages bleibt leer und ist grau
hinterlegt ("keine Verlaufsdaten"): Alle Kurven sind Prognosen ab jetzt, und
für die zurückliegenden Stunden liest die Integration keine Messwerte aus der
Historie. Statt dort eine rückwirkend geschätzte Kurve zu zeichnen, zeigt die
Karte offen, dass sie nichts weiß.

Gestrichelt darüber liegt die SoC-Prognose: ein stündlicher Vorwärtslauf ab
jetzt, bei dem Überschuss lädt (begrenzt durch Ladeleistung und Kapazität) und
Defizit bis zur Reserve entlädt. Sie beginnt bewusst erst bei "jetzt" — für die
Vergangenheit ist nur der aktuelle Stand bekannt, alles davor wäre erfunden.

Das Band am unteren Rand zeigt die Warmwasser-Verfügbarkeit: türkis heißt
freigegeben, grau eine konfigurierte Sperrzeit (siehe unten).

Die Nachtlast je Stunde stammt aus einem gelernten Lastprofil (14 Tage
Zähler-Statistik); reicht das Akku-Budget nicht für die ganze Nacht, werden
alle Stunden proportional reduziert, damit der Speicher bis Sonnenaufgang
durchhält.

## Warmwasser-Sperrzeiten

Beim Warmwasser-Gerät lassen sich "Sperrzeit ab" und "Sperrzeit bis" angeben
(Konfigurieren → Warmwasser). In diesem Fenster empfiehlt der Planner weder
Basis- noch Komfortladung — der Speicher darf also bis zum Ende der Sperre
unter die Basistemperatur auskühlen, statt aus dem Netz nachzuheizen.

Liegt das Ende vor dem Anfang, läuft das Fenster über Mitternacht: `18:00` bis
`06:00` sperrt jede Nacht von abends bis morgens. Beide Felder leer (oder zwei
gleiche Zeiten) bedeutet keine Sperre.
