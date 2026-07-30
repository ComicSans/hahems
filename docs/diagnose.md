# Diagnose und Meldungen

Zurück zur [Übersicht](../README.md).

## Config-Sanity-Check

`binary_sensor.hems_konfiguration` (device_class `problem`) prüft jeden Zyklus,
ob die Konfiguration für den Auto-Modus taugt — die Antwort auf „Kann ich
scharfschalten?“.

**An = Problem.** Harte Fehler melden immer, eine Überlappung nur im
Auto-Modus: in den Modi `beobachten` und `aus` sind fremde Automationen ja
erwünscht.

Die Details stehen in den Attributen:

| Attribut | Bedeutung |
|---|---|
| `bereit_fuer_auto` | Keine harten Fehler. |
| `auto_schaltet` | Welche Rollen der Auto-Modus tatsächlich stellt (die mit konfigurierter Steuer-Entität). Der Rest bleibt reine Beobachtung. |
| `fehler` | Der Auto-Modus würde scheitern: Steuer-Entität existiert nicht, falsche Domain, Richtungs-Select ohne Optionswerte, oder eine Options-Angabe passt nicht exakt zu einer echten Option (Groß-/Kleinschreibung zählt). |
| `warnungen` | Funktioniert, aber Vorsicht: nur ein Speicher-Setpoint gesetzt, Warmwasser ohne Sperrfenster (24/7 an), Last ohne Leistungsmessung. |
| `ueberlappung` | Aktive Automationen, die auf dieselbe Steuer-Entität schreiben wie HEMS. Vor dem Auto-Modus die jeweilige Automation deaktivieren. |
| `ueberlappungspruefung` | `ok` oder `nicht verfügbar`, falls Home Assistant die Automations-Referenzen nicht hergibt. |

Die Überlappungsprüfung liest die `referenced_entities` der Automationen. Sie
ist heuristisch: Templates und indirekte Referenzen entgehen ihr.

Fehler und Warnungen werden zusätzlich bei Änderung ins Log geschrieben.

## Störungs- und Warnmeldungen

HEMS stellt aktive Störungen nach Schweregrad zu, damit nichts doppelt lärmt:

| Meldung | Push-Sensor | Notification | Reparatur |
|---|:---:|:---:|:---:|
| **Betriebsstörung der Wärmepumpe** | ✓ | ✓ | ✓ |
| **Störungsquelle nicht erreichbar** (Entität `unavailable`) | – | – | ✓ (Warnung) |
| **Konfigurationsfehler** (Auto-Modus würde scheitern) | – | – | ✓ |
| **Konfigurationswarnung** | – | – | – (nur Config-Sensor und Log) |

- **Reparatur** — ein Eintrag unter *Einstellungen → Reparaturen*. Erscheint und
  verschwindet automatisch mit dem Zustand, restart-fest.
- **Notification** — eine persistente Meldung in der HA-Glocke.
- **Push-Sensor** — `binary_sensor.hems_warmepumpen_storung` (device_class
  `problem`), **an**, sobald eine Wärmepumpe eine entprellte Störung meldet.
  Attribute: `anzahl`, `stoerungen` (je `anlage`, `code`, `meldung`) und
  `meldung` als Ein-Zeilen-Zusammenfassung.

**Entprellung:** Eine Störung greift erst nach drei aufeinanderfolgenden
Störsignalen und verschwindet erst nach fünf störungsfreien Zyklen — ein
einzelner Modbus- oder ESPHome-Aussetzer löst also keinen Fehlalarm aus. Fällt
die Störungs-Entität selbst auf `unavailable`, gilt das als eigener, sanfter
Fall (nur Reparatur, kein Push).

## Push aufs Handy einrichten

HEMS pusht nicht selbst. Dafür braucht es eine Automation, die auf den
Push-Sensor triggert und die
[Mobile-App-Notification](https://www.home-assistant.io/integrations/mobile_app/)
deines Geräts aufruft:

```yaml
automation:
  - alias: HEMS Wärmepumpen-Störung aufs Handy
    trigger:
      - trigger: state
        entity_id: binary_sensor.hems_warmepumpen_storung
        from: "off"
        to: "on"
    action:
      - action: notify.mobile_app_dein_geraet   # an dein Gerät anpassen
        data:
          title: "Wärmepumpe: Störung"
          message: "{{ state_attr('binary_sensor.hems_warmepumpen_storung', 'meldung') }}"
```

> Der Entity-Slug kann je nach Instanz abweichen — die tatsächliche Entity-ID
> unter *Entwicklerwerkzeuge → Zustände* nachsehen (nach „Wärmepumpen-Störung“
> filtern).
