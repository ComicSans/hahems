# HEMS - Konzept (v0.3, Stand 2026-07-19)

Home Energy Management System als Home-Assistant-Custom-Integration.
Ziel: Autarkie zuerst. Netz ist Rückfallebene, nie Optimierungsziel.

## Ausgangslage

- **PV**: 3 Prognose-Flächen (Ost, Süd, West) via Forecast-Integration, stündlich + 7 Tage voraus
- **Speicher**: 3x Zendure Hyper 2000 (je Phase, gesamt 11,52 kWh), Manager-Integration
- **Wärmepumpe**: LG Luft-Wasser, climate-Entität + Power-Switch, getrennte Sensorik Heizen/Warmwasser
- **Wallbox**: nur Ampere-Steuerung, fix 3-phasig (min. ~4,1 kW), kein SoC-Zugriff aufs Auto
- **Zähler**: Hauptzähler-Leistung (OBIS 16.7.0) als einzige Regelgröße.
  Ein separater Einspeise-Fehlleistungssensor wird bewusst komplett ignoriert.
- **Strompreis**: fest (input_number), kein dynamischer Tarif geplant

## Entscheidungen

1. **Eigenes Plugin** (Custom Integration, HACS-fähig), kein EMHASS
2. **Heuristik-Planner** statt Linearprogrammierung: bei festem Preis ist die Zielfunktion
   simpel (Netzbezug minimieren), eine Heuristik bleibt erklärbar und debugbar
3. **Dynamische Priorisierung nach Prognose**, Warmwasser immer Priorität 1
4. **WP-Steuerung über vorhandene HA-Entitäten** (kein SG-Ready)
5. **Alles variabel**: geräte-agnostisches Rollenmodell, keine Entity-IDs im Code,
   Geräte jederzeit über die UI hinzufügbar/entfernbar

## Rollenmodell

Der Planner kennt keine Hersteller, nur abstrakte Rollen:

| Rolle | Beispiel | Parameter |
|---|---|---|
| Meter (genau 1) | Zähler 16.7.0 | Entity, Vorzeichen-Invertierung |
| ForecastSource (0..n) | PV-Fläche Ost/Süd/West | Energie heute/Rest/morgen, Leistung jetzt |
| Storage (0..n) | Hyper 2000 | SoC, Kapazität, Reserve-SoC, max. Lade-/Entladeleistung, Sollwert-Entitäten für Laden/Einspeisen |
| ThermalStore (0..n) | Warmwasserspeicher | Temperatur, Basis-Soll, Komfort-Soll |
| SwitchableLoad (0..n) | Wärmepumpe | Switch, Taktschutz (min. Lauf/Pause, max. Sperre/Tag) |
| ModulatedLoad (0..n) | Wallbox | Ampere-Entity, min/max A, Phasen, optionaler Schalter, min. Laufzeit |

Storages werden zu einem virtuellen Gesamtspeicher aggregiert; Sollwerte werden
proportional zu freier Kapazität/Leistung verteilt. Reserven (z.B. L3) bleiben
Parameter des einzelnen Geräts. Ein vierter Akku = neue Storage-Instanz, fertig.

## Architektur (vier Module)

1. **Forecast-Fusion**: aggregiert alle ForecastSources, rechnet gegen ein
   gelerntes Lastprofil. Primärquelle ist der rekonstruierte Hausverbrauch
   (`lastfluss` = PV + Netzsaldo + Akku, PV- und akkukompensiert), aus dem ein
   volles 24-h-Profil je Wochentagstyp (Werktag/Wochenende, UTC-Stunde, 28 Tage
   Langzeitstatistik) gebildet wird. Fällt die Historie noch aus, greift das
   Nacht-Profil aus dem rohen Zähler (14 Tage, nur Nachtstunden), zuletzt die
   konfigurierte Grundlast. Liefert u.a. PV-Rest heute, PV morgen,
   Überschuss-Prognose, Nachtdefizit, Sonnenfenster.
2. **Planner**: rollierender Plan in 15-min-Slots (24-48 h), prognosebasierte
   Heuristik. Pro Slot: WW-Basis → Hausverbrauch → (WW-Komfort | Akku | Auto in
   prognoseabhängiger Reihenfolge) → Einspeisung als Rest.
3. **Executor + Safety** (ab Phase 2): Service-Calls für Zendure/WP/Wallbox.
   Safety-Layer ist nicht überstimmbar (siehe unten).
4. **Simulation/Sizing** (Phase 4): Service `hems.simulate` rechnet die
   Vergangenheit mit virtuellen Akkugrößen durch (Autarkiegrad,
   Eigenverbrauchsquote, vermiedener Netzbezug in kWh/€).

## Regeln im Detail

### Warmwasser (Priorität 1, Zwei-Sollwert-Strategie)
- Basis-Soll 48 °C: wird immer gehalten, notfalls mit Netzstrom (Safety-Layer)
- Komfort-Soll 60 °C: nur bei vorhandenem oder prognostiziertem Überschuss.
  Der WW-Speicher ist damit der billigste "Akku" im System (~12 °C Hub thermisch)
- Legionellenprogramm bleibt unangetastet

