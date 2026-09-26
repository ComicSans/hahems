# Der Speicher stand zwanzig Minuten still, und die Ursache ist nicht bewiesen

In der Nacht vom 09. auf den 10.09.2026, der ersten Nacht mit
Speicher-Zwangsladung (Release 2.9.0), standen beide Zendure Hyper 2000 zwanzig
Minuten lang still, obwohl HEMS sie auf „laden" kommandiert hatte. Der Nachtritt
(Git-Commit dieser Aufgabe, `SPEICHER_NACHTRETEN_FRIST` in `actuator.py`) ist
die Gegenmaßnahme — er deckt alle verbliebenen Kandidaten ab, ohne die Ursache
zu kennen. Diese Aufgabe hält fest, was noch offen ist, damit der nächste
natürliche Stillstand nicht wieder von vorn untersucht wird.

## Der Befund

- 23:04:40 — `sensor.hems_speicher_regelung` wechselt von `entladen` auf
  `laden`, `zwangsladung: true`, Zuteilung 1200 W je Gerät, Ziel-SoC 100 %,
  Ist-SoC 22 % (L2) bzw. 21 % (L3).
- 23:04:40 — HEMS schreibt beiden Geräten `ac_mode = input`.
- 23:04:43/44 — HEMS schreibt beiden Geräten `input_limit = 1200`.
- 23:04:44 bis 23:24:53 — `grid_input_power` und `output_pack_power` bleiben auf
  0 W. Das Haus zieht in dieser Zeit rund 1,3 kW aus dem Netz (E-Auto mit
  Zwangsladung, 6 A).
- 23:12:45 — der Watchdog meldet pflichtgemäß „Speicher L3 lädt nicht".
- 23:21:18 — Eingriff von Hand am Zendure-Manager: `operation` auf `manual`.
- 23:22:21 — `manual_power` auf 1400 (positiv, also Entladen).
- 23:23:18 — HEMS korrigiert `ac_mode` von L2 auf `input`, ausgelöst durch eine
  Meldung des Geräts zwei Sekunden zuvor.
- 23:23:52 — Eingriff von Hand: `number.hyper_2000_l2_input_limit` auf 300 W.
- 23:24:44/48 — Zendure-Manager zurück auf `off`, `manual_power` auf 0.
- 23:24:53 — L2 lädt, 244 W. Kurz darauf läuft auch L3 an; HEMS regelt beide
  auf rund 660 W hoch, SoC steigt.

Während des eigentlichen Stillstands (23:04 bis 23:21) stand der
Zendure-Manager durchgehend auf `off`. Er bleibt tabu: HEMS steuert
ausschließlich über die Geräte-Entitäten.

## Was widerlegt ist

Alle drei mit Daten aus derselben Nacht, damit sie nicht wiederkommen:

- **„Die Integration/Verbindung ist tot."** Nein: `hyper_tmp` kam während des
  Stillstands alle 20 bis 35 Sekunden frisch und fiel von 51 auf 45 °C. Der
  Reload der Zendure-Integration um 23:08 hat den Stillstand nicht beendet.
- **„Das Gerät braucht einen niedrigen Anlaufwert (Kickstart)."** Nein: Aus 74
  Ladestarts vom 07. bis 09.09. starteten mehrere aus dem Stillstand bei hohem
  Limit — 08.09. 11:04 Uhr bei Limit 1200 mit 1041 W, 08.09. 07:10 Uhr bei
  Limit 745 mit 745 W. Eine Anlauf-Konstante wäre eine Zahl gegen einen Befund,
  den es nicht gibt.
- **„Das Gerät lädt nur Überschuss, nie aus dem Netz."** Nein: Nach dem Lösen
  zog es 1,32 kW bei Netzbezug. Und im Quelltext der Zendure-Integration ist
  der Hyper 2000 ein `ZendureLegacy` in `auto_model = 0`; dort sind `acMode` +
  `inputLimit` der Befehlspfad, nicht bloß ein Deckel. (Für die neueren Geräte
  schreibt `ZendureDevice.charge()` `smartMode/acMode/inputLimit` direkt — die
  `deviceAutomation`-Nachricht mit `autoModel: 8` ruft nur der Manager.)

## Was übrig bleibt

Drei Kandidaten, alle belegt, keiner als Auslöser bewiesen. Der dritte ist der
stärkste und zugleich der unangenehmste, weil HEMS ihn nicht bedienen kann.

