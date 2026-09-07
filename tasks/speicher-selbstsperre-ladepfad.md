# Speicher-Selbstsperre über den Lade-Pfad (vierter Fund derselben Ursache)

**Priorität:** P0 — die Anlage regelt seit Stunden nur noch mit einem von drei
Speichern und zieht dabei Strom aus dem Netz, den sie im Haus stehen hat.

## Befund (gemessen am 07.09.2026, ~17:12)

`sensor.hems_speicher_regelung`:

```
soll_w = 1200.0        fehler_w = 1409.0
zuteilung = [{ "name": "L2", "watt": 1200 }]
abgemeldet = ["L1", "L3"]
lade_deckel_soc = 100.0
```

Der Regler will 1409 W ausregeln, kann aber nur 1200 W zuteilen: L2 hängt an
seiner Ausgangsgrenze (`inverse_max_power = 1200 W`), L1 und L3 sind verriegelt
abgemeldet und nehmen an der Zuteilung nicht teil. Die Differenz kam aus dem
Netz — `sensor.hems_netzsaldo = 1384 W` bei einem Hausverbrauch von 2586 W.

L1 und L3 waren dabei gesund und zu 99 % geladen (`sensor.hyper_2000_l1/l3_electric_level = 99`,
`available_kwh = 3.42` je Einheit), ihre Geräte meldeten selbst
`soc_limit = 1` — Ladeschluss erreicht. Ihre AC-Ausgangsgrenze stand auf 0 W.

### Die Kette

Home-Assistant-Log:

```
14:47:40 HEMS-Actuator: Speicher L1 lädt nicht, obwohl seit 5 min Leistung zugeteilt ist (gemessen: 0.0 W)
14:47:40 HEMS-Actuator: Speicher L3 lädt nicht, obwohl seit 5 min Leistung zugeteilt ist (gemessen: 0.0 W)
15:17:40 HEMS-Actuator: Speicher L3 lädt nicht, obwohl seit 5 min Leistung zugeteilt ist (gemessen: 0.0 W)
```

1. Nachmittags-Restüberschuss wird L1/L3 zugeteilt. Beide stehen bei 99 %, das
   BMS ist im Taper und nimmt nichts mehr auf — gemessen 0 W.
2. `ladeauftrag_in_frist_erfuellbar` (actuation.py:258) soll genau das abfangen,
   greift hier aber nicht: `frei_wh = (100 − 99) / 100 × 3,7 kWh = 37 Wh` gegen
   `zugeteilt_w × 5 min`. Die Ausnahme trägt erst ab **444 W** Zuteilung; der
   Restüberschuss lag darunter. Also „nicht gefolgt“.
3. Zugleich schweigt der push-basierte SoC-Sensor eines ruhenden vollen Akkus
   länger als `STORAGE_STALE_MIN = 15 min`.
4. Beide Auslöser zusammen → `speicher_stumm_latch` (strategies/types.py:361)
   verriegelt L1 und L3.
5. Entriegelt wird ausschließlich über eine frische SoC-Meldung. Ein
   verriegelter Speicher bekommt 0 W, ruht weiter, meldet nichts. Die Sperre
   hält sich selbst — abends, als seine Leistung gebraucht wurde, war er noch
   gesperrt.

### Das Muster hinter dem Fall

Vierter Fund derselben Ursache: 15.08.2026 (echter Ausfall, Entlade-Pfad),
17.08.2026 (Fix: zweiter Auslöser gefordert), 19.08.2026 (Fix:
`ladeauftrag_in_frist_erfuellbar`), 07.09.2026 (dieser Befund). Dreimal wurde
der Auslöser verfeinert, dreimal fand die Sperre einen neuen Weg zuzuschnappen.

Zwei Asymmetrien, die die bisherigen Fixes nicht adressiert haben:

- **Der einzige echte Ausfall kam über das Entladen, beide Fehlalarme über das
  Laden.** Der Schaden, gegen den die Sperre existiert, ist entlade-spezifisch:
  `_verteile_entladen` bündelt greedy, an einer toten Einheit hängt die GANZE
  Anforderung. `_verteile_laden` verteilt proportional — der Anteil einer toten
  Einheit geht ins Netz, gedeckelt, nicht die Anlage steht still. Asymmetrischer
  Schaden, symmetrischer Latch.
- **Die Verriegelung hat keinen Rückweg, der ohne Zutun des verriegelten Geräts
  auskommt.** Der Docstring von `HemsCoordinator._stumm` verbietet den Rückweg
  „HEMS hört auf zu befehlen“ zu Recht (er löschte seinen eigenen Beweis). Ein
  probeweises Zuteilen ist davon nicht betroffen: Es erzeugt den Beweis, statt
  ihn zu tilgen.

## Betroffene Dateien

- `custom_components/hems/actuation.py` — `ladeauftrag_in_frist_erfuellbar`
- `custom_components/hems/actuator.py` — `Actuator._quittung_speicher`,
  `SPEICHER_QUITTUNG_FRIST`
- `custom_components/hems/coordinator.py` — `HemsCoordinator._stumm`,
  `_speicher_stumm`
- `custom_components/hems/strategies/types.py` — `speicher_stumm_latch`,
  `StorageState.stale`, `PlanResult.speicher_nicht_uebernommen`, `ControlResult`
- `custom_components/hems/strategies/battery.py` — `_storage_control`,
  `_verteile_entladen`
- `custom_components/hems/sensor.py` — Attribute von `speicher_regelung`
- Tests: `tests/test_speicher_selbstsperre.py`, `tests/test_speicher_quittung.py`,
  `tests/test_speicher_abgemeldet.py`

## Entscheidung (Architekt, 07.09.2026)

Nichts hier ist ein Verdikt. Der Koordinator entscheidet, was davon in den
Schnitt geht.

### Gemessene Lage (Datei:Zeile, Stand HEAD 32399a4)

Die Verriegelung und ihre Zuführungen:

- `speicher_stumm_latch` — `strategies/types.py:361-377`. Setzt bei
  `schweigt ∧ nicht_gefolgt`, löscht ausschließlich bei `¬schweigt`.
  Richtungsblind: `nicht_gefolgt` ist ein Bool ohne Herkunft.
- `HemsCoordinator._stumm` — `coordinator.py:630-669`. `schweigt` aus
  `_abgemeldet(soc_entity, 15 min)` (`coordinator.py:671-691`, liest
  `last_reported`), `nicht_gefolgt = s.name in offen`.
- `offen` — `coordinator.py:1119-1124`:
  `set(self.data.plan.speicher_nicht_uebernommen)` des **vorigen** Zyklus.
- `speicher_nicht_uebernommen` wird an genau einer Stelle geschrieben:
  `actuator.py:527-528` in `_quittung_speicher` (`actuator.py:439-528`), und
  zwar **für beide Richtungen** — der Entlade- und der Lade-Zweig laufen auf
  dieselbe Zeile. Gelesen wird das Feld in `sensor.py:295` (Attribut
  `nicht_uebernommen`) und `coordinator.py:1123` (der Latch). Ein Feld, zwei
  Leser mit verschiedenem Anspruch: Der Sensor will alles sehen, der Latch darf
  nur Beweise bekommen.
- Die Lade-Ausnahme: `actuator.py:480-495` ruft
  `ladeauftrag_in_frist_erfuellbar` (`actuation.py:258-289`) mit
  `grenze_soc = plan.lade_deckel_soc`, es sei denn `laden_statt_einspeisen`
  steht — dann 100. Bei `lade_deckel_soc = 80` und `ist_soc = 79` rechnet sie
  also gegen den Deckel, nicht gegen das physische Ladeende; genau die Regel,
  die die Aufgabe oben als Maskierung eines echten Ausfalls benennt, steht
  heute im Code.