### Zendure-Akkus
- Normalbetrieb: Laden ausschließlich aus PV-Überschuss
- Ziel-SoC = Nachtdefizit-Prognose (gedeckelt auf 100 %)
- Netzladen nur als Notreserve-Ausnahme: SoC unter Reserve-Schwelle und keine
  nennenswerte PV in Sicht (konfigurierbar, standardmäßig konservativ niedrig)

### Wallbox
- Modus **PV-Überschuss** (Standard): HEMS besitzt den Ladestrom selbst. Der
  Sollstrom folgt dem Überschuss **vor dem Akku** (aus dem Saldo werden aktuelle
  Wallbox-Last und Akkuleistung herausgerechnet). Dadurch weicht die Wallbox als
  modulierbare Last **vor** der Akku-Entladung: lässt der Ertrag nach, wird zuerst
  der Ladestrom bis zum Minimum heruntergeregelt, erst danach hilft der Akku;
  reicht es nicht mehr für die Mindestleistung, schaltet die Wallbox ab. Freigabe
  erst ab stabil ~4,3 kW Überschuss (3-phasig, 6 A Minimum), symmetrisches
  Hysterese-Band + Mindestlaufzeit gegen Schützflattern
- Modus **Sofortladen** (Notfall-Override): volle Ampere, Netz egal. Die
  Wallbox-Last wird dabei aus dem Speicher-Saldo herausgerechnet (Akku schonen).
  Setzt sich nach Ladeende selbst auf Standard zurück
- **Mehrere modulierbare Lasten:** Der Überschuss wird über alle Lasten verteilt.
  Reicht er für alle Minima, laufen alle und der Rest wird proportional zum
  Schwankungsbereich aufgeteilt (alle anteilig gedrosselt statt eine ganz).
  Reicht er nicht, entscheidet Priorität grob (höhere zuerst) und darunter
  Energie-Fairness: die heute am wenigsten geladene Last kommt zuerst, mit
  Rotation im Takt der Mindestlaufzeit. Eine angesteckt-lose Wallbox (an, zieht
  ~0) wird erkannt und tritt zurück, damit sie keine ladende verdrängt; sie wird
  nur einmal je Cooldown (`EV_EMPTY_COOLDOWN_S`, Standard 30 min) kurz geprüft,
  um ein zwischenzeitlich angestecktes Auto zu entdecken. Wer eine dauerhaft
  leere Zweitbox hat, gibt dem Auto besser eine höhere Priorität (dann keine
  Prüf-Pausen)
- **Wichtig:** Die bisherige externe Überschuss-Ladeautomation muss deaktiviert
  werden — sonst regeln beide gegeneinander (HEMS drosselt, Automation rampt hoch)

### Wärmepumpe (Winterlogik)
- Pausenfenster um den PV-Peak nur an ertragsschwachen Tagen, wenn der Überschuss
  sonst nicht in die Akkus passt
- Ehrliche COP-Abwägung als transparente Kennzahl (tagsüber ist der COP besser als
  nachts; die Pause lohnt nur, wenn das Gebäude die Tageswärme nicht speichern kann)
- Taktschutz: Mindestlaufzeit, Mindestpause, max. Sperrdauer/Tag, Untergrenze Innentemperatur

### Safety-Layer (nicht überstimmbar)
- WW-Mindesttemperatur, Legionellenprogramm
- WP-Taktschutz und Komfortgrenzen
- Akku-Mindest-/Reserve-SoC pro Gerät
- Watchdog: bei Planner-Ausfall Rückfall in den heutigen Zustand; die bestehende
  Zendure-Saldo-Automation bleibt als Fallback erhalten und wird nur bei aktivem
  HEMS pausiert

## Phasenplan

1. **Beobachten** (dieses Repo, jetzt): Forecast-Fusion + Prognose-Sensoren,
   Planner loggt nur, fasst nichts an. 2-3 Wochen Validierung.
   Dazu der Sensor **Einspeiseplan**: verteilt das Akku-Budget (verfügbar minus
   Reserve) als stündliche Einspeise-Obergrenzen über die Nacht ("gleichmäßig
   strecken", damit der Akku bis Sonnenaufgang reicht); live soll die
   Einspeisung später saldo-geführt darunter bleiben. Die Stundenwerte kommen
   aus dem gelernten Lastprofil (24 h je Wochentagstyp aus dem rekonstruierten
   Hausverbrauch, Nacht-Zählerprofil als Fallback bis genug Historie da ist),
   die zugehörige **hems-plan-card** zeigt Plan, SoC-Verlauf und die
   PV-Stundenkurve für heute und morgen.
   Nebenbei: Vorzeichenverhalten der 16.7.0 gegen Historie klären
   (Verdacht: bei 0 gedeckelt, dann Saldo-Rekonstruktion über Einspeise-Sensor).
2. **WW + Akku steuern**: Zwei-Sollwert-WW, Zendure-Zielladung
3. **Wallbox + WP-Winterlogik**: Überschussladen, Sofort-Override, Pausenfenster
4. **Simulation**: `hems.simulate` für Akku-Sizing und What-if-Reports

## Offene Punkte

- Lizenz wählen (Repo ist inzwischen öffentlich)
- 16.7.0-Vorzeichenfrage (wird in Phase 1 mit echten Daten beantwortet)