1. **Das Gerät verwirft den geschriebenen Wert von selbst.** `input_limit` fiel
   nach jedem Schreiben binnen ein bis sieben Minuten auf einen eigenen Wert
   zurück: 1200 → 702 (23:07:01), → 832 (23:14:53), → 0 (23:15:48), → 715
   (23:22:00). Dieselbe Sorte Rückzug wie beim Ziel-SoC am 14.08.2026.
2. **Der Zustand des Richtungs-Selects ist nach einem Reload wertlos.**
   `ZendureSelect(self, "acMode", {1: "input", 2: "output"}, …, 1)` legt die
   Entität ohne Restore mit „input" an. L2 meldete um 23:08:57 und um 23:23:16
   „output" — HEMS korrigierte beide Male binnen zwei Sekunden, weil erst die
   Meldung den Vergleich `self._state(mode_entity) != want` wahr machte. L3
   meldete nach dem Reload gar kein acMode mehr und stand für HEMS unverrückbar
   auf „input". Was L3 wirklich tat, ist von außen nicht feststellbar.

3. **Ein altes Automatik-Programm im Gerät überstimmt acMode und inputLimit.**
   `ZendureManager.update_operation` ruft beim Wechsel auf `off` für jedes
   Gerät `power_off()`, und für den Hyper 2000 heißt das eine
   `deviceAutomation`-Nachricht mit `autoModel: 0`, `autoModelProgram: 0`,
   `chargingPower: 0`, `outPower: 0` — sie löscht das laufende Programm. Genau
   dieser Aufruf lag um 23:24:44, neun Sekunden vor der ersten Leistung. Ein
   Gerät, das noch in einem Programm steht, ignoriert die Property-Schreibwege,
   auf denen HEMS arbeitet; `charging_mode` und `charging_type` standen die
   ganze Zeit auf 2, während `auto_model` 0 meldete.

   Wenn das die Ursache ist, kann HEMS sie nicht selbst beheben: Der einzige
   Weg zu `power_off()` führt über den Manager, und der ist tabu. Dann wäre der
   ehrliche Stand: HEMS kann den Befehl beliebig oft wiederholen, aber ein
   Gerät in fremdem Programm nicht zurückholen — und die Zwangsladung braucht
   entweder eine Regel „Manager nach jedem Reload der Integration einmal auf
   off stellen" von Hand oder einen anderen, property-basierten Weg, das
   Programm zu löschen.

Auffällig, aber nicht eingeordnet: Die Korrektur des `ac_mode` von L2 um
23:23:18 liegt 95 Sekunden vor der ersten Leistung, der 300-W-Schreibvorgang 61
Sekunden davor, `power_off()` 9 Sekunden davor. Die Reihenfolge spricht für
Kandidat 3, beweist ihn aber nicht.

## Zweiter Befund: 16.09.2026, 20:40 bis 21:05

Derselbe Stillstand, diesmal mit gezielten Einzeltests. Ausgangslage wie am
09.09.: beide Hyper 2000 entladen mit rund 700 W, SoC 22 %, dann Zwangsladung
an. Zeiten in Ortszeit.

- 20:40:34 — beide Geräte hören von selbst auf zu entladen, sieben Sekunden
  vor dem Schalter und ohne Schreibvorgang von HEMS. Nicht eingeordnet.
- 20:40:50/52 — HEMS schreibt `ac_mode = input`, `input_limit = 1200`,
  `output_limit = 0`. Danach 0 W. L2 meldet noch rund drei Minuten lang
  `pack_state` 0 ⇄ 2 (Ruhe ⇄ Entladen).
- 20:45:56 — Watchdog-Warnung für L2 und L3, wie vorgesehen.
- 20:48:23 — Test 1, von Hand: L2 `input_limit = 300`. Das Gerät übernimmt
  den Wert (die Number hat `doupdate=False`, der Zustand ist also das Echo
  des Geräts). HEMS schreibt im nächsten Zyklus wieder 1200, auch das wird
  übernommen. Leistung bleibt 0 W bis 20:51:23. **Eine Wertänderung am Limit
  löst den Stillstand nicht.** Damit ist der Störwert aus der zweiten
  Zu-tun-Zeile hinfällig.
- 20:52:20/30 — Test 2, von Hand: L2 `ac_mode` auf `output`, zehn Sekunden
  später zurück auf `input`. 0 W bis 20:55:23. **Ein erneuter
  Richtungswechsel löst ihn auch nicht.**