- Die Quittung läuft nur innerhalb von `_apply_battery` (`actuator.py:373-437`)
  und nur für Speicher mit `watt > 0` (`laedt_soll`/`entlaedt_soll`,
  `actuator.py:386-387`). Ein verriegelter Speicher bekommt 0 W, wird also nie
  quittiert. Der Latch hat damit **keinen Beweisweg außer der SoC-Meldung** —
  gemessen, nicht abgeleitet.

Was `stale` in der Regelung bewirkt (`strategies/battery.py`):

- `known = [s … if s.soc is not None and not s.stale]` — Zeile 191. Zuteilung
  im Entlade-Zweig (454-465) und Lade-Zweig (466-490) nur über `known`.
- `bat_ist` ohne stale-Leistung — Zeilen 226-228 (bewacht von
  `test_eingefrorene_leistung_geht_nicht_in_den_regler`,
  `tests/test_speicher_abgemeldet.py:110-131`).
- `max_ent = sum(max_discharge_w for known)` — Zeile 272; `soll` wird darauf
  gekappt (Zeile 274). Wichtig für die Probe: Ein bekannter Speicher an der
  Reserve hat `anteil ≤ 0`, bekommt in `_verteile_entladen` 0 W, zählt aber
  weiter in `max_ent`.
- Alle stale → Frühausstieg mit passiver Empfehlung, Zeilen 192-212.

Die Schadens-Asymmetrie ist im Code, nicht nur in der Erzählung:

- `_verteile_entladen` — `battery.py:332-369`: sortiert nach Energie über der
  Reserve, gibt der ersten Einheit `min(rest, max_discharge_w)`. Ein Speicher
  mit eingefrorenen 100 % gewinnt die Rangfolge und nimmt die ganze
  Anforderung.
- `_verteile_laden` — `battery.py:371-434`: proportional zur freien Kapazität,
  gedeckelt auf `max_charge_w`. Eine tote Einheit bekommt ihren Anteil; der
  geht ins Netz, der Rest lädt.

**Weitere Verriegelungen nach demselben Muster: keine.** Gezählt:

- Warmwasser: `plan.warmwasser_nicht_uebernommen` (Bool), geschrieben
  `actuator.py:343` aus `plan_ww_action` (`actuation.py:65-165`). Meldung plus
  ein aktiver Rückweg (`rueckweg`, schreibt einmal neu). Schließt nichts aus.
- Heizung: `plan.heizung_nicht_uebernommen` (Liste), `_turn_heizung`
  `actuator.py:597-648`. Meldet einmal, schreibt denselben Befehl nicht nach;
  verlässt den Zustand, sobald das Gerät die Lage zeigt oder der Befehl
  wechselt. Der Planner liest das Feld nie (einziger Leser `sensor.py:334`).
  Kein Ausschluss, kein Selbsthalten.
- `_latch` — `types.py:380-399`: Schmitt-Trigger mit zwei Schwellen,
  symmetrischer Ausgang. Anderes Muster.

Das Ausschluss-Latch mit einseitigem Rückweg steht also genau einmal. Die
„Gegenpart“-Formulierung in den Docstrings meint die *Quittung* (Frist statt
Update-Ereignis), nicht die Verriegelung. Kein Sammelumbau nötig — das ist ein
Ergebnis.

Tests, die Quelltext per AST pinnen und beim Umbau mitgehen müssen (sonst hält
ein Coder die alte Form am Leben, um grün zu werden):

- `tests/test_speicher_selbstsperre.py:254-263` pinnt
  `"laden_soll and ladeauftrag_in_frist_erfuellbar"`, `:266-271` pinnt
  `"SPEICHER_QUITTUNG_FRIST.total_seconds() / 3600"`.
- `tests/test_speicher_quittung.py:233-235` pinnt
  `"plan.speicher_nicht_uebernommen"` in `_quittung_speicher`.
- `tests/test_speicher_selbstsperre.py:146-168` pinnt `self._speicher_stumm`
  und den Aufruf der gemeinsamen Latch-Funktion im Coordinator.
- `tests/test_speicher_abgemeldet.py:199-206` pinnt die Übergabe von `stale`
  in den Planner.
- Kein Test instanziiert `Actuator`; die HA-nahen Nähte werden im Projekt über
  den Syntaxbaum geprüft. Das ist das bestehende Muster, der Schnitt bleibt
  dabei.

### Frage 1 — Ja. Nur eine Entlade-Verweigerung verriegelt.

Das Kriterium, das die drei Nachbesserungen nicht hatten: **Entladen ist der
einzige Befehl, den jeder gesunde Speicher mit SoC über der Reserve ausführen
kann.** Laden verweigert ein gesundes Gerät aus vielen Gründen — voll, im
CV-Taper, zu kalt, zu warm, Zellausgleich, geräteseitiger Ziel-SoC. Eine
Lade-Verweigerung ist deshalb kein Beweis für einen Ausfall, sondern
mehrdeutig, und diese Mehrdeutigkeit ist physikalisch, keine Schwelle. Genau
darum hat jede Verfeinerung des Auslösers einen neuen Fehlalarm gefunden: Sie
hat an einer Schwelle gedreht, wo es keine gibt.

Die Schadens-Asymmetrie sagt dasselbe von der anderen Seite. Die Sperre
existiert gegen den greedy-Schaden vom 15.08. (`_verteile_entladen`); im
Lade-Zweig gibt es diesen Schaden nicht. Ein Latch, der mehr abdeckt als den
Schaden, gegen den er gebaut ist, kostet an genau den Stellen, wo er nichts
schützt.

Kosten der Trennung, ausdrücklich: Eine tagsüber ausgefallene Einheit bleibt
bis zu ihrem ersten Entlade-Auftrag in der Lade-Zuteilung; ihr proportionaler
Anteil geht als Einspeisung verloren. Gedeckelt durch ihren Anteil, nicht die
Anlage. Dieselbe Toleranz gilt heute für jede Einheit, die noch nie einen
Befehl bekommen hat.

**Form (die billigste, die trägt):**

- `PlanResult.speicher_nicht_uebernommen` bleibt, wie es ist — beide
  Richtungen, für Sensor, Log und Entscheidungs-Log. Der Betreiber soll eine
  Einheit, die nicht lädt, weiterhin sehen.
- Neues Feld `PlanResult.speicher_entladen_verweigert: list[str]`. Befüllt
  **nur** im Entlade-Zweig von `_quittung_speicher`, nach Ablauf der Frist,
  bei gemessener Nicht-Ausführung. Der Lade-Zweig schreibt es nie.
- `coordinator.py:1119-1124` bildet `offen` aus dem neuen Feld.
- `speicher_stumm_latch`: Keyword `nicht_gefolgt` → `entladen_verweigert`. Der
  Name trägt dann die Regel; „nicht gefolgt“ ist die Formulierung, die den
  Lade-Zweig wieder einlädt. Die Tests in `test_speicher_selbstsperre.py`
  nutzen das Keyword und ziehen mit.
- Docstrings und Kommentare, die „beide Richtungen“ für die *Verriegelung*
  behaupten oder das Feld als „Ladeleistung nicht ziehen“ beschreiben:
  `_quittung_speicher` (actuator.py:448-474), `_stumm`
  (coordinator.py:631-662), `HemsCoordinator.__init__` (coordinator.py:445-451),
  `StorageState.stale` (types.py:36-46), `speicher_stumm_latch` (types.py:364-371),
  `PlanResult.speicher_nicht_uebernommen` (types.py, Kommentar über dem Feld),
  `SPEICHER_QUITTUNG_FRIST` (actuator.py:93-109: „Gilt in BEIDE Richtungen“
  bleibt für die *Warnung* richtig, für den Latch nicht — sagen, welches),
  `STORAGE_STALE_MIN` (const.py:178-195).

### Frage 2 — Ja. Freischwimm-Probe im Entlade-Zweig; kein neuer Entriegelungs-Eingang.

