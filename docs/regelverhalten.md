# Regelverhalten

Wie HEMS zu seinen Empfehlungen kommt. Wer nur einrichten will, braucht das
nicht — die Standardwerte funktionieren ohne diese Seite.

Zurück zur [Übersicht](../README.md).

## Warmwasser

Der Sollwert folgt der Priorität **Legionellenschutz > PV-Boost > Basis**.

- **Basis** — wird immer gehalten, notfalls aus dem Netz.
- **PV-Boost** — Aufheizen auf den Komfort-Sollwert nur, wenn der Speicher
  fast voll ist **und** kräftig eingespeist wird. Beide Schwellen (Speicher-SoC
  und Netzsaldo) haben je ein Ein- und ein Aus-Niveau.
- **Legionellenschutz** — wöchentliches Fenster, in dem der Sollwert
  unabhängig vom Überschuss angehoben wird (Standard 60 °C). Hygiene geht vor.
- **Sperrzeit** — in diesem Fenster wird weder Basis- noch Komfortladung
  empfohlen; `sensor.hems_warmwasser_soll` wird `unbekannt`, Status `aus`. Der
  Speicher darf bis zum Ende der Sperre unter die Basistemperatur auskühlen,
  statt aus dem Netz nachzuheizen.

Ohne gesetztes Sperrfenster hält der Auto-Modus das Warmwasser rund um die Uhr
auf Basistemperatur. Das Fenster ist eine feste Uhrzeit und kann eine
saisonale Tag/Nacht-Umschaltung nur annähern.

## Speicher: Saldo-Regelung

Aus Netzsaldo und gemessener Speicherleistung berechnet der Planner eine
Regel-Empfehlung je Speicher (`sensor.hems_speicher_regelung`):
Proportionalregler mit Priorität „Bezug minimieren“ — schnell gegen teuren
Netzbezug, gemächlich beim Laden, Sollwert leicht in die Einspeisung
verschoben, Totband gegen Dauerkorrekturen.

- **Laden** verteilt parallel proportional zur freien Kapazität. Mehrere Akkus
  laden gleichzeitig (niedrigere C-Rate je Akku, SoC-Ausgleich) — außer der
  Überschuss reicht nur für wenige Einheiten über dem Mindest-Setpoint, dann
  werden die leersten zuerst bedient.
- **Entladen** wird greedy zugeteilt: ein Akku zur Zeit, mit Auswahl-Hysterese
  gegen Umschaltverschleiß.
- Speicher ohne SoC-Wert fallen aus der Zuteilung.

Ein als **Kaltreserve** markierter Speicher entlädt erst mit, wenn der mittlere
SoC der übrigen unter 40 % fällt, und scheidet oberhalb von 45 % wieder aus.
Geladen wird er immer mit.

### Ladedeckel über den Tag (Akku-Schonung)

Kalendarische Alterung ist bei hohem SoC am größten. Deshalb begrenzt ein
zeitabhängiger Ladedeckel die Live-Ladung: tagsüber nur bis
`STORAGE_DAY_HOLD_SOC` (Standard 78 %), erst in den letzten
`STORAGE_FULL_CHARGE_LEAD_H` Stunden (Standard 3 h) vor Sonnenuntergang steigt
er per Rampe auf 100 %. Der Speicher ist damit etwa zum Sonnenuntergang voll
und verbringt möglichst wenig Zeit bei 100 %. Der Deckel begrenzt nur das
Laden — liegt der SoC schon darüber, wird nicht zwangsentladen.

Der Deckel fällt sofort weg, sobald Nachtdeckung vor Schonung geht:

- das Optimierungsziel verlangt Vollladung (`nulleinspeisung`, `vollladen`),
- morgen wird es knapp (`morgen_knapp`), oder
- der erwartete Restertrag heute reicht nicht mehr, um den tatsächlich
  fehlenden Rest bis 100 % nachzuladen.

