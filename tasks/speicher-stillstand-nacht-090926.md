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

## Zu tun

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