Warum das Verbot aus dem `_stumm`-Docstring die Probe nicht trifft: Der
verbotene Rückweg („HEMS befiehlt nicht mehr“ → entriegeln) löscht den Beweis,
weil er die Einheit zurück in `known` lässt. Dort gewinnt sie mit
eingefrorenem SoC die Rangfolge und bekommt für fünf Minuten die ganze
Anforderung — das ist der 15.08. im Takt. Die Probe lässt die Einheit
**außerhalb von `known`**: keine Rangfolge, kein Beitrag zu `bat_ist`, kein
Anteil an dem, was die bekannten Speicher liefern. Sie bekommt allein den
Rest, den kein bekannter Speicher decken kann, und die bestehende Quittung
misst die Antwort. Das erzeugt den Beweis, den die Sperre heute nicht bekommen
kann:

- Folgt die Einheit, entlädt sie, ihr SoC tickt, der push-Sensor meldet,
  `schweigt` fällt, die **bestehende** Entriegelung greift. Kein zweiter
  Eingang am Latch nötig.
- Folgt sie nicht, landet sie nach fünf Minuten in
  `speicher_entladen_verweigert`, der Latch bleibt — jetzt mit frischem Beweis
  in jedem Zyklus statt mit einem alten, der sich selbst hält.

**Bedingung (entscheidend, hier baut man es sonst falsch):** Modus `entladen`
UND nach der Zuteilung an `known` bleibt ungedeckter Rest:

```
soll_wunsch = Reglerforderung nach allen Deckel-Klauseln (Wallbox-Klauseln
              battery.py:287-321), aber VOR der Kappung auf max_ent (Zeile 274)
rest        = soll_wunsch − Σ zuteilung(known)
Probe nur, wenn rest ≥ CONTROL_MIN_SETPOINT_W
```

Nicht „bekannte Speicher am Leistungsdeckel“. Ein bekannter Speicher an der
Reserve hat `anteil ≤ 0`, bekommt 0 W, zählt aber in `max_ent`
(battery.py:191/272). Mit dem Kriterium „Σ Zuteilung ≥ max_ent“ hieße das
0 ≥ 1200, keine Probe, und der verriegelte volle Nachbar bliebe draußen,
während das Haus aus dem Netz zieht — die Schadensklasse vom 07.09. in
anderer Verkleidung. Mit dem Rest-Kriterium fällt derselbe Fall richtig.

**Warum die Probe dem arbeitenden Speicher nichts wegnimmt:** Die Zuteilung an
`known` wird zuerst und unverändert berechnet — derselbe Code wie heute, mit
denselben Eingaben. Danach verteilt `_verteile_entladen(stale_anteile, rest)`
allein den Rest über die verriegelten Einheiten, mit derselben Anteil-Formel
wie für `known` (Energie über der Reserve, Kaltreserve-Regel), gerechnet auf
den letzten bekannten SoC. Der Netzbezug kann in keinem Zyklus steigen: Die
Probe ersetzt Netzbezug oder bewirkt nichts. Das ist die Eigenschaft, die der
Nachsteller belegt (`zuteilung[known]` mit und ohne verriegelte Nachbarn
identisch, über ein Raster von Saldo-Werten).

Mit den Zahlen vom 07.09.: `bat_ist = 1200` (L2), `fehler = 1409`,
`gain = 0.65` → `soll_wunsch = 2116`, Rest 916 W → L1 bekommt 916 W, L3 0 W
(Greedy über die verriegelten). L2 bleibt bei 1200.

**Ein Transient, der benannt gehört, statt versteckt:** `bat_ist` schließt
stale-Leistung aus (battery.py:226-228, mit Test). Solange die probierte
Einheit stale ist, sieht der Regler ihre Lieferung nicht als Basis; die Probe
konvergiert auf `f·g/(1+g)` ≈ 40 % des Rests bei g = 0,65 — beim 07.09. rund
555 W Probe bei rund 855 W Restbezug. Das hält, bis der SoC einen Punkt tickt
(37 Wh je % bei 3,7 kWh, bei 555 W rund vier Minuten) und die Einheit über
die bestehende Entriegelung in `known` zurückkehrt. Danach regelt der normale
Pfad. Der Nachsteller darf in der Probe-Phase deshalb **nicht** „Netzbezug →
0“ verlangen, sondern „Netzbezug fällt und steigt in keinem Zyklus“.

Offene Messung, die nur ein Lauf liefert: Wie viele Zyklen die Probe-Phase an
den Hyper 2000 tatsächlich dauert (Erwartung: ein bis zwei SoC-Ticks). Liegt
sie gemessen über zehn Minuten, ist der nächste Schritt eine Integration des
zuletzt kommandierten Probe-Werts über `PlanFlags` — nicht in diesem Schnitt.

**Sichtbarkeit:** `ControlResult.probe_namen: list[str]`, in `sensor.py:272-295`
als Attribut `probe` durchgereicht. Eine probierte Einheit steht zugleich in
`zuteilung` und in `abgemeldet`; ohne eigenes Feld widerspricht sich der
Sensor selbst. `soll_w` bleibt der Wert für `known`, die Probe steht nur in
`zuteilung` und `probe_namen`.

**Was ich nicht bauen würde:** Eine zweite Entriegelung „hat einem Befehl
gefolgt“. Ein eingefrorener Leistungssensor mit einem Wert ≠ 0 würde damit
eine tote Einheit entriegeln. Zwar verriegelt eine solche Einheit heute
ohnehin nie (die Quittung sähe „folgt“) — aber der zweite Eingang kauft
Sensor-Konsistenz für eine Minute gegen ein Risiko, das nicht gemessen ist.
Die Probe erzeugt genau die Bedingung, die den einen bestehenden Eingang
auslöst; das reicht.

**Grenze, die der Koordinator trägt:** Der Rückweg öffnet sich erst, wenn
ungedeckter Rest besteht. Unter niedriger Last bleibt eine zu Unrecht
verriegelte Einheit draußen — schadlos, weil nichts zu decken ist, aber
sichtbar als `abgemeldet`. Das steht so in der Abnahme.

### Frage 3 — Das Muster steht einmal.

Gezählt oben. Warmwasser und Heizung führen Melde- und Wiederhol-Buchführung,
kein Ausschluss-Latch; `_latch` ist ein Schmitt-Trigger. Kein Sammelumbau.

### Frage 4 — `ladeauftrag_in_frist_erfuellbar` entfällt ersatzlos.

Nach Frage 1 schützt die Funktion nichts mehr außer einer Log-Zeile und einem
Sensor-Eintrag. Und ihre Prämisse ist falsch: Sie modelliert den Akku als
Verbraucher, der bis zur Grenze alles nimmt, was zugeteilt ist. Im CV-Taper
nimmt er, was das BMS zulässt, unabhängig von der Zuteilung — die Zuteilung
ist eine Obergrenze, keine Nachfrage. Deshalb trägt sie bei 444 W und
scheitert bei 300 W, und jede Nachbesserung an der Formel fände die nächste
Zahl, bei der sie scheitert. Dazu der Befund oben: Sie rechnet heute gegen
`plan.lade_deckel_soc` (actuator.py:487), nicht gegen das physische Ladeende.

Ersatz für die Lade-**Warnung** (nicht für den Latch): eine feste physische
Schwelle. `ist_soc ≥ SPEICHER_VOLL_SOC` (99.0, Zendure meldet 100 % faktisch
nie) → im Lade-Zweig wird nicht quittiert, Uhr und Meldeflagge zurück wie
heute bei „kein Befehl“. Darunter bleibt die Warnung scharf. Sie ist dann
wieder, was sie sein soll: ein Hinweis für den, der hinschaut — und der Latch
hängt nicht mehr an ihr.

### Frage 5 — `soc_limit` bleibt draußen.