Der fehlende Rest bemisst sich am wirklichen Speicherstand, nicht an einer
festen Annahme ab dem Halte-Niveau. Der aktuelle Deckel steht als
`lade_deckel_soc` im Plan und begrenzt auch die SoC-Prognose der Plan-Karte.

### Optimierungsziel

`select.hems_optimierungsziel` steuert zur Laufzeit, worauf die Speicher-Regelung
optimiert. Das Ziel ist unabhängig vom Prioritätsmodus aus der Einrichtung, der
nur die Reihenfolge der Überschussverteilung bestimmt. Es wird als Attribut
`ziel` an `sensor.hems_empfehlung` gespiegelt.

- **eigenverbrauch** (Standard) — Bezug minimieren, der Regel-Rest wird bewusst
  leicht in die Einspeisung geschoben; der Akku wird nur bis zur Nachtdeckung
  geladen (voll nur bei schlechter Prognose für morgen).
- **nulleinspeisung** — echter Zero-Export. Der Regler hält das Netz auf einem
  kleinen Bezug (~100 W) statt auf leichter Einspeisung: gegen realen Export
  wird geladen, ein kleiner Restbezug wird toleriert, am Nullpunkt bleibt der
  Regler stehen (kein Zwangsbezug). Zusätzlich wird der Akku voll geladen.
  Physikalische Grenze: ist der Akku voll und die PV liefert weiter mehr als das
  Haus braucht, lässt sich Einspeisung ohne PV-Abregelung nicht vermeiden — die
  stellt diese Integration nicht.
- **vollladen** — hält das Ladeziel dauerhaft auf 100 %, sonst wie
  eigenverbrauch. Die manuelle Variante der automatischen
  Schlechtwetter-Vollladung.

## Ladevorrang Akku ↔ Wallbox

Bei PV-Überschuss teilt HEMS die Ladehoheit nach dem Prioritätsmodus auf:

- **ev_first** — die Wallbox bedient sich zuerst, der Akku bekommt den Rest.
- **battery_first** — der Akku hat Vorrang auf den Überschuss **oberhalb des
  Wallbox-Minimums**. Ein bereits ladendes Auto behält sein Minimum und wird nie
  abgeregelt; zusätzlicher Überschuss geht zuerst in den Akku. Ist der Akku am
  Tagesdeckel, reserviert er nichts mehr.
- **auto** — bei knappem Tagesertrag wie battery_first, sonst wie ev_first.

Der reservierte Überschuss wird dem Lasten-Regler vorenthalten; den Rest holt
sich die Speicher-Regelung über ihr normales Saldo-Residuum
(`strategies/coordination.py`).

## Schaltbare Lasten

Schaltbare Lasten schaltet HEMS überschussgesteuert: ein, solange der Überschuss
ihre **erwartete Leistung** deckt, aus, wenn er fehlt. Beliebig viele Lasten
sind möglich; jede hat eigene Zeiten, eigene Priorität und eigene gelernte
Leistung.

Reicht der Überschuss nicht für alle:

1. **Modulierbare Lasten drosseln herunter** — sie geben ihr Headroom auf,
   behalten aber ihr Minimum. Sie sind der elastische Puffer.
2. **Schaltbare Lasten** werden abgeschaltet, die mit der niedrigsten Priorität
   zuerst.
3. Der **Akku pausiert** zuletzt — er lädt weiter, solange gedrosselt oder
   abgeschaltet werden kann.

Anti-Takt: `min_on` hält eine Last an, `min_off` hält sie aus, `max_block`
erzwingt ein Einschalten, wenn HEMS sie zu lange ausgehalten hat
(`strategies/switchable.py`).

### Wie die erwartete Leistung gelernt wird

Die erwartete Leistung wird je Last aus ihrer Leistungs-Entität gelernt und über
Neustarts hinweg persistiert. Gelernt wird nicht jeder Messwert, sondern nach
drei Regeln:

