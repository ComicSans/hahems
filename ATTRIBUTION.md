# Herkunft der Daten

## Kennlinien der Presets

Die COP-Polynome in `custom_components/wp_optimization/presets/` stammen aus
der Parameterdatenbank von **hplib** des Forschungszentrums Jülich
(FZJ-IEK3-VSA), die ihrerseits aus den öffentlichen Datensätzen der
europäischen **Keymark**-Zertifizierung abgeleitet ist.

- hplib: <https://github.com/FZJ-IEK3-VSA/hplib> — MIT-Lizenz
- Zugrundeliegende Keymark-Daten: **CC BY 4.0**
  (<https://creativecommons.org/licenses/by/4.0/>)

Die Kennwerte wurden aus der Datenbank ausgelesen und als eigene
Preset-Dateien abgelegt. hplib ist **keine Laufzeitabhängigkeit** dieser
Integration: das COP-Polynom hat vier Koeffizienten, die Auswertung ist eine
Zeile Rechnung. Eine Bibliothek mit pandas und scikit-learn im Rücken auf
einem Raspberry Pi zu installieren, nur um vier Zahlen zu multiplizieren,
wäre nicht angemessen.

Die Polynomform wurde gegen den Quelltext von hplib geprüft:

```
cop = p1 · t_aussen + p2 · t_vorlauf + p3 + p4 · t_aussen
```

Bei Luft-Wasser-Geräten ist die Quellentemperatur gleich der
Außentemperatur — deshalb heben sich `p1` und `p4` dort weitgehend auf.

### Abweichung von hplib

hplib rekonstruiert die Vorlauftemperatur als Rücklauf plus 5 K. Diese
Integration setzt stattdessen die **gemessene** Vorlauftemperatur ein — genau
jene 5-K-Annahme ist es ja, die die Spreizungsdiagnose überprüfen soll.

### Modellfehler

Die Presets führen den mittleren absoluten prozentualen Fehler der Kennlinie
mit (`cop_modellfehler_prozent`). Er liegt je nach Modell im Bereich von 8 bis
17 Prozent und wird als eigene Größe veröffentlicht, damit aus dem Vergleich
mit dem Datenblatt keine Scheingenauigkeit wird.

Die sechs generischen Typen tragen keinen gemessenen Fehler — sie *sind* der
Fit. Ihnen ist ein bewusst konservativer Wert von 25 Prozent zugeordnet, der
eine Annahme ist und kein Messwert.

## Methodische Anregungen

Verfahren, die in vergleichbaren Projekten vorkommen und hier eigenständig
umgesetzt sind: Wärmeverlustbestimmung per linearer Regression,
Kniepunkterkennung und die vierstufige Qualitätskennzeichnung von Messwerten.
Ideen sind frei; übernommener Quelltext ist in diesem Repo keiner enthalten.