Nach dem Schnitt wäre die Lade-Warnung der einzige Abnehmer. Dafür kein
Config-Feld je Speicher, keine Migration, kein `config_check`-Zweig. Die
Semantik ist außerdem Zendure-spezifisch; für andere Geräte gäbe es nichts
einzutragen. Wiedervorlage nur, wenn die 99-%-Schwelle an einem Gerät
gemessen falsch liegt.

## Abnahme

- [x] (A) Regressionstest gegen die **kaputte** Fassung rot: Eine
      Lade-Verweigerung (gemessen 0 W nach `SPEICHER_QUITTUNG_FRIST`,
      beliebige Zuteilung, beliebiger SoC) landet **nicht** in dem Feld, aus
      dem `coordinator.py` `offen` bildet. Heute landet sie in
      `speicher_nicht_uebernommen`, und das ist dieses Feld — der Test ist
      also an der Naht im Coordinator UND an der Naht im Actuator rot.
- [ ] Die Szene vom 07.09. (`ist_soc = 99`, `capacity_kwh = 3.7`, Zuteilung
      unterhalb 444 W, Frist 5 min) erzeugt nach Subtask C auch keine
      Warnung mehr; vor Subtask C bleibt die Warnung, verriegelt aber nicht.
- [x] (A) Ein voller, ruhender Speicher wird durch einen Ladeauftrag, den er nicht
      annehmen kann, nicht mehr verriegelt — unabhängig von der Höhe der
      Zuteilung.
- [x] (A) Ein Speicher, der einem **Entlade**-Auftrag nicht folgt, wird weiterhin
      verriegelt (Befund vom 15.08.2026 bleibt abgedeckt); der Entlade-Zweig
      schreibt weiterhin in das Feld, aus dem `offen` gebildet wird.
- [x] (B) Ein zu Unrecht verriegelter Speicher findet ohne Reload und ohne
      Quittierung von Hand zurück in die Zuteilung, sobald ungedeckter Rest
      besteht (Modus `entladen`, `rest ≥ CONTROL_MIN_SETPOINT_W`).
- [x] (B) Ein Freischwimm-Versuch nimmt dem arbeitenden Speicher keine Leistung weg:
      `zuteilung[known]` ist mit und ohne verriegelte Nachbarn identisch, über
      ein Raster von Saldo-Werten (kein Zyklus, in dem der Netzbezug durch die
      Probe steigt).
- [x] (B) Die Probe greift auch, wenn ein bekannter Speicher an der Reserve steht
      (`anteil ≤ 0`, 0 W zugeteilt) und ein verriegelter voller daneben — der
      Fall, den ein Deckel-Kriterium statt des Rest-Kriteriums verfehlt.
- [x] (B) Keine Probe, wenn `known` die Forderung deckt; keine Probe im Modus
      `laden`; keine Probe an eine verriegelte Einheit unter der Reserve; keine
      Probe unter `CONTROL_MIN_SETPOINT_W`.
- [x] (B) Ein probierter Speicher steht im Sensor `speicher_regelung` als `probe`,
      nicht nur als `abgemeldet`.
- [x] (B) `test_abgemeldeter_speicher_bekommt_keine_entladeleistung`
      (`tests/test_speicher_abgemeldet.py:58-75`) bleibt grün: bei
      `saldo = 1100` decken L2/L3 die Forderung, kein Rest, keine Probe.
- [ ] Nach Subtask C gibt es keinen Aufrufer von `ladeauftrag_in_frist_erfuellbar`
      mehr; die AST-Pins in `tests/test_speicher_selbstsperre.py:254-271`
      sind durch Pins auf die neue Form ersetzt, nicht ersatzlos gelöscht.
- [ ] Volle Suite grün (fährt der Koordinator).

## Schnitt

Reihenfolge A → B → C. A ist der P0-Fix und geht allein in einen Commit. B ist
davon im Code unabhängig (battery.py gegen actuator/coordinator), aber im
Arbeitsbaum schreibt nur einer zur Zeit. **C darf nicht vor A landen:** Ohne A
öffnet das Entfernen der Ausnahme den Latch vom 19.08. wieder.

### A — Der Latch bekommt nur noch Entlade-Beweise (Reasoning: high)

Vierte Änderung an derselben Zustandsmaschine; der 25.08.-Fall im
Arbeitsmodell (Regressionstest grün gegen kaputten Code) ist genau dieser.

Ändert:

- `strategies/types.py`: `PlanResult.speicher_entladen_verweigert: list[str]`
  mit Kommentar, warum es neben `speicher_nicht_uebernommen` steht (zwei Leser,
  zwei Ansprüche). `speicher_stumm_latch`: Keyword `nicht_gefolgt` →
  `entladen_verweigert`, Docstring nach.
- `actuator.py` `_quittung_speicher`: Nach Ablauf der Frist und gemessener
  Nicht-Ausführung schreibt der Entlade-Zweig zusätzlich in
  `speicher_entladen_verweigert`; der Lade-Zweig schreibt weiterhin nur in
  `speicher_nicht_uebernommen` (Warnung, Sensor). Docstring: „Beide
  Richtungen“ gilt für die Warnung, die Verriegelung bekommt nur Entladen —
  mit dem Kriterium aus Frage 1 als Begründung.
- `coordinator.py:1119-1124`: `offen` aus `speicher_entladen_verweigert`.
  Docstring `_stumm` und Kommentar in `__init__` (445-451) nach.
- Kommentare: `StorageState.stale`, `SPEICHER_QUITTUNG_FRIST`,
  `STORAGE_STALE_MIN` (siehe Liste unter Frage 1).

Tests (Klassenebene: `pytest tests/test_speicher_selbstsperre.py
tests/test_speicher_quittung.py tests/test_speicher_abgemeldet.py`):

- Neu, **rot vor dem Umbau**: AST-Test im Stil von
  `test_offen_kommt_aus_der_quittung_des_actuators`, der pinnt, dass `offen`
  im Coordinator aus `speicher_entladen_verweigert` gebildet wird.
- Neu, **rot vor dem Umbau**: AST-Test, dass in `_quittung_speicher` der
  Append auf `speicher_entladen_verweigert` unter `not laden_soll` steht und
  im Lade-Zweig kein solcher Append existiert.
- Bestehende Latch-Tests (`test_speicher_selbstsperre.py:85-140`): Keyword
  umbenennen, Aussagen unverändert. `test_stille_allein_verriegelt_niemanden`
  bekommt einen Kommentar: „Lade-Verweigerung ist ab jetzt derselbe Fall“.
- `test_speicher_quittung.py:233-235` behält den Pin auf
  `speicher_nicht_uebernommen` (Sensor-Weg) und bekommt einen zweiten auf das
  neue Feld.
- Nachsteller: die beiden neuen AST-Tests. Sie werden rot, sobald jemand den
  Lade-Zweig wieder an den Latch hängt oder `offen` auf das Sammel-Feld
  zurücksetzt.

### B — Freischwimm-Probe im Entlade-Zweig (Reasoning: high)

Zentraler Regelpfad; die Gefahr ist, dem arbeitenden Speicher Leistung zu
nehmen, und die sieht ein abhakender Durchlauf nicht.

Ändert:

- `strategies/battery.py` `_storage_control`, Entlade-Zweig (454-465):
  `soll_wunsch` vor der `max_ent`-Kappung (Zeile 274) festhalten und durch die
  Wallbox-Klauseln 287-321 mitführen, damit „kein Akkustrom ins Auto“ auch für
  die Probe gilt (die Klauseln deckeln heute nur das gekappte `soll`). Nach `ctrl.zuteilung = _verteile(anteile, soll, laden=False)`:
  `rest = soll_wunsch − Σ zuteilung`; wenn `rest ≥ CONTROL_MIN_SETPOINT_W`,
  `stale_anteile` mit derselben Anteil-Formel über
  `[s for s in inp.storages if s.stale and s.soc is not None]` bilden,
  `_verteile_entladen(stale_anteile, rest)` aufrufen, Ergebnisse mit
  `watt > 0` an `ctrl.zuteilung` anhängen und in `ctrl.probe_namen`
  eintragen. `bat_ist`, `soll`, `soll_w` und die Zuteilung an `known` bleiben
  unberührt — das ist die Invariante. Der Frühausstieg „alle stale“
  (192-212) bleibt, wie er ist.