- 20:58:35 — der Betreiber stellt den Zendure-Manager auf `manual` (Leistung
  0), 21:03:21 auf `smart_charging`. `auto_model` springt um 21:02 auf beiden
  Geräten von 0 auf 8 — die Programm-Befehle kommen also an. Trotzdem 0 W.
- 21:04:03 — L3 bekommt `input` erst jetzt, der Nachtritt holt eine
  gedrosselte Umschaltung nach (input → output → input binnen fünf Minuten,
  `_CALL_THROTTLE` merkt sich den Wert, nicht den Wechsel). Kein Einfluss auf
  den Stillstand, aber der Nachtritt deckt die Drossel-Lücke ab.
- 21:04:13/24 — `grid_reverse` von Hand kurz auf `disabled` und zurück.
  Keine Wirkung.
- **21:05:33 — Manager auf `off`. 21:05:35 meldet `auto_model` 0.
  21:05:44 lädt L3 mit 456 W, 21:05:45 L2 mit 336 W, binnen drei Sekunden
  beide über 800 W.**

Die Akkupacks waren während des ganzen Stillstands unauffällig: 23 bis 30 °C,
Zellen 3,27 bis 3,28 V, keine Heiz- oder Tieftemperatur-Sperre,
`soc_status`/`soc_limit` 0. Laden am AC-Eingang lief am selben Vormittag
normal (Überschuss, bis 100 % um 12:11).

### Was daraus folgt

Zweimal, am 09.09. und am 16.09., lief das Laden **neun bis elf Sekunden nach
dem Wechsel des Managers auf `off`** an, und beide Male hatte vorher jeder
andere Weg versagt. `update_operation` ruft dabei `power_off()` auf, also die
`deviceAutomation`-Nachricht mit `autoModel: 0`. Kandidat 3 ist damit nicht
mehr nur plausibel, sondern zweimal reproduziert — allerdings anders als oben
beschrieben: Vor dem Test meldeten beide Geräte bereits `auto_model` 0, es lief
also kein sichtbares Programm. Das Gerät braucht offenbar die **ausdrückliche**
`autoModel: 0`-Nachricht, um nach einer Entladung wieder Netzladung
anzunehmen; der gemeldete Zustand 0 genügt nicht. Deckt sich mit Zendure-HA
#1532 (Hyper 2000, Firmware v2.1.30, lokales MQTT: "Writing `input_limit`
alone does nothing - the hub needs a state change").

Offen bleibt, ob ein Wechsel `manual` → `off` genauso wirkt wie
`smart_charging` → `off` (am 09.09. war es `manual`, am 16.09.
`smart_charging`) und ob die Nachricht auch ohne vorherigen Programm-Wechsel
wirkt. Beides lässt sich über HEMS nicht testen: Der einzige Weg zu
`power_off()` führt über den Manager, und der bleibt für HEMS tabu. Ein
Firmware-Update und ein Neustart der Geräte sind als Lösung ausgeschlossen
(Betreiber, 16.09.2026); ein Fork der Integration ebenfalls.

## Dritter Befund: 24.09.2026, ab 20:47

Derselbe Stillstand, diesmal mit der Freigabe-Automation
`automation.hyper_2000_netzladen_nach_stillstand_freigeben` im Einsatz — und
sie hat ihn nicht gelöst. Zeiten in Ortszeit.

- 20:47:18 — beide Geräte hören von selbst auf zu entladen (L2 rund 680 W,
  L3 rund 665 W, SoC 53 %), zwölf Sekunden vor dem Schalter. Wie am 16.09.
- 20:47:30 — `sensor.hems_speicher_regelung` wechselt auf `laden`,
  `zwangsladung: true`, 1200 W je Gerät, Deckel 100 %. HEMS schreibt
  `ac_mode = input` und `input_limit = 1200`. Danach 0 W.
- 20:49:29 — Reload der Zendure-Integration. Die Verbindung kommt binnen
  Sekunden zurück (`connection_status` 11, `hyper_tmp` läuft weiter);
  `auto_model` meldet auf beiden Geräten 0, `charging_mode` 2,
  `charging_type` 2 (L2) bzw. 1 (L3).
