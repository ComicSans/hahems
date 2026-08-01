"""Die Ausgaberollen der Wärmepumpen-Analyse als Entities.

Jede Rolle ist ein eigener Zustand mit Einheit und Zustandsklasse. Tragende
Werte stehen bewusst nie in Attributen: Attribute sind nicht in der Registry
verankert und brechen still — eine Karte mit `state_attr(...)` wird schlicht
leer, ohne Fehler.

Die Rollennamen hier sind eine öffentliche Schnittstelle. Sie bilden über
`f"{entry_id}_{rolle_id}_{key}"` die Kennung in der Entity-Registry, an der
Automationen und Dashboards hängen. `docs/waermepumpen-analyse.md` führt sie
auf, und `tests/waermepumpe/test_rollen.py` hält Code und Dokument zusammen.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntityDescription,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)

from .analysis.types import Analyse

DATENBASIS_STUFEN = ["keine_daten", "unzureichend", "vorlaeufig", "belastbar"]

VERWERFUNGSGRUENDE = [
    "ok",
    "spreizung_zu_klein",
    "keine_leistung",
    "kein_durchfluss",
    "abtauen",
    "warmwasser",
    "unplausibel",
]


@dataclass(frozen=True, kw_only=True)
class AnalyseSensorDescription(SensorEntityDescription):
    """Beschreibung samt Zugriff auf die Analyse."""

    wert: Callable[[Analyse], float | str | None]


@dataclass(frozen=True, kw_only=True)
class AnalyseHinweisDescription(BinarySensorEntityDescription):
    """Ein Hinweis mit Hysterese, als eigene Entity."""

    wert: Callable[[Analyse], bool]


SENSOREN: tuple[AnalyseSensorDescription, ...] = (
    AnalyseSensorDescription(
        key="cop_momentan",
        name="COP momentan",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        wert=lambda a: a.cop_momentan,
    ),
    AnalyseSensorDescription(
        key="cop_soll",
        name="COP Soll",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        wert=lambda a: a.cop_soll,
    ),
    AnalyseSensorDescription(
        key="cop_soll_unsicherheit",
        name="COP-Soll Unsicherheit",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.cop_soll_unsicherheit,
    ),
    AnalyseSensorDescription(
        key="cop_abweichung",
        name="COP-Abweichung",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.cop_abweichung,
    ),
    AnalyseSensorDescription(
        key="waermeleistung",
        name="Wärmeleistung",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        wert=lambda a: a.waermeleistung_w,
    ),
    AnalyseSensorDescription(
        key="spreizung",
        name="Spreizung",
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        wert=lambda a: a.spreizung_k,
    ),
    # Zielwerte zu den Spreizungshinweisen. Beide beziehen sich auf den
    # Volumenstrom und nicht auf die Pumpenstufe — eine Umwälzpumpe fördert
    # nicht linear zu ihrer Prozentanzeige, und ihre Kennlinie ist hier nicht
    # bekannt. "Volumenstrom auf 80 %" ist eine Zielgröße, "Pumpe auf Stufe
    # 80 %" wäre geraten.
    AnalyseSensorDescription(
        key="durchfluss_ziel_prozent",
        name="Durchfluss-Ziel",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        wert=lambda a: a.durchfluss_ziel_prozent,
    ),
    AnalyseSensorDescription(
        key="durchfluss_abweichung_prozent",
        name="Durchfluss-Abweichung",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        wert=lambda a: a.durchfluss_abweichung_prozent,
    ),
    AnalyseSensorDescription(
        key="waermeverlust_koeffizient",
        name="Wärmeverlustkoeffizient",
        native_unit_of_measurement="W/K",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.waermeverlust_w_pro_k,
    ),
    # Zähler: monoton wachsend. Das Stundenmittel einer Startzahl ist
    # bedeutungslos — Aussagen über einen Zeitraum entstehen aus der Differenz
    # zweier Zählerstände.
    AnalyseSensorDescription(
        key="takte",
        name="Verdichterstarts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        wert=lambda a: a.takt.starts,
    ),
    AnalyseSensorDescription(
        key="laufzeit_summe",
        name="Verdichterlaufzeit",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        wert=lambda a: round(a.takt.laufzeit_s / 3600.0, 3),
    ),
    AnalyseSensorDescription(
        key="laufzeit_mittel",
        name="Mittlere Taktlänge",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.laufzeit_mittel_min,
    ),
    # Empfehlungen zur Heizkurve. Sie werden veröffentlicht, nicht
    # geschrieben: die Übernahme ist eine Einstellung am Heizkreis.
    AnalyseSensorDescription(
        key="empfehlung_fusspunkt",
        name="Empfehlung Fußpunkt",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.kurve.fusspunkt_c,
    ),
    AnalyseSensorDescription(
        key="empfehlung_steilheit",
        name="Empfehlung Steilheit",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        wert=lambda a: a.kurve.steilheit,
    ),
    AnalyseSensorDescription(
        key="empfehlung_vorlauf_min",
        name="Empfehlung Vorlauf-Minimum",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        wert=lambda a: a.kurve.vorlauf_min_c,
    ),
    # Datenbasis zweimal, weil zwei verschiedene Dinge gemeint sind: wie
    # sauber gerade gemessen wird, und wie lange schon beobachtet wurde. In
    # einen Wert zusammengeworfen sähe ein tadellos gemessener COP wochenlang
    # wertlos aus, nur weil die Historie für eine Kurvenempfehlung nicht
    # reicht.
    AnalyseSensorDescription(
        key="datenbasis",
        name="Datenbasis",
        device_class=SensorDeviceClass.ENUM,
        options=DATENBASIS_STUFEN,
        wert=lambda a: a.datenbasis,
    ),
    AnalyseSensorDescription(
        key="datenbasis_empfehlung",
        name="Datenbasis Empfehlung",
        device_class=SensorDeviceClass.ENUM,
        options=DATENBASIS_STUFEN,
        wert=lambda a: a.datenbasis_empfehlung,
    ),
    AnalyseSensorDescription(
        key="verwerfungsgrund",
        name="Verwerfungsgrund",
        device_class=SensorDeviceClass.ENUM,
        options=VERWERFUNGSGRUENDE,
        wert=lambda a: a.verwerfungsgrund,
    ),
)

# Hinweise als eigene Binärsensoren, nicht als Liste in einem Attribut: nur so
# bleiben sie in der Registry verankert und in Automationen adressierbar.
# Jeder hat Ein- und Ausschaltschwelle, nie eine einzelne, und wird über Tage
# gemittelt statt je Zyklus ausgewertet.
HINWEISE: tuple[AnalyseHinweisDescription, ...] = (
    AnalyseHinweisDescription(
        key="hinweis_spreizung_niedrig",
        name="Hinweis Spreizung niedrig",
        wert=lambda a: a.hinweise.spreizung_niedrig,
    ),
    AnalyseHinweisDescription(
        key="hinweis_spreizung_hoch",
        name="Hinweis Spreizung hoch",
        wert=lambda a: a.hinweise.spreizung_hoch,
    ),
    AnalyseHinweisDescription(
        key="hinweis_taktung_hoch",
        name="Hinweis Taktung hoch",
        wert=lambda a: a.hinweise.taktung_hoch,
    ),
    AnalyseHinweisDescription(
        key="hinweis_vorlauf_zu_hoch",
        name="Hinweis Vorlauf zu hoch",
        wert=lambda a: a.hinweise.vorlauf_zu_hoch,
    ),
    AnalyseHinweisDescription(
        key="hinweis_effizienz_unter_erwartung",
        name="Hinweis Effizienz unter Erwartung",
        wert=lambda a: a.hinweise.effizienz_unter_erwartung,
    ),
    # Kein Anlagenproblem, sondern ein Messproblem: Vor- und Rücklauf melden
    # denselben Wert, obwohl der Verdichter läuft. Dann stimmt die
    # Registerzuordnung oder die Verdrahtung nicht, und die Spreizung, an der
    # fast alles hängt, ist strukturell null.
    AnalyseHinweisDescription(
        key="hinweis_temperaturen_identisch",
        name="Hinweis Temperaturen identisch",
        wert=lambda a: a.hinweise.temperaturen_identisch,
    ),
    AnalyseHinweisDescription(
        key="durchfluss_geschaetzt",
        name="Durchfluss geschätzt",
        wert=lambda a: a.durchfluss_geschaetzt,
    ),
)

# Wärmemenge ist keine Description: sie wird über die Zeit integriert und
# braucht dafür eigenen Zustand. Siehe `sensor.WaermemengeSensor`.
ENERGIE_KEY = "waermemenge"

#: Alle Rollennamen, unter denen die Analyse veröffentlicht.
ROLLEN: tuple[str, ...] = (
    tuple(d.key for d in SENSOREN) + (ENERGIE_KEY,) + tuple(d.key for d in HINWEISE)
)