- `strategies/types.py` `ControlResult.probe_namen: list[str]` mit
  Kommentar (was eine Probe ist, warum sie nichts kostet, warum sie neben
  `abgemeldet_namen` steht).
- `sensor.py:272-295`: Attribut `probe` neben `abgemeldet`. Falls das Entscheidungs-Log
  (`test_sensor_und_log_zeigen_den_ausfall`,
  `tests/test_speicher_abgemeldet.py:209-216`) `abgemeldet` führt, führt es
  auch `probe`.
- Docstring `_stumm` (coordinator.py): Der Absatz „Entriegelt wird
  ausschließlich über eine frische Meldung“ bleibt wahr und bekommt den Satz,
  dass die Probe in `_storage_control` diese Meldung herbeiführt.

Tests (neue Datei `tests/test_speicher_freischwimmen.py`, HA-frei über
`compute_plan` und die Factories; dazu `tests/test_speicher_abgemeldet.py`):

- **Rot vor dem Umbau:** Szene 07.09. — L2 88 % `power_w = 1200`, L1/L3 99 %
  `stale=True`, `saldo_w = 1409`: L2 bleibt bei 1200, L1 bekommt ≥ 60 W,
  `probe_namen == ["L1"]`, L3 0 W.
- **Rot vor dem Umbau:** Bekannter Speicher an der Reserve (`soc = 10`,
  `reserve_soc = 10`) neben einer verriegelten vollen Einheit, Netzbezug:
  die verriegelte bekommt die Probe. Das ist der Test gegen das
  Deckel-Kriterium.
- Invarianz: für `saldo_w in (100, 500, 1100, 1409, 2500, 4000)` ist
  `zuteilung[known]` mit und ohne stale-Nachbarn identisch; `soll_w`
  identisch.
- Keine Probe: `known` deckt (saldo 500, L2 allein); Modus `laden`
  (saldo −1500, stale-Einheit mit freier Kapazität bleibt bei 0 W —
  `test_abgemeldeter_speicher_bekommt_auch_keine_ladeleistung` deckt das
  schon, hier nur bestätigen); stale unter der Reserve; Rest unter
  `CONTROL_MIN_SETPOINT_W`.
- `test_abgemeldeter_speicher_bekommt_keine_entladeleistung` bleibt
  unverändert grün (Rest 0 bei saldo 1100 — der Coder rechnet das nach und
  schreibt die Rechnung als Kommentar in den Test).
- Gezählter Wirkungskreis von `stale=True` in den Tests (Literal-Suche, Stand
  HEAD): `tests/test_speicher_abgemeldet.py:65,85,100,119,142-143` und
  `tests/test_coordination.py:342` (`_voll_am_nachmittag`, saldo −4466, Modus
  `laden`, prüft nur die Ladereservierung). Keiner dieser Fälle erzeugt im
  Entlade-Zweig ungedeckten Rest; `test_eingefrorene_leistung_geht_nicht_in_den_regler`
  (Zeile 119) vergleicht `soll_w`, das die Probe nicht anfasst. Färbt die
  Probe einen davon rot, ist die Probe falsch gebaut, nicht der Test.
- Nachsteller: der Invarianz-Test und der Reserve-Test. Der erste wird rot,
  sobald die Probe in die `known`-Zuteilung greift; der zweite, sobald jemand
  die Bedingung auf „am Deckel“ zurückbaut.

### C — Frist-Rechnung entfernen, Lade-Warnung an die physische Schwelle (Reasoning: medium)

Mechanisch, nachdem A steht. Ohne A nicht bauen.

Ändert:

- `actuation.py`: `ladeauftrag_in_frist_erfuellbar` entfernen. Neu
  `SPEICHER_VOLL_SOC = 99.0` mit Kommentar (physisches Ladeende, Zendure
  meldet 100 % faktisch nie, Deckel ist hier ausdrücklich nicht die Grenze)
  und `ladeauftrag_am_ladeschluss(ist_soc: float | None) -> bool`
  (`None` → False: nichts zu rechnen heißt nicht fertig).
- `actuator.py:480-495`: Aufruf ersetzen; `plan.lade_deckel_soc` und
  `laden_statt_einspeisen` werden dort nicht mehr gebraucht. Import in
  `actuator.py:25-31` nachziehen. Docstring-Absatz „Nur beim Laden gilt die
  Ausnahme“ auf die neue Form.
- `tests/test_speicher_selbstsperre.py:195-271`: Die sechs Tests der alten
  Funktion ersetzen — `test_voller_akku_meldet_keinen_ausfall` (99 → True,
  jetzt ohne Zuteilung), `test_halbvoller_akku_muss_weiter_quittieren` (90 →
  False), `test_ohne_soc_bleibt_es_bei_der_quittung`, der AST-Pin
  `"laden_soll and ladeauftrag_am_ladeschluss"`. Die Frist-Pin-Tests
  (`test_kleine_zuteilung_verlaengert_die_erwartung`,
  `test_die_frist_der_ausnahme_ist_die_der_quittung`) entfallen mit der
  Rechnung; ihr Kommentar-Block (195-203) wird zur Begründung der Schwelle
  umgeschrieben, nicht gelöscht.
- Neuer Test: die Szene vom 07.09. (`ist_soc = 99`, Zuteilung 300 W) ist
  „am Ladeschluss“; dieselbe Szene mit `ist_soc = 79` und `lade_deckel_soc =
  80` ist es nicht.
- Nachsteller: der AST-Pin auf die neue Form plus der 79/80-Test.

Tests auf Klassenebene: `pytest tests/test_speicher_selbstsperre.py
tests/test_speicher_quittung.py`.

## Bewusst liegen gelassen

- **Probe im Frühausstieg „alle stale“** (battery.py:192-212). Nach A
  erreicht man diesen Zustand nur noch, wenn alle Speicher einen
  Entlade-Befehl verweigert haben — Totalausfall oder systematischer Fehler
  (z. B. Richtungs-Select auf allen Geräten falsch). Wiedervorlage, wenn
  `abgemeldet` alle Speicher nennt, während `fehler_w > 0` länger als 15 min
  steht.
- **Lade-Probe (Spiegelbild von B).** Deckt den Fall „geräteseitiger
  Mindest-SoC über der HEMS-Reserve“: Die Einheit verweigert das Entladen zu
  Recht, verriegelt, und käme dann auch nicht mehr in die Lade-Zuteilung.
  Das ist ein Konfigurationsfehler, kein Regelfehler; Symptom, auf das man
  achtet: eine verriegelte Einheit nahe der Reserve bei Einspeisung.
- **Backoff für eine dauerhaft tote Einheit unter Probe.** Sie bekommt jeden
  Zyklus den Rest zugeteilt; das kostet nichts (der Strom käme sonst aus dem
  Netz), schreibt aber jeden Zyklus Setpoints an tote Entitäten.
  `_CALL_THROTTLE` fängt identische Werte. Wiedervorlage bei Log-Rauschen.
- **Integration des Probe-Werts über `PlanFlags`.** Nur, wenn die offene
  Messung (Dauer der Probe-Phase) über zehn Minuten liegt.
- **Zweiter Entriegelungs-Eingang „gefolgt“.** Begründung unter Frage 2.
- **`soc_limit`-Entität.** Begründung unter Frage 5.
- **`_speicher_stumm` beim Moduswechsel leeren.** Im Beobachtungsmodus läuft
  keine Quittung, also setzt sich kein neuer Latch; ein bestehender bleibt,
  bis das Gerät meldet oder die Probe ihn löst. Kein Handlungsbedarf.