- 20:49:30–35, 20:50:19–24 und 20:50:58–20:52:19 — der Manager geht dreimal
  `off` → `manual` → `off` (die Automation feuerte um 20:52:14).
  `operation_state` folgt jedes Mal (3 → 0 → 3), die Befehle kommen also an.
  **Trotzdem 0 W.**
- ab 20:50:57 — L2 meldet `pack_state` im Sekundentakt 1 ⇄ 2, bei 0 W an
  Netz und Pack. L3 bleibt auf 0. Nicht eingeordnet.
- 20:52:27 — L2 zieht `input_limit` von selbst auf 518 zurück, HEMS schreibt
  um 20:52:41 wieder 1200 (Kandidat 1).
- 20:52:40 / 20:55:40 — Watchdog-Warnung für L3 bzw. L2.

### Was daraus folgt

`manual` → `off` genügt nicht immer. Am 09.09. wirkte er (dort mit
`manual_power` 1400 vorher), am 24.09. dreimal nicht; am 16.09. wirkte erst
`smart_charging` → `off`. Damit ist die offene Frage aus dem zweiten Befund
zur Hälfte beantwortet: Der Wechsel auf `off` allein ist es nicht, der
vorherige Modus zählt. Die Automation schaltet heute auf `manual` und sollte
auf `smart_charging` umgestellt werden. Zusätzlich feuert ihr Template-Trigger
nur beim Übergang falsch → wahr: Bleibt der Stillstand nach dem ersten Versuch
bestehen, versucht sie es nicht noch einmal.

Als Handweg gibt es seit dem 24.09. das HA-Skript
`script.hyper_2000_netzladen_freigeben`. Die erste Fassung (`smart_charging`,
20 s, `off`) lief um 21:04:40 und blieb wirkungslos: 0 W auch eine Minute nach
`off`, `auto_model` die ganze Zeit 0.

Der Unterschied zum 16.09. ist die Dauer. Dort stand der Manager rund sieben
Minuten nicht auf `off`, und `auto_model` sprang um 21:02 auf 8, bevor der
Wechsel auf `off` das Laden freigab. Am 24.09. dauerte keine Phase länger als
80 Sekunden, und `auto_model` blieb jedes Mal auf 0. Arbeitshypothese: Das Gerät
braucht den tatsächlichen Wechsel `auto_model` 8 → 0; eine `autoModel: 0`-Nachricht
an ein Gerät, das schon 0 meldet, bewirkt nichts. Die zweite Fassung des Skripts
wartet deshalb auf `auto_model` 8 an beiden Geräten (höchstens 8 min) und stellt
erst dann auf `off`.

**Widerlegt, 21:07–21:17.** Die zweite Fassung lief ab 21:07:33. In 6,5 Minuten
`smart_charging` blieb `auto_model` auf 0; die 8 kam erst um 21:14:11, nachdem
der Betreiber von Hand über `smart` und `store_solar` geschaltet hatte. Das
Skript stellte um 21:14:22 auf `off`, `auto_model` fiel um 21:14:23 auf 0 —
und die Geräte blieben bei 0 W. Der Wechsel 8 → 0 allein gibt die Netzladung
also nicht frei. Danach meldeten beide Geräte von sich aus `ac_mode = output`
(21:15:39/57), HEMS korrigierte um 21:16:52 auf `input`.

Dritte Fassung (Vorschlag des Betreibers, mehrfaches Umschalten): bis zu vier
Runden `manual` → `smart_charging` → `off` mit je 20 s Verweildauer, Abbruch,
sobald ein Gerät mehr als 50 W aus dem Netz zieht.

**Gelöst, 21:23.** Vorgeschichte: Der Betreiber hatte den Manager ab 21:16:30
auf `manual` stehen lassen (mit kurzen Wechseln um 21:19); `auto_model` stand
seit 21:16:54 auf beiden Geräten auf 8. Um 21:22:29 lief ein weiterer Reload
der Zendure-Integration, danach meldeten beide Geräte wieder 8.

- 21:22:44 — dritte Fassung gestartet, Runde 1: `manual` (stand schon).
- 21:23:04 — `smart_charging`.
- 21:23:24 — `off`; `auto_model` fällt um 21:23:25/26 auf 0.
- **21:23:42 — L3 lädt mit 1159 W, 21:23:45 L2 mit 1188 W**, binnen Sekunden
  beide bei 1200 W. Das Skript endet nach der ersten Runde (21:23:42).