- **Anlaufkarenz (5 min)** — direkt nach dem Einschalten ist der Verbraucher
  noch nicht auf Leistung. Die Karenz läuft nach einem HA-Neustart neu an.
- **Boden** — unterhalb 20 W gilt eine Last als „an, aber zieht nichts“.
  Heizungsgekoppelte Lasten haben einen eigenen Boden von 500 W: bei einer
  Wärmepumpe ziehen Regelung, Umwälzpumpe und Ventile ein paar hundert Watt,
  lange bevor der Kompressor auf Leistung ist.
- **Asymmetrie** — nach oben sofort, nach unten nur zu 25 % pro Messung. Eine
  unterschätzte Last wird zu früh eingeschaltet und provoziert Netzbezug; eine
  Teillastphase soll den gelernten Wert deshalb nicht nach unten ziehen.

### Heizungsgekoppelt

Nur Lasten, deren Verbrauch der Außentemperatur folgt (Wärmepumpe, Heizstab),
fließen in das Heizgradstunden-Modell für die Bedarfsprognose ein und werden aus
dem gelernten Lastprofil herausgerechnet. Eine überschussgesteuerte Last (Pool,
Luftentfeuchter) hat keinen Temperaturbezug — sie würde die Regression verzerren
(zu hohe Basisleistung, überschätztes Nachtdefizit) und bleibt deshalb im
normalen Lastprofil.

## Heizkreis

Die Modus-Empfehlung kommt aus der Außentemperatur (heizen unter / aus über bzw.
kühlen über / aus unter, jeweils mit Hysterese), dazu ein witterungsgeführter
Vorlauf-Sollwert. Die Heizkurve (Fußpunkt bei 0 °C, Steigung, Min/Max) ist
konfigurierbar; eine optionale Wärmeanforderungs-Entität hebt die Kurve um bis
zu 5 K an — ohne Anforderung fällt der Vorlauf auf das Minimum (Absenkbetrieb).
In den Sperrmonaten (Standard Mai–September) wird Heizen nie empfohlen, außer
der Frostschutz greift. Bei niedrigem Vorlauf-Soll meldet das Attribut
`leise_empfohlen`, dass der Flüsterbetrieb der Anlage reicht.

### Was `vorlauf_ziel_c` wirklich führt

HEMS schreibt den Sollwert in die Größe, auf die die Anlage geregelt wird — und
welche das ist, steht am Bedienteil, nicht in HEMS. Regelt die Anlage auf den
Rücklauf, führt das Attribut `vorlauf_ziel_c` von `sensor.hems_heizkreis` den
Rücklauf-Soll, und der Vorlauf liegt im Kühlbetrieb deutlich darunter. Der Name
bleibt trotzdem, weil eine Umbenennung Lovelace-Karten leert, ohne dass eine
unavailable gewordene Entität davon erzählt.

Im Kühlbetrieb rechnet HEMS ohnehin keine Kurve: Es reicht den konfigurierten
festen Sollwert durch, Fußpunkt, Steigung und Grenzen gelten nur fürs Heizen.

Wer auf Rücklauf regelt und Flächenkühlung hat, sollte den Vorlauf im Auge
behalten: Am 30.07.2026 lag er bei einem Rücklauf-Soll von 21 °C siebzehn
Minuten unter dem Raumtaupunkt von 13,3 °C, mit einem Minimum von 11,4 °C in
der Volllastphase am Taktende. Eine Taupunkt-Untergrenze kennt HEMS nicht.

### Taktschutz: Zwangspause bei zu vielen Verdichterstarts

Ist die optionale Rückmeldung „Verdichter läuft“ konfiguriert, zählt HEMS deren
Einschaltflanken in einem **rollierenden** Fenster: gespeichert werden die
Startzeitpunkte, gezählt wird, wie viele davon jünger als die Fensterlänge sind.
Reißt die Zahl die Schwelle, empfiehlt HEMS für die eingestellte Pausendauer
„aus“; die Attribute `taktschutz`, `taktschutz_bis` und `verdichterstarts` von
`sensor.hems_heizkreis` machen das sichtbar. Danach läuft der Heizkreis
mindestens eine Viertelstunde frei, bevor die nächste Pause greifen darf — sonst
sperrt HEMS den Betrieb dauerhaft aus.