- **Sensor-Attribut `nicht_uebernommen` und README.** Bleiben, wie sie sind;
  das Attribut bedeutet weiterhin beide Richtungen.

## Review Subtask A (07.09.2026)

**Verdikt: angenommen mit Auflagen.**

Geprüft wurde `git diff` im Arbeitsbaum gegen HEAD (32399a4, sauber, keine
Divergenz zu `origin/main`) für genau die sechs im Auftrag genannten Dateien.
Kein Testlauf gefahren (Weisung); Beurteilung ausschließlich durch Lesen von
Code, Tests und AST-Prüfungen von Hand.

### Kette lückenlos, nur Entladen verriegelt

- `actuator.py:550-554` (`_quittung_speicher`): Append auf
  `plan.speicher_entladen_verweigert` steht unter `if not laden_soll and
  s.name not in plan.speicher_entladen_verweigert`. Die Funktion kehrt weiter
  oben (`if not (laden_soll or entladen_soll) or not s.power_entity: return`)
  aus, wenn keine der beiden Richtungen gilt — an der Stelle des Appends ist
  `not laden_soll` deshalb äquivalent zu `entladen_soll`. Kein Lade-Pfad
  erreicht das Feld.
- `coordinator.py:1134-1137`: `offen` wird ausschließlich aus
  `self.data.plan.speicher_entladen_verweigert` gebildet; kein zweiter
  Schreiber von `offen` im Modul (geprüft per grep über die ganze Datei).
- `coordinator.py:677`: `entladen_verweigert=s.name in offen` — einzige
  Zuführung in `speicher_stumm_latch` (`types.py:373`), das Keyword ist
  konsistent umbenannt (kein verbliebenes `nicht_gefolgt` im Baum).
- `speicher_stumm_latch` (`types.py:363-386`) setzt nur bei
  `schweigt ∧ entladen_verweigert`, löscht nur bei `¬schweigt` — unverändert
  in der Logik, nur im Namen der Bedingung.

Damit ist Frage 1 aus der Entscheidung sauber umgesetzt: Eine
Lade-Verweigerung hat nach diesem Diff keinen Weg mehr in den Latch.

### Zweiter Leser intakt

`plan.speicher_nicht_uebernommen` bleibt unverändert bestehen und wird
weiterhin aus **beiden** Zweigen beschrieben (`actuator.py:548-549`, vor der
neuen bedingten Zeile, ohne `laden_soll`-Gate). Gelesen von `sensor.py:295`
(unverändert) und im Entscheidungs-Log — beides nicht angetastet. Das Feld
wurde nicht verengt und nicht durch das neue Feld ersetzt.

### Befund 15.08. bleibt abgedeckt

Bestätigt an den umbenannten Tests `test_schweigen_und_nichtausfuehrung_verriegeln`,
`test_verriegelung_haelt_ohne_weiteren_befehl`,
`test_eine_frische_meldung_entriegelt_sofort`,
`test_verriegelung_trennt_die_speicher` (`test_speicher_selbstsperre.py:97-150`):
Aussagen unverändert, nur `nicht_gefolgt=True` → `entladen_verweigert=True`.
Ein Speicher, der einem Entlade-Auftrag nicht folgt, verriegelt weiterhin.

### Die neuen Nachsteller pinnen echte Nähte

- `test_offen_kommt_aus_der_entladen_verweigert_liste`
  (`test_speicher_selbstsperre.py:186-198`): Literalsuche auf
  `"self.data.plan.speicher_entladen_verweigert"` im Coordinator-Quelltext.
  Gegen die kaputte Fassung (Feld existiert nicht) wäre die Zeile nicht im
  Quelltext vorhanden — der Test ist dort rot. Schwäche, die die Codebasis
  bereits an der Schwesterstelle `test_offen_kommt_aus_der_quittung_des_actuators`
  (Zeile 162-168, unverändert) hat: reine Substring-Prüfung ohne strukturelle
  Bindung zwischen den beiden Assert-Zeilen. Gleiches, etabliertes Muster,
  keine neue Schwäche.
- `test_nur_der_entlade_zweig_fuettert_den_latch`
  (`test_speicher_selbstsperre.py:201-226`): echter AST-Test. Läuft über
  `_quittung_speicher`, verlangt genau ein `if`, dessen Bedingung
  `"not laden_soll"` enthält und dessen Rumpf einen Append auf
  `speicher_entladen_verweigert` enthält. Gegen die kaputte Fassung (kein
  solcher Append) ist `treffer` leer, `assert len(treffer) == 1` schlägt fehl
  — rot. Der Test fällt auch, wenn jemand den Append ungated oder im
  Lade-Zweig einträgt (Bedingung fehlt „not laden_soll“ bzw. zweiter Treffer).
  Das ist die Naht, an der der Fehler saß, sauber gepinnt.
- `test_quittung_schreibt_die_entlade_verweigerung_getrennt`
  (`test_speicher_quittung.py:238-244`): pinnt nur, dass
  `plan.speicher_entladen_verweigert` irgendwo in `_quittung_speicher`
  vorkommt — kein Gate geprüft, aber laut Entscheidung („bekommt einen
  zweiten [Pin] auf das neue Feld“) genau das verlangte Maß; das Gate prüft
  bereits der AST-Test oben.

Alle drei sind vor dem Umbau nachweislich rot (die Felder/Zeilen existierten
nicht), keiner zeichnet nur die neue Implementierung nach, ohne etwas zu
verlangen.

### Umfang: sauber auf Subtask A begrenzt

`git diff --stat` zeigt ausschließlich die sechs genannten Dateien plus
`TASKS.md` (Aufgabenverwaltung, nicht Teil des Reviews). `sensor.py`,
`actuation.py`, `strategies/battery.py` unverändert (leerer Diff geprüft);
`ladeauftrag_in_frist_erfuellbar` unverändert und weiter aufgerufen
(`actuator.py:501`, `strategies/coordination.py:134`) — Subtask C nicht
angefangen. `tests/test_speicher_abgemeldet.py` unverändert (leerer Diff) —
Subtask B nicht angefangen. Nichts aus A fehlt gegenüber dem „Ändert“-Abschnitt
im Schnitt.

### Auflage (vor Commit zu beheben)

1. **`custom_components/hems/strategies/types.py:626-630`** — der Kommentar
   über `speicher_nicht_uebernommen` ist von diesem Diff nicht angefasst,
   obwohl die Entscheidung ihn unter Frage 1 ausdrücklich auflistet:
   „PlanResult.speicher_nicht_uebernommen (types.py, Kommentar über dem
   Feld)“. Er beschreibt das Feld weiterhin einseitig als „Speicher, die
   zugeteilte **Ladeleistung** nicht ziehen“ und nennt nur das Lade-Szenario
   (Ladebefehl entgegennimmt, Überschuss geht ins Netz) — obwohl das Feld seit
   dem 15.08.-Fix beide Richtungen führt und seit diesem Diff der einzige
   „Sammel“-Leser ist, während `speicher_entladen_verweigert` direkt darunter
   den Latch bedient. Genau die Art von stehengebliebenem Kommentar, vor der
   das Arbeitsmodell warnt. Zu beheben: den Kommentar auf beide Richtungen
   erweitern und den Bezug zum neuen Feld herstellen, analog zum bereits
   korrigierten Docstring in `actuator.py:99-109` bzw. `_quittung_speicher`.
   Reiner Dokumentationsfix, keine Verhaltensänderung — blockiert die
   Umsetzung nicht, muss aber vor dem Commit rein, weil er explizit
   zugesagt war.

## Beobachtungen Coder B

**Dauer der Probe-Phase (offene Messung aus Frage 2):** Aus dem Code ableitbar,
nicht gemessen — ein realer Lauf bleibt die verlässliche Zahl.

- Der Coordinator-Zyklus steht auf 60 s (`coordinator.py:404`,
  `update_interval=timedelta(seconds=60)`).