Abstand `off` → Leistung: 18 bzw. 21 Sekunden.

### Was die drei Versuche am 24.09. unterscheidet

| Zeit  | `auto_model` 8 vor `off` | Modi vor `off`                     | Ergebnis |
|-------|--------------------------|------------------------------------|----------|
| 20:49–20:52 | nie              | `manual` (5 s bis 80 s)            | 0 W      |
| 21:14:22 | 11 s                  | `smart` → `store_solar` → `smart_charging` → `manual` | 0 W |
| 21:23:24 | rund 6,5 min (Reload dazwischen) | `manual` → `smart_charging` | lädt |

Am 16.09. stand `auto_model` 3,5 Minuten auf 8, bevor `off` wirkte. Das
stützt eine neue Arbeitshypothese: Die Freigabe braucht ein Gerät, das eine
Weile **im Programm gelaufen** ist (`auto_model` 8 über Minuten), nicht bloß
den Wechsel 8 → 0. Nicht ausgeschlossen ist, dass der Reload um 21:22:29
beigetragen hat; um 20:49 hat ein Reload allein nichts bewirkt.

## Zu tun

- [ ] Beim nächsten Stillstand `script.hyper_2000_netzladen_freigeben`
      (dritte Fassung) ohne Vorlauf von Hand starten: Wirkt es auch, wenn
      `auto_model` vorher nicht schon Minuten auf 8 stand? Wenn nicht, im
      Skript vor `off` auf `auto_model` 8 warten und die 8 einige Minuten
      halten lassen (Hypothese aus dem dritten Befund).
- [x] Freigabe-Automation auf das Skript umgestellt (24.09.2026, 21:30):
      Sie ruft `script.hyper_2000_netzladen_freigeben` auf statt eines eigenen
      `manual` → `off`. Neben dem Template-Trigger (2 min Stillstand) prüft ein
      Trigger alle 10 Minuten erneut, weil der Template-Trigger je Stillstand
      nur einmal feuert. Bedingungen: Stillstand-Template wahr, Manager `off`,
      Skript läuft nicht, Regelung seit mindestens 2 min unverändert.
      Betreiber-Automation, nicht HEMS.

- [ ] Beim nächsten natürlichen Stillstand prüfen, ob der Nachtritt allein
      genügt: `custom_components.hems.actuator` auf INFO/DEBUG stellen und
      mitschreiben, welcher Schreibvorgang unmittelbar vor dem Anlaufen liegt.
      Läuft der Speicher ohne Zutun am Manager wieder an, sind Kandidat 1 oder 2
      die Ursache und der Nachtritt reicht. Bleibt er stehen, ist es Kandidat 3
      — dann ist die nächste Frage, ob sich ein laufendes Automatik-Programm
      über die Property-Wege löschen lässt, die HEMS offenstehen.
- [ ] Echo-Verhalten klären: Schreibt HEMS denselben Wert erneut, führt das
      Gerät ihn dann aus, oder braucht es eine Änderung? Falls Änderung:
      Störwert (einen Zyklus lang `wert − 1`) in den Nachtritt aufnehmen. Der
      Nachtritt schreibt heute denselben Wert.
- [ ] Entlade-Richtung: Der Nachtritt gilt bereits für beide Richtungen, ein
      Befund liegt aber nur für das Laden vor. Prüfen, ob ein stummes Entladen
      real vorkommt und ob der Nachtritt dort nicht mit der Freischwimm-Probe
      (`tests/test_speicher_freischwimmen.py`) kollidiert.
- [ ] Dauerfeuer im CV-Taper bewerten: Der Nachtritt feuert jeden Zyklus,
      solange `speicher_folgt` falsch ist. Ein Akku bei 95 %, der noch 30 W
      nimmt, liegt unter `SPEICHER_LADEN_MIN_W`, aber über `SPEICHER_VOLL_SOC`
      — also drei MQTT-Schreibvorgänge je Minute mit identischen Werten, bis er
      voll ist. Harmlos und dieselbe Klasse wie `soc_set` mit `ohne_drossel`,
      aber falls der Bus darunter leidet, gehört hier eine eigene Schwelle hin.
- [ ] Prüfen, ob HEMS den Rückzug des Geräts (Kandidat 1) sichtbar machen soll:
      Ein Setpoint, der binnen Minuten von selbst zurückfällt, steht heute
      nirgends — weder im Sensor noch im Log.