Das Fenster rolliert, weil ein Fenster fester Lage die Pause zu spät einlegt:
Häufungen links und rechts seiner Grenze zählen nie zusammen. Am 31.07.2026
liefen so 44 Minuten mit fünf Starts, bevor die Pause griff.

Was das leistet und was nicht: Es begrenzt die **Startrate**, nicht die Länge
des einzelnen Takts. Aus den drei bis vier Minuten Pause, die sich eine Anlage
über ihre eigene Wiederanlaufsperre gönnt, wird eine halbe Stunde. Kurzzyklen
entstehen, wenn die kleinste Leistung der Anlage über der Restlast liegt — das
ändert keine Regel in HEMS, dagegen helfen mehr Durchfluss, mehr Wasserinhalt
oder ein anderer Sollwert.

Heizen und Kühlen: Beide takten, in beiden greift die Pause. Ausgenommen ist der
Frostschutz — dort geht es um Umwälzung gegen einfrierende Leitungen, und dafür
ist eine halbe Stunde Zwangspause der falsche Preis. Die Starts einer
Warmwasserladung zählen nicht mit: die gehören dem Speicher.

Zwei Grenzen: Läuft gerade eine Warmwasserladung, stellt HEMS am Heizkreis
nichts (siehe unten) — die Pause wird dann erst nach der Ladung geschrieben und,
wenn sie noch währenddessen abläuft, gar nicht. Der Heizkreis taktet in diesem
Fenster ohnehin nicht. Und der Zählerstand lebt nur im Speicher: Nach einem
Neustart von Home Assistant beginnt das Fenster bei null, eine laufende Pause
ist weg.

Warum sich das lohnt: Auf einer Anlage, deren kleinste Leistung über der
Restlast liegt, kostet jeder Kurztakt Effizienz. Gemessen am 30.07.2026 lagen
Takte von drei bis fünf Minuten bei etwa der Hälfte der Arbeitszahl langer
Läufe.

### Während der Warmwasserbereitung stellt HEMS nichts

Ist die optionale Rückmeldung „Warmwasserbereitung“ konfiguriert und an, lässt
HEMS den Heizkreis in Ruhe: weder Modus noch Vorlauf-Soll werden geschrieben.
Grund ist nicht Vorsicht, sondern Zwecklosigkeit: Der Warmwasserspeicher hat
seinen eigenen Sollwert, und die Anlage hebt den Vorlauf-Soll für die Ladung
selbst an. Führt HEMS nach, schreiben beide in jedem Zyklus gegeneinander.

Die Empfehlung bleibt in diesem Fenster sichtbar stehen, das Attribut
`warmwasserbereitung_aktiv` von `sensor.hems_heizkreis` ist dann `true`.
Empfehlung und Ist dürfen also auseinanderlaufen; das ist kein Fehlzustand.
Flüsterschalter und Saison-Select laufen weiter, sie kollidieren mit der Ladung
nicht. Ohne konfigurierte Rückmeldung ändert sich nichts am bisherigen
Verhalten.

### Wärmepumpe in der Bedarfsprognose

Ist ein Heizkreis konfiguriert und hat die Wärmepumpe als schaltbare Last eine
Leistungs-Entität, lernt HEMS ein temperaturabhängiges Verbrauchsmodell aus
45 Tagen Langzeitstatistik:

```
P = Basis + k × (Heizgrenze − Außentemperatur)
```

Die Basis ist die mittlere WP-Leistung oberhalb der Heizgrenze (Warmwasser,
Standby), `k` die gelernte Steigung in W/K, gedeckelt auf die historisch
beobachtete Spitzenleistung. Solange die Historie nicht reicht, überbrückt ein
Richtwert von 40 W/K (Attribut `quelle: richtwert` statt `gelernt`).