- `_stumm` entriegelt, sobald `schweigt` auf `False` kippt — unabhängig von
  `SPEICHER_QUITTUNG_FRIST` (die gilt nur für die Quittung/Warnung, nicht für
  den Latch). `schweigt` kommt aus `_abgemeldet(soc_entity, STORAGE_STALE_MIN)`
  und prüft `last_reported`; eine einzelne frische Meldung setzt das sofort
  zurück. Die Entriegelung braucht also nur die ERSTE echte SoC-Änderung nach
  Probe-Beginn, nicht 15 Minuten Stille-Ende.
- Diese erste Änderung ist ein SoC-Tick (bei den Hyper 2000: 37 Wh je 1 % auf
  3,7 kWh). Bei der Probe-Leistung aus der Beispielrechnung der Entscheidung
  (rund 555 W, `f·g/(1+g)` bei g = 0,65) dauert ein Tick rechnerisch rund
  4 Minuten (37 Wh / 555 W). Mit den Zahlen dieses Subtasks
  (`test_szene_070926_verriegelte_probe_deckt_den_rest`: Rest 932 W, L1 bekommt
  greedy den ganzen Rest statt eines Anteils, also näher an voller Rest- als an
  Anteil-Leistung) fiele der erste Tick eher schneller.
- Gesamtdauer bis zur Entriegelung ≈ Zeit bis zum ersten Tick + höchstens ein
  60-s-Zyklus (der Latch braucht einen Durchlauf, um `schweigt=False` zu sehen)
  ≈ 4–5 Minuten bzw. 4–5 Zyklen. Das deckt sich mit der Erwartung „ein bis
  zwei SoC-Ticks" aus der Entscheidung — ein einzelner Tick allein braucht bei
  dieser Leistung schon einen Großteil davon.
- Nicht aus dem Code ableitbar: das reale Meldeintervall der Zendure-
  Integration (push-basiert, Cloud-Anbindung) — die Rechnung oben unterstellt,
  dass ein SoC-Tick gemeldet wird, sobald er auftritt. Liegt die reale
  Meldeverzögerung der Integration signifikant über einem Coordinator-Zyklus,
  verlängert sich die Probe-Phase entsprechend. Das ist die Lücke, die nur ein
  Lauf am echten Gerät schließt.

### Nicht geprüft

- Kein Testlauf (weder einzelne Klassen noch Vollsuite) — das übernimmt der
  Koordinator über den Broker.
- `TASKS.md` und `tasks/speicher-selbstsperre-ladepfad.md` selbst nur
  überflogen, nicht inhaltlich geprüft (Aufgabenverwaltung, laut Auftrag kein
  Review-Gegenstand).
- Laufzeitverhalten (Home-Assistant-nahe Pfade) nicht ausführbar geprüft —
  wie im Projekt üblich nur über die AST-Nähte, siehe oben.
- Subtask B/C nicht bewertet, da nicht Teil dieses Diffs (Abwesenheit nur
  über leere `git diff`-Ausgabe verifiziert, nicht inhaltlich gegen die
  spätere Umsetzung geprüft).

## Review Subtask B (07.09.2026)

**Verdikt: angenommen mit Auflagen.**

Geprüft: `git diff HEAD` für `strategies/battery.py`, `strategies/types.py`,
`sensor.py`, `coordinator.py`, `tests/test_speicher_abgemeldet.py`, plus die
neue, unversionierte `tests/test_speicher_freischwimmen.py` (`git status`:
`??`). HEAD ist `90785a4`, Subtask A bereits committet und nicht erneut
geprüft. `tasks/…md` selbst (Abschnitt „Beobachtungen Coder B“) ist
Aufgabenverwaltung, nicht Review-Gegenstand. Kein Testlauf gefahren (Weisung);
Beurteilung durch Lesen von Code, Kommentaren und Tests, inklusive
Nachrechnen der Beispielzahlen von Hand.

### Invariante hält

`ctrl.zuteilung = _verteile(anteile, soll, laden=False)`
(`battery.py:481`) berechnet die Zuteilung an `known` zuerst, mit derselben
Formel wie vor diesem Diff. Der Probe-Block danach (`battery.py:483-508`)
*hängt* nur neue `StorageSetpoint`-Einträge an dieselbe Liste an — er mutiert
oder ersetzt keinen bestehenden Eintrag. `ctrl.soll_w` wird oben in der
`ControlResult`-Konstruktion (`battery.py:333-339`) gesetzt und danach nicht
mehr angefasst; `bat_ist` (Zeilen 226-228) ist von der Probe unerreichbar.
Kein Pfad, auf dem die Probe `known` etwas wegnimmt oder `soll_w` verschiebt —
bestätigt durch Nachrechnen der Szene 07.09. (`bat_ist=1200`, `soll_wunsch≈
2132`, `soll=1200` gekappt, `rest≈932` → L1 bekommt den Rest, L2 bleibt exakt
bei 1200) und durch `test_zuteilung_bekannter_unveraendert_ueber_saldo_raster`
(`tests/test_speicher_freischwimmen.py:91-115`), das dieselbe Rechnung über
sechs Saldo-Werte automatisiert.

### Bedingung ist das Rest-Kriterium, nicht der Deckel

`rest = soll_wunsch − Σ zuteilung(known)` (`battery.py:494`),
`soll_wunsch` als ungekappte Fassung von `soll` (`battery.py:275-282`,
`max(-max_lad, bat_ist + fehler*gain)` ohne `min(…, max_ent)`) — genau die
von der Entscheidung verlangte Form. Durchgerechnet für
`test_probe_greift_bei_reserve_nicht_bei_deckel`
(`tests/test_speicher_freischwimmen.py:67-85`): L1 (known) steht an der
Reserve, `anteil=0`, bekommt 0 W, zählt aber mit `max_discharge_w=1200` voll
in `max_ent`. Ein Deckel-Kriterium (`Σ zuteilung ≥ max_ent`, also `0 ≥ 1200`)
ließe keine Probe zu; das tatsächlich gebaute Rest-Kriterium
(`rest = soll_wunsch − 0 > 0`) lässt sie zu und L2 bekommt sie. Test bestätigt
das über zwei unabhängige Assertions (`z["L2"] > 0` UND
`probe_namen == ["L2"]"`) — keine reine Attribut-Prüfung.

Die Wallbox-Klauseln „kein Akkustrom ins Auto“ (`battery.py:296-302`,
`330-335`) sind für `soll_wunsch` mitgeführt — beide Stellen aktualisieren
`soll` und `soll_wunsch` mit demselben Deckel-Ausdruck, nur gegen den
jeweils eigenen Vorwert genommen (`min`/`max`). Da `soll ≤ soll_wunsch` als
Invariante durch beide Klauseln erhalten bleibt (beide monoton, gleicher
zweiter Term) und der Probe-Block ausschließlich im Zweig `soll >
CONTROL_DEADBAND_W` läuft, kann die Probe nie ohne die für `soll` bereits
geltende Wallbox-Deckelung feuern. Korrekt umgesetzt — **aber ungetestet**:
Keiner der neuen bzw. geänderten Tests setzt `wallbox_w`. Siehe Auflage 1.

### Geltungsbereich der Probe korrekt eingeschränkt

- Nur Modus `entladen`: Probe-Block liegt vollständig innerhalb
  `if soll > CONTROL_DEADBAND_W:` (`battery.py:478-508`), der Lade-Zweig
  (`elif soll < -CONTROL_DEADBAND_W`) ist unverändert. Bestätigt durch
  `test_keine_probe_im_lademodus`.
- Verriegelte Einheit unter der Reserve: `stale_anteile` verwendet dieselbe
  `anteil`-Formel wie `known` (Reserve-Subtraktion), `_verteile_entladen`
  überspringt `anteil ≤ 0`. Bestätigt durch `test_keine_probe_unter_der_reserve`.
  Nachgerechnet: L1 bekommt trotz auslösender Bedingung (`rest=932≥60`) 0 W,
  weil `anteil=0`.
