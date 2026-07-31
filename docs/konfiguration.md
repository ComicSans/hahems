# Konfiguration — alle Felder

Nachschlagewerk für den Config-Flow und den Konfigurations-Editor im
HEMS-Panel. Jedes Feld ist dort auch als Hilfetext unter dem Formularfeld
hinterlegt; reine Namens- und Label-Felder sind hier nicht erklärt.

Zurück zur [Übersicht](../README.md).

## Grundeinstellungen

| Feld | Beschreibung |
|---|---|
| **Zähler (Leistung am Netzanschluss)** | Sensor mit der momentanen Leistung am Netzanschlusspunkt in Watt. Erwartet wird: positiv = Netzbezug, negativ = Einspeisung. |
| **Vorzeichen invertieren** | Aktivieren, falls dein Zähler Einspeisung als positiven Wert meldet. |
| **PV-Leistung jetzt (W, optional)** | Sensor mit der aktuellen PV-Gesamtleistung über alle Flächen. Wird für den Lastfluss und die Empfehlung genutzt. |
| **PV-Vorzeichen invertieren** | Aktivieren, falls dein Wechselrichter die PV-Leistung mit umgekehrtem Vorzeichen meldet (negativ = Erzeugung). |
| **PV-Sensor enthält Akkuleistung** | Aktivieren, wenn PV und Akku am selben Punkt gemessen werden (Hybrid-Wechselrichter) und der PV-Wert die Akkuleistung enthält. HEMS rechnet die Akkuleistung dann aus der gemessenen PV heraus (Entladen senkt sie, Laden hebt sie). |
| **Wettervorhersage (optional)** | Wetter-Entität, deren Tagesvorhersage in die Planung einfließt: Bei trübem Folgetag lädt HEMS den Speicher voll statt nur den Nachtbedarf zu sichern. |
| **Grundlast tagsüber (W)** | Typischer Dauerverbrauch des Hauses tagsüber ohne große Verbraucher (Kühlschrank, Netzwerk, Standby). |
| **Grundlast nachts (W)** | Startwert für den Verbrauch in der Nacht. HEMS lernt den echten Wert nach einigen Tagen automatisch aus der Zählerstatistik. |
| **Prioritäten bei Überschuss** | Wohin soll der Überschuss zuerst fließen? „Automatisch“ sichert bei knappem Ertrag zuerst den Akku für die Nacht ab, bei reichlich Ertrag darf das E-Auto zuerst laden. Die Warmwasser-Basisladung hat immer Vorrang. Siehe [Ladevorrang Akku ↔ Wallbox](regelverhalten.md#ladevorrang-akku--wallbox). |
| **Kapazität frei: Bedarf (kWh)** | Der Binärsensor „Kapazität frei“ schaltet ein, wenn diese Energiemenge über die angegebene Dauer verfügbar ist, ohne Reserve und Nachtdeckung anzutasten. |
| **Kapazität frei: Dauer (h)** | Dauer, über die der Bedarf gedeckt sein muss. PV-Überschuss, der in dieses Zeitfenster fällt, zählt zur freien Kapazität. |

## PV-Prognosefläche

| Feld | Beschreibung |
|---|---|
| **Name** | Kurze Bezeichnung dieser Prognosefläche, z. B. die Dachausrichtung (nur zur Anzeige). |
| **Energie heute (kWh)** | Sensor mit der prognostizierten PV-Gesamtenergie für heute, aus deiner Prognose-Integration (z. B. Forecast.Solar, Solcast). |
| **Energie heute verbleibend (kWh)** | Sensor mit der prognostizierten PV-Restenergie für den restlichen heutigen Tag. Fließt in die Live-Überschuss- und Empfehlungsberechnung ein. |
| **Energie morgen (kWh)** | Sensor mit der prognostizierten PV-Gesamtenergie für morgen. Entscheidet, ob der Speicher heute schon voll als Puffer gegen einen schlechten Folgetag geladen wird. |

## Speicher

| Feld | Beschreibung |
|---|---|
| **Name** | Kurze Bezeichnung dieses Speichers, z. B. Einbauort oder Gerätename. |
| **SoC-Entität (%)** | Sensor mit dem aktuellen Ladestand in Prozent. |
| **Leistungs-Entität (W, optional)** | Sensor mit der aktuellen Lade-/Entladeleistung. Konvention: positiv = Entladen ins Haus, negativ = Laden. Wird für die Lastfluss-Anzeige und die Regelung im Auto-Modus genutzt. |
| **Lade-Sollwert-Entität (W, optional)** | Number-Entität, über die HEMS die aktuelle Ladeleistung setzen kann. |
| **Entlade-Sollwert-Entität (W, optional)** | Number-Entität, über die HEMS die aktuelle Entladeleistung setzen kann. |
| **Kapazität (kWh)** | Nutzbare Kapazität dieses Speichers; bestimmt die Verteilung der Lade-/Entladeleistung über mehrere Speicher und die SoC-Prognose. |
| **Reserve-SoC (%)** | Unter diesen Ladestand soll der Akku nicht entladen werden (Notreserve). |
| **Max. Ladeleistung (W)** | Begrenzung der Ladeleistung; bestimmt, wie schnell der Akku im Sonnenfenster voll wird. |
| **Max. Entladeleistung (W)** | Begrenzung der Entladeleistung; mehr kann der Akku nicht ins Haus liefern. |
| **Kaltreserve** | Dieser Speicher nimmt am Entladen erst teil, wenn der mittlere SoC der übrigen Speicher unter die Reserve-Schwelle fällt (mit Hysterese). Geladen wird er immer mit, proportional zur freien Kapazität. |
| **Richtungs-Select (optional)** | Select/Input_select, über den HEMS zwischen Lade- und Entladerichtung umschaltet (z. B. Zendures `ac_mode`). Nur nötig, wenn dein Speicher zusätzlich zum Sollwert einen Modus-Umschalter braucht. |
| **Richtungs-Option beim Laden** | Options-Wert, der den Speicher in den Lademodus versetzt. Muss exakt (inkl. Groß-/Kleinschreibung) einer verfügbaren Option des Selects entsprechen — der Config-Check meldet es sonst. |
| **Richtungs-Option beim Entladen** | Options-Wert, der den Speicher in den Entlademodus versetzt. Gleiche Regel wie oben. |
| **Ziel-SoC-Entität (geräteseitiger Ladedeckel, optional)** | Number-Entität, die begrenzt, wie weit der Speicher eigenständig lädt. Manche Speicher ignorieren ein Lade-Limit von 0 und laden trotzdem bis zu diesem Ziel weiter — dann hier setzen. |

## Warmwasser

| Feld | Beschreibung |
|---|---|
| **Temperatur-Entität (optional)** | Sensor mit der aktuellen Warmwassertemperatur. Ohne ihn empfiehlt HEMS weiterhin einen Sollwert, kann die tatsächliche Temperatur aber weder anzeigen noch prüfen. |
| **Basis-Soll (°C)** | Diese Temperatur wird immer gehalten, notfalls mit Netzstrom. |
| **Komfort-Soll (°C)** | Auf diese Temperatur wird nur bei PV-Überschuss aufgeheizt. |
| **Sperrzeit ab / bis** | Tägliches Fenster ohne Warmwasserbereitung. Liegt das Ende vor dem Anfang, läuft das Fenster über Mitternacht (z. B. 18:00 bis 06:00). Beide Felder leer heißt keine Sperre — und ohne Sperre hält der Auto-Modus das Warmwasser rund um die Uhr auf Basistemperatur. |
| **Legionellenschutz: Wochentag** | Wöchentliches Hygiene-Fenster: An diesem Tag wird der Sollwert unabhängig vom Überschuss angehoben — notfalls aus dem Netz. „Deaktiviert“ schaltet die Funktion ab. |
| **Legionellenschutz: ab / bis** | Lokale Start- und Endzeit des Fensters. Ein Ende vor dem Anfang läuft über Mitternacht. |
| **Legionellenschutz-Soll (°C)** | Solltemperatur während des Legionellen-Fensters. |
| **PV-Boost: Speicher-SoC ab / Ende (%)** | Die Komfortladung startet erst, wenn der Gesamt-Speicher-SoC das erste Niveau erreicht, und endet unter dem zweiten (Hysterese). |
| **PV-Boost: Netzsaldo ab / Ende (W)** | Netzsaldo für Start und Ende des Boosts; negativ = Einspeisung (z. B. −2800 = 2,8 kW Einspeisung). |
| **Steuer-Entität für Auto-Modus** | Entität, die HEMS im Auto-Modus ein-/ausschaltet. Ein `water_heater` trägt zusätzlich den Sollwert selbst; ein `switch`/`input_boolean` schaltet nur ein/aus — den Sollwert dann über die Sollwert-Number stellen. Ohne Steuer-Entität wird die Empfehlung nur angezeigt. |
| **Sollwert-Number (nur bei Schalter)** | Number-Entität, auf die HEMS die Soll-Temperatur schreibt, wenn die Steuer-Entität ein Schalter ist (z. B. eine Modbus-Wärmepumpe mit getrenntem Freigabe-Schalter). Bei einem `water_heater` leer lassen. |

## Heizkreis

| Feld | Beschreibung |
|---|---|
| **Außentemperatur-Entität** | Temperatursensor, der Modus-Entscheidung und Heizkurve speist. |
| **Wärmeanforderungs-Entität (%, optional)** | Sensor mit der Wärmeanforderung der Räume in Prozent (z. B. PID-Thermostat-Ausgang, mehrere Räume per Template-Sensor kombiniert). Hebt das Vorlauf-Soll um bis zu 5 K an; unter 1 % Anforderung fällt der Vorlauf auf das Minimum (Absenkbetrieb). |
| **Heizen ein unter / aus über (°C)** | Schwellen der Heiz-Empfehlung, mit Hysterese. |
| **Kühlen ein über / aus unter (°C)** | Schwellen der Kühl-Empfehlung, mit Hysterese. |
| **Frostschutz ein unter / aus über (°C)** | Frostschutz erzwingt Heizen — auch während der Sommersperre. |
| **Heizsperre ab / bis Monat** | In den Sperrmonaten (einschließlich) wird Heizen nur noch vom Frostschutz erzwungen. Ein Start nach dem Ende läuft über den Jahreswechsel. |
| **Kurve: Vorlauf-Soll bei 0 °C (°C)** | Fußpunkt der Heizkurve. |
| **Kurve: Steigung (K je K)** | Absenkung des Vorlauf-Solls je Grad Außentemperatur. |
| **Minimaler Vorlauf (°C)** | Das Vorlauf-Soll fällt beim Heizen nie unter diesen Wert. |
| **Minimaler Vorlauf bei Kälte (°C)** | Minimales Vorlauf-Soll unter 5 °C Außentemperatur. |
| **Maximaler Vorlauf (°C)** | Das Vorlauf-Soll übersteigt diesen Wert nie. |
| **Kühl-Vorlauf (°C)** | Fester Vorlauf beim Kühlen. |
| **Steuer-Entität für Auto-Modus** | Entität, auf der HEMS im Auto-Modus den Modus setzt. Ein `climate` trägt Modus **und** Vorlauf-Soll selbst; ein `select`/`input_select` (z. B. ein Modbus-Betriebsmodus-Register) trägt nur den Modus — den Vorlauf-Soll dann über die Vorlauf-Number stellen. |
| **Vorlauf-Sollwert-Number (nur bei Select)** | Number-Entität, auf die HEMS den Vorlauf-Soll schreibt, wenn die Steuer-Entität ein Modus-Select ist. Bei einem `climate` leer lassen. |
| **Modus-Optionen (nur bei Select): Heizen / Kühlen / Aus** | Klartext-Optionen des Modus-Selects, die HEMS schreibt (z. B. „Heizen“, „Kühlen“, „Aus/nur Warmwasser“). Müssen exakt echten Optionen entsprechen. Kühlen darf bei reinen Heizgeräten leer bleiben. |
| **Schalter Flüsterbetrieb (optional)** | Schalter/Input_boolean, den HEMS bei knappem Überschuss einschaltet, um die Wärmepumpe im Silent-Modus laufen zu lassen. |
| **Saison-Richtung Select (optional)** | Select/Input_select, mit dem HEMS eine Wärmepumpe zwischen Heiz- und Kühlrichtung umschaltet, falls dein Gerät einen expliziten Saison-Umschalter braucht. |
| **Störungs-/Fehler-Entität (optional)** | `binary_sensor` (an = Störung) oder `sensor`, dessen Rohwert ≠ `0`/`ok` als Fehlercode gilt (z. B. ein Modbus-Fehlerregister). HEMS überwacht ihn und meldet Betriebsstörungen — steuert aber nichts. Siehe [Diagnose](diagnose.md). |
| **Rückmeldung Warmwasserbereitung (optional)** | `binary_sensor`/`switch`/`input_boolean`, der an ist, solange die Wärmepumpe den Warmwasserspeicher lädt (z. B. ein Modbus-Statusbit). Solange er an ist, stellt HEMS am Heizkreis weder Modus noch Vorlauf-Soll: Viele Anlagen heben den Vorlauf-Soll für die Ladung selbst an und schreiben jeden Wert von HEMS wieder zurück. Leer lassen, wenn dein Gerät keine solche Rückmeldung hat. |
| **Rückmeldung Verdichter läuft (optional)** | `binary_sensor`/`switch`/`input_boolean`, der an ist, solange der Verdichter läuft. Einzige Quelle des Taktschutzes: Ohne diese Rolle zählt HEMS keine Starts und pausiert nie. |
| **Raumtemperatur für den Taupunkt (optional)** | Temperatursensor eines repräsentativen Raums. Zusammen mit der Raumfeuchte rechnet HEMS daraus den Taupunkt und hebt den Kühl-Vorlauf an, wenn er darunter läge — an einer Flächenkühlung schlägt sich sonst Wasser nieder. Wirkt nur zusammen mit der Raumfeuchte. |
| **Raumfeuchte für den Taupunkt (optional)** | Feuchtesensor desselben Raums, in % relativer Feuchte. Ohne beide Rollen bleibt die Untergrenze aus, und der Kühl-Vorlauf fährt auf den konfigurierten Sollwert. |
| **Sicherheitsabstand zum Taupunkt** | Wie weit der Kühl-Vorlauf über dem Taupunkt bleiben soll (Standard 2 K). Die Vorlauftemperatur ist nicht die Oberflächentemperatur — der Aufbau puffert. Siehe [Regelverhalten](regelverhalten.md). |
| **Taktschutz: Verdichterstarts je Fenster** | Ab wie vielen Starts im Beobachtungsfenster HEMS eine Zwangspause einlegt. `0` schaltet den Taktschutz ab. |
| **Taktschutz: Beobachtungsfenster** | Länge des Fensters, in dem die Starts gezählt werden. |
| **Taktschutz: Zwangspause** | Wie lange HEMS nach zu vielen Starts „aus“ empfiehlt. Siehe [Regelverhalten](regelverhalten.md). |

## Schaltbare Last

Geräte, die nur an oder aus können — Umwälzpumpe, Pool, Luftentfeuchter,
Heizstab.

| Feld | Beschreibung |
|---|---|
| **Schalter-/Climate-Entität** | Entität, die HEMS abhängig vom Überschuss ein- und ausschaltet. |
| **Leistungs-Entität (W, optional)** | Sensor mit der aktuellen Leistungsaufnahme. HEMS lernt daraus die erwartete Leistung. Ohne sie greift ein konservativer Fallback von 2000 W — kleine Lasten werden dann praktisch nie zugeschaltet. |
| **Mindestlaufzeit (min)** | Einmal an, bleibt das Gerät mindestens so lange eingeschaltet. |
| **Mindestpause (min)** | Nach dem Ausschalten bleibt es mindestens so lange aus. |
| **Max. Sperrdauer pro Tag (min)** | Länger als diese Dauer pro Tag wird das Gerät nie blockiert. |
| **Priorität** | 1 = höchste Priorität. Bei knappem Überschuss werden Lasten mit höherer Priorität zuerst versorgt. |
| **Heizungsgekoppelt** | Nur für Lasten, deren Verbrauch der Außentemperatur folgt (Wärmepumpe, Heizstab). Nur diese fließen in die Bedarfsprognose ein — siehe [Regelverhalten](regelverhalten.md#schaltbare-lasten). |

## Modulierbare Last

Geräte mit stellbarem Strom — typischerweise die Wallbox.

| Feld | Beschreibung |
|---|---|
| **Strom-Sollwert-Entität (A)** | Number-Entität, über die der Strom-Sollwert gesetzt wird. |
| **Schalter-Entität (optional)** | Schalter, über den HEMS das Gerät zusätzlich komplett ein- und ausschaltet (z. B. Ladefreigabe der Wallbox). |
| **Leistungs-Entität (W, optional)** | Sensor mit der aktuellen Leistungsaufnahme. Wird für die Lastfluss-Anzeige genutzt und um den tatsächlichen Bedarf zu lernen. |
| **Minimalstrom (A)** | Unterhalb dieses Stroms kann das Gerät nicht arbeiten (Wallbox-Minimum meist 6 A). |
| **Maximalstrom (A)** | Das Gerät wird nie über diesen Strom hinaus angesteuert. |
| **Phasen** | Anzahl der angeschlossenen Phasen; wird zur Umrechnung zwischen Strom und Leistung genutzt. |
| **Mindestlaufzeit / Mindestpause (min)** | Anti-Takt-Schutz, wie bei der schaltbaren Last. |
| **Priorität** | 1 = höchste Priorität. |

## Steuer-Entitäten je Rolle (Auto-Modus)

Alle optional. Ohne Steuer-Entität bleibt die Rolle reine Beobachtung, auch im
Auto-Modus.

| Rolle | Steuer-Entitäten | Aufgerufene Dienste |
|---|---|---|
| Warmwasser | `control_entity` (`water_heater` **oder** `switch`/`input_boolean`), bei Schalter zusätzlich `setpoint_entity` (Number) | on/off + `set_temperature` bzw. `number.set_value` |
| Heizkreis | `control_entity` (`climate` **oder** `select`/`input_select` + `setpoint_entity` + Modus-Optionen), `silent_switch_entity`, `season_select_entity` | `set_hvac_mode` + `set_temperature` bzw. `select_option` + `number.set_value` |
| Speicher | `charge_setpoint_entity`, `discharge_setpoint_entity`, optional `mode_entity` + Richtungs-Optionen | `number.set_value` (+ `select_option`) |
| Modulierbare Last | `current_entity`, `switch_entity` | `number.set_value` + on/off |
| Schaltbare Last | `switch_entity` | on/off |

Der Actuator schreibt nur bei Wertänderung, nie auf eine fehlende Empfehlung,
und isoliert Fehler je Gerät. Reihenfolge: Warmwasser → Heizkreis → Speicher →
modulierbare Lasten → schaltbare Lasten.

### Beispiel: Wärmepumpe über Modbus

Eine LG Therma V lässt sich ohne Cloud und ohne Gateway per Modbus RTU
anbinden; die dabei entstehenden Entitäten passen direkt auf die Rollen
Heizkreis und Warmwasser. Ein durchgerechnetes Beispiel steht in
[lg-therma-v-esphome-modbus](https://github.com/ComicSans/lg-therma-v-esphome-modbus).