Das Lastprofil wird dann WP-bereinigt gelernt und die Wärmepumpe stattdessen
explizit je Stunde aufgeschlagen — mit der Temperatur aus der stündlichen
Wettervorhersage, ersatzweise der aktuellen Außentemperatur. Damit reagieren
Nachtdefizit, Ziel-SoC, Entladeplan und SoC-Prognose sofort auf Kälteeinbrüche,
statt dem 28-Tage-Mittel hinterherzulaufen. Während der Sommersperre zählt nur
die Basisleistung.

Transparenz: `sensor.hems_nachtdefizit` weist den WP-Anteil als
`wp_anteil_kwh` aus, `sensor.hems_heizkreis` das gelernte Modell unter
`verbrauchsmodell`. Ohne Heizkreis oder ohne Leistungs-Entität bleibt die
Wärmepumpe implizit im Lastprofil.

## E-Auto

### Mindestladeleistung der Wallbox

Die Empfehlung „E-Auto mit Überschuss“ prüft, ob der Momentanüberschuss die
physikalische Mindestladeleistung der modulierbaren Last erreicht
(`min_a × Phasen × 230 V`) — darunter könnte die Wallbox den Überschuss real gar
nicht abnehmen. Die Ein-Schwelle liegt mit 200 W Sicherheitsmarge darüber, die
Aus-Schwelle am nackten Minimum, damit die Empfehlung nicht bei jedem
Wolkenschatten kippt. Ohne konfigurierte modulierbare Last genügt jeder
Überschuss über 200 W.

### Zwangsladung

`switch.hems_e_auto_zwangsladung` erzwingt die Ladeempfehlung. Der Zustand wird
als Attribut `ev_zwang` an `sensor.hems_empfehlung` gespiegelt.

**Der Zwang garantiert, _dass_ geladen wird — nicht, _wie schnell_.** Jede
modulierbare Last läuft: Sie fällt nicht durch das Schmitt-Band, die Rotation
zwischen gleichrangigen Lasten oder die Mindestpause. Ihr Sollstrom folgt aber
weiterhin dem Überschuss und sinkt bei Defizit bis auf `min_a`, statt volle
Ampere aus dem Netz zu ziehen. Bei reichlich Überschuss geht sie bis `max_a`
hoch. Ohne Saldo- oder Leistungsmessung gibt es keinen Überschuss zu verteilen:
dann volle Ampere (Fail-safe „jetzt laden“).

Wie ohne Zwang startet eine ausgeschaltete Wallbox erst am Minimum und bekommt
mehr, sobald sie im Folgezyklus echte Nachfrage nachweist — eine Wallbox ohne
angestecktes Auto zieht sonst nur eine Phantomlast durch die Bilanz.

Damit der Hausakku nicht still ins Auto leerläuft, rechnet die Saldo-Regelung
die aktuelle Wallbox-Leistung aus dem Saldo heraus, den sie ausregelt: Der Akku
hält seinen SoC, das Zwangs-Delta kommt aus dem Netz. Liefert die PV gerade
Überschuss, lädt der Akku daraus wie gewohnt weiter — er wird nur nicht
zusätzlich für die Wallbox entladen.

## Update-Takt und Sprung-Erkennung

Der Coordinator rechnet regulär alle 60 s neu. Springt der Netzsaldo zwischen
zwei Messpunkten um mehr als 800 W (Wolkenkante, große Last an/aus), löst das
sofort eine zusätzliche Neuberechnung aus, statt bis zum nächsten Takt zu
warten. Ein Cooldown von 20 s verhindert Update-Stürme bei einer Serie kleiner
Sprünge.

Das bleibt eine Sprungerkennung, keine Prognose: ein sehr kurzer Ausschlag
zwischen zwei Messpunkten des Zählers selbst bleibt unsichtbar.