- `known` deckt die Forderung: `rest` fällt auf 0, Bedingung
  `rest ≥ CONTROL_MIN_SETPOINT_W` greift nicht. Bestätigt durch
  `test_keine_probe_wenn_known_die_forderung_deckt`, nachgerechnet identisch
  zur Rechnung, die jetzt auch als Kommentar in
  `test_abgemeldeter_speicher_bekommt_keine_entladeleistung`
  (`tests/test_speicher_abgemeldet.py:60-66`) steht — Zahlen stimmen
  überein (Rest 0 bei saldo 1100).
- Unter `CONTROL_MIN_SETPOINT_W`: explizite Schranke, nachgerechnet in
  `test_keine_probe_unter_dem_mindest_setpoint` (Rest 32,5 W < 60 W).
- Frühausstieg „alle stale“ (`battery.py:192-212`): im Diff nicht berührt.

Alle vier „keine Probe“-Tests prüfen zusätzlich zur `probe_namen`-Liste auch
`"L1" not in _zuteilung(r)` (Mitgliedschaft im Zuteilungs-Dict) — ein Bug, der
nur die Buchführung in `probe_namen` verfehlt, aber trotzdem Watt zuteilt,
würde auch auffallen.

### Zu den acht Tests, „schwaches Rot“

Der im Auftrag zitierte Coder-Befund („alle acht am fehlenden Attribut
`probe_namen` gescheitert“) trifft nicht auf alle acht zu — nachvollzogen
durch Prüfen der jeweils ERSTEN Assertion, die gegen den Stand vor diesem
Diff (kein `probe_namen`-Feld, keine Probe-Logik) auslösen würde:

- `test_szene_070926_verriegelte_probe_deckt_den_rest` und
  `test_probe_greift_bei_reserve_nicht_bei_deckel`: scheitern zuerst an
  `z["L1"]`/`z["L2"]` mit **KeyError** (die Einheit fehlt im
  Zuteilungs-Dict), nicht am Attribut — sie erreichen die
  `probe_namen`-Zeile gar nicht.
- `test_keine_probe_wenn_known_die_forderung_deckt`,
  `test_keine_probe_im_lademodus`, `test_keine_probe_unter_der_reserve`,
  `test_keine_probe_unter_dem_mindest_setpoint`: scheitern tatsächlich zuerst
  (teils nach einer bereits pass­ierenden Vor-Assertion) an
  `r.regelung.probe_namen` mit **AttributeError**.
- `test_probe_sichtbar_im_sensor`: scheitert an einer reinen
  String-Prüfung (`'"probe"' in sensor.py`), keine Berührung mit
  `probe_namen`. Entspricht dem etablierten Muster
  `test_sensor_und_log_zeigen_den_ausfall`
  (`tests/test_speicher_abgemeldet.py:216-218`,
  `"abgemeldet" in sensor.py`) — keine neue Schwäche.
- `test_zuteilung_bekannter_unveraendert_ueber_saldo_raster`: berührt
  `probe_namen` gar nicht und wäre gegen den Stand vor diesem Diff
  **bereits grün** gewesen (die Invariante — `known` unberührt von stale
  Nachbarn — galt schon vorher, weil `known` stale Speicher immer
  ausgeschlossen hat). Das ist kein Mangel: Der Test ist als
  Regressionsschutz für die Probe gedacht, nicht als Rot-vor-Umbau-Beleg
  (die Entscheidung verlangt das für diesen Test auch nicht), und er würde
  eine künftige Verletzung der Invariante zuverlässig fangen (geprüft durch
  Kopfrechnen: jede Änderung, die `known`-Zuteilung zwischen „mit“ und „ohne“
  stale Nachbarn divergieren ließe, träfe auf einen der sechs Saldo-Werte).

Zusammengefasst: Die pauschale Coder-Aussage ist ungenau, aber das
eigentliche Ergebnis ist besser als sie nahelegt — sechs der acht Tests
prüfen tatsächliches Verhalten (Zuteilungs-Mitgliedschaft, Watt-Werte,
Invarianz über ein Raster), nicht nur Attribut-Existenz. Zwei folgen einem
bereits etablierten, im Projekt akzeptierten String-Pin-Muster.

### Umfang sauber auf Subtask B begrenzt

`actuation.py`, `actuator.py` unverändert; `ladeauftrag_in_frist_erfuellbar`
weiterhin vorhanden und aufgerufen (`actuator.py:501`,
`strategies/coordination.py:134`) — Subtask C nicht angefangen. Aus B
vorhanden: `ControlResult.probe_namen` (`types.py:130-142`), Sensor-Attribut
`probe` (`sensor.py:282-286`), Docstring-Nachzug in `HemsCoordinator._stumm`
(`coordinator.py:668-673`). Nichts davon fehlt gegenüber dem
„Ändert“-Abschnitt im Schnitt.

### Kommentare wahr

`battery.py:275-282` (Begründung `soll_wunsch`), `battery.py:483-494`
(Begründung Rest- vs. Deckel-Kriterium), `types.py:130-141`
(`probe_namen`-Kommentar), `sensor.py:282-286`, `coordinator.py:668-673`
— alle gegen den tatsächlichen Code geprüft, keine Abweichung gefunden.

### Auflage (vor Commit zu beheben)

1. **Fehlende Regressionsprüfung für die Wallbox-Klausel in `soll_wunsch`**
   (`custom_components/hems/strategies/battery.py:296-302, 330-335`; Tests:
   `tests/test_speicher_freischwimmen.py`,
   `tests/test_speicher_abgemeldet.py`). Kein Test in beiden Dateien setzt
   `wallbox_w` (geprüft per Volltextsuche — kein Treffer). Die Entscheidung
   nennt genau diesen Punkt ausdrücklich als Stelle, an der man es sonst
   falsch baut („damit ‚kein Akkustrom ins Auto‘ auch für die Probe gilt“).
   Der Code setzt es korrekt um (nachgerechnet: beide Klauseln aktualisieren
   `soll` und `soll_wunsch` mit demselben Deckel-Ausdruck), aber ohne
   Nachsteller kann ein künftiger Umbau genau diese Kopplung lösen, ohne dass
   ein Test es bemerkt — die Art Lücke, gegen die dieses Projekt laut eigener
   Historie („vierter Fund derselben Ursache“) besonders empfindlich ist. Zu
   ergänzen: ein Test mit aktivem `wallbox_w` (und `battery_to_ev=False`),
   der zeigt, dass die Probe im Entlade-Zweig nicht mehr Rest beansprucht,
   als nach Abzug der Wallbox-Last verbleibt (Spiegelung von
   `test_gegenstueck_kein_akkustrom_ins_auto` o. ä., falls ein solcher Test
   für `soll` bereits existiert — sonst neu). Reine Testergänzung, keine
   Code-Änderung nötig.

### Nicht geprüft

- Kein Testlauf (weder einzelne Klassen noch Vollsuite) — Weisung, übernimmt
  der Koordinator über den Broker.
- Subtask A (bereits committet) nicht erneut geprüft.
- `tasks/…md`-Abschnitt „Beobachtungen Coder B“ nur überflogen, nicht
  inhaltlich geprüft (Aufgabenverwaltung, kein Review-Gegenstand laut
  Auftrag).
- Laufzeitverhalten (Home-Assistant-nahe Pfade, `_stumm`/Coordinator-Zyklus)
  nicht ausführbar geprüft — wie im Projekt üblich nur durch Lesen.
- Ob im Projekt bereits ein existierender Test die Wallbox-Klausel für
  `soll` (nicht `soll_wunsch`) abdeckt, habe ich nicht verifiziert (wäre die
  Vorlage für die in Auflage 1 verlangte Ergänzung) — nur per Volltextsuche
  in den beiden hier geänderten Testdateien geprüft, nicht im gesamten
  Testbaum.
