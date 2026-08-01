"""Heizkreis-Domäne: witterungsgeführte Modus- und Vorlaufempfehlung."""
from __future__ import annotations

from datetime import timedelta
from math import ceil, log

from ..const import (
    ANTITAKT_RELEASE_MIN,
    ANTITAKT_ZEITEN_MAX,
    DEWPOINT_RELEASE_K,
    HEATING_COLD_THRESHOLD_C,
    HEATING_DEMAND_SHIFT_K,
    MAGNUS_A,
    MAGNUS_B,
    SILENT_VLT_OFF_C,
    SILENT_VLT_ON_C,
)
from .kurve import kurven_wahl
from .types import HeatingResult, PlanInput, PlanResult, _latch


def _heating_plan(inp: PlanInput, res: PlanResult) -> HeatingResult:
    """Heizkreis: Modus über Außentemperatur-Hysterese, Vorlauf aus der Kurve.

    Heizen unterliegt der Sommersperre; Kühlen greift oberhalb der eigenen
    Schwellen. Im Heizbetrieb hebt die Wärmeanforderung der Räume die
    witterungsgeführte Kurve an; ohne Anforderung fällt der Vorlauf auf das
    Minimum (Absenkbetrieb). Der Vorlauf bleibt zwischen Minimum und Maximum.

    Frostschutz übersteuert die Sommersperre: Fällt die Außentemperatur unter
    die Frostschwelle (mit eigener Hysterese), wird Heizen erzwungen, damit der
    Heizkreis in den Sperrmonaten bei Spätfrost nicht einfriert. Der Vorlauf
    bleibt dabei auf dem Minimum — Ziel ist Umwälzung, nicht Komfort.

    Die Warmwasser-Rückmeldung (optionale Rolle) wird nur durchgereicht: die
    Empfehlung bleibt, was sie ist, gestellt wird in diesem Fenster nichts;
    das entscheidet ``plan_heating_control``.

    Im Kühlbetrieb hält die Taupunkt-Untergrenze den Vorlauf über dem Taupunkt
    der Raumluft, sofern beide Raumklima-Rollen konfiguriert sind.

    Welche Heizkurve dabei gilt — die konfigurierte oder die aus der
    Wärmepumpen-Analyse übernommene — entscheidet `strategies/kurve.py`. Hier
    wird nur mit dem Ergebnis gerechnet.
    """
    h = inp.heating
    result = HeatingResult(
        name=h.name, sommer_sperre=h.heat_locked, ww_bereitung=h.dhw_active
    )
    kurve = kurven_wahl(inp, res)
    result.kurve_quelle = kurve.quelle
    result.kurve_grund = kurve.grund
    result.kurve_fusspunkt_c = kurve.fusspunkt_c
    result.kurve_steilheit = kurve.steilheit
    taupunkt = _taupunkt_c(h.room_temp_c, h.room_humidity_pct)
    result.taupunkt_c = None if taupunkt is None else round(taupunkt, 1)
    # Außerhalb des Kühlbetriebs wacht die Grenze nicht: dort wird der Vorlauf
    # nach oben geführt, nicht nach unten. Der Latch startet damit jedes Mal
    # sauber, wenn der Kühlbetrieb wieder aufgenommen wird.
    res.flags.waermepumpe_taupunkt = False
    t = h.outdoor_temp_c
    if t is None:
        result.modus = "unbekannt"
        _taktschutz(inp, res, result)
        return result
    result.t_aussen_c = t

    res.flags.waermepumpe_heizen = (
        False
        if h.heat_locked
        else _latch(inp.flags.waermepumpe_heizen, t, on=h.heat_on_c, off=h.heat_off_c)
    )
    res.flags.waermepumpe_kuehlen = _latch(
        inp.flags.waermepumpe_kuehlen, t, on=h.cool_on_c, off=h.cool_off_c
    )
    # Frostschutz-Latch unabhängig vom Sperr-Zustand: greift auch, wenn die
    # Sommersperre waermepumpe_heizen hart auf False zwingt.
    res.flags.waermepumpe_frost = _latch(
        inp.flags.waermepumpe_frost, t, on=h.frost_on_c, off=h.frost_off_c
    )

    if res.flags.waermepumpe_heizen or res.flags.waermepumpe_frost:
        # Frostschutz erzwingt Heizen nur, wenn der reguläre Heizbetrieb (evtl.
        # per Sommersperre) aus ist; sonst heizt die Anlage witterungsgeführt.
        frost_only = res.flags.waermepumpe_frost and not res.flags.waermepumpe_heizen
        result.modus = "heizen"
        result.frostschutz = frost_only
        vlt_min = (
            h.vlt_min_cold_c if t < HEATING_COLD_THRESHOLD_C else kurve.vorlauf_min_c
        )
        if frost_only or (h.demand_pct is not None and h.demand_pct < 1):
            vlt = vlt_min
        else:
            vlt = kurve.fusspunkt_c - t * kurve.steilheit
            if h.demand_pct is not None:
                vlt += h.demand_pct / 100 * HEATING_DEMAND_SHIFT_K
            vlt = max(vlt_min, min(vlt, h.vlt_max_c))
        result.vlt_ziel_c = float(round(vlt))
        res.flags.waermepumpe_leise = _latch(
            inp.flags.waermepumpe_leise,
            result.vlt_ziel_c,
            on=SILENT_VLT_ON_C,
            off=SILENT_VLT_OFF_C,
        )
        result.leise_empfohlen = res.flags.waermepumpe_leise
    elif res.flags.waermepumpe_kuehlen:
        result.modus = "kuehlen"
        result.vlt_ziel_c = _taupunkt_grenze(inp, res, result, taupunkt)
    else:
        result.modus = "aus"
    _taktschutz(inp, res, result)
    return result


def _taupunkt_c(temp_c: float | None, humidity_pct: float | None) -> float | None:
    """Taupunkt der Raumluft nach Magnus, oder None ohne brauchbare Messwerte.

    Beides wird gebraucht, Temperatur und relative Feuchte — fehlt eines, gibt
    es keinen Taupunkt und die Untergrenze bleibt aus. Feuchtewerte außerhalb
    von 0 bis 100 % sind Messfehler; 0 % ist zusätzlich der Pol des Logarithmus
    und damit rechnerisch unbrauchbar.
    """
    if temp_c is None or humidity_pct is None:
        return None
    if not 0 < humidity_pct <= 100:
        return None
    gamma = log(humidity_pct / 100) + MAGNUS_A * temp_c / (MAGNUS_B + temp_c)
    return MAGNUS_B * gamma / (MAGNUS_A - gamma)


def _taupunkt_grenze(
    inp: PlanInput, res: PlanResult, result: HeatingResult, taupunkt: float | None
) -> float:
    """Kühl-Vorlauf über dem Taupunkt halten (optionale Raumklima-Rollen).

    An einer Flächenkühlung schlägt sich Wasser nieder, sobald die Oberfläche
    den Taupunkt der Raumluft unterschreitet. Gemessen am 30.07.2026: 17 Minuten
    Vorlauf unter dem Raumtaupunkt, Minimum 11,4 °C bei einem Taupunkt von
    13,3 °C. Die Grenze hebt deshalb den Kühl-Sollwert an, statt ihn zu
    unterschreiten — sie senkt ihn nie.

    Der Sicherheitsabstand ist konfigurierbar, weil die Vorlauftemperatur nicht
    die Oberflächentemperatur ist: Estrich und Putz liegen dazwischen, die
    Oberfläche bleibt wärmer als das Wasser. Eine Grenze exakt auf dem Taupunkt
    wäre zu scharf, gar keine zu spät.

    Zwei Schwellen, wie bei jeder Ja/Nein-Entscheidung hier: Angehoben wird,
    sobald die Untergrenze über dem Kühl-Sollwert liegt; losgelassen erst,
    wenn sie `DEWPOINT_RELEASE_K` darunter gefallen ist. Ohne die zweite
    Schwelle würde der geschriebene Sollwert um die Grenze herum zwischen zwei
    Werten springen, sobald die Raumfeuchte ein wenig schwankt.
    """
    h = inp.heating
    soll = h.cool_vlt_c
    if taupunkt is None:
        return soll
    # Aufgerundet auf ganze Grad: die Aktuierung vergleicht und schreibt auf
    # ganze Grad, eine Untergrenze mit Nachkommastellen erzeugte Schreibverkehr,
    # den die Anlage gar nicht auflöst. Aufgerundet und nicht kaufmännisch,
    # weil ein halbes Grad zu warm hier billiger ist als ein halbes Grad zu
    # kalt — das eine kostet Kälteleistung, das andere setzt Wasser an.
    grenze = float(ceil(taupunkt + h.dewpoint_margin_k))
    result.taupunkt_grenze_c = grenze
    res.flags.waermepumpe_taupunkt = _latch(
        inp.flags.waermepumpe_taupunkt,
        grenze - soll,
        on=0.5,
        off=-DEWPOINT_RELEASE_K,
    )
    if not res.flags.waermepumpe_taupunkt:
        return soll
    vlt = max(soll, grenze)
    result.taupunkt_begrenzt = vlt > soll
    return vlt


def _taktschutz(inp: PlanInput, res: PlanResult, result: HeatingResult) -> None:
    """Zwangspause gegen zu häufige Verdichterstarts (Heizen und Kühlen).

    Gezählt werden die Einschaltflanken der Rolle „Verdichter läuft" in einem
    rollierenden Fenster: gespeichert werden die Startzeitpunkte selbst, und
    gezählt wird, wie viele davon jünger als die Fensterlänge sind. In einem
    Fenster fester Lage zählen Häufungen links und rechts der Grenze nie
    zusammen, die Pause kommt entsprechend spät — gemessen am 31.07.2026:
    44 Minuten Takten mit fünf Starts, bevor sie griff.

    Reißt die Zahl die Schwelle, empfiehlt HEMS für die Pausendauer „aus"; die
    Anlage bekommt damit eine lange Ruhephase statt der vier Minuten, die ihre
    eigene Wiederanlaufsperre hergibt. Das senkt die STARTRATE — der einzelne
    Takt wird davon nicht länger.

    Zwei Schwellen, wie bei jeder Ja/Nein-Entscheidung hier: die Startzahl legt
    die Pause ein, `ANTITAKT_RELEASE_MIN` hält sie danach für eine Weile fern.
    Ohne diese zweite Schwelle könnte HEMS den Betrieb dauerhaft aussperren.

    Heizen und Kühlen takten beide, deshalb gilt die Pause für beide. Nicht
    aber im Frostschutz: dort geht es um Umwälzung gegen einfrierende Leitungen,
    und dafür ist eine halbe Stunde Zwangspause der falsche Preis.

    Während einer Warmwasserladung werden keine Starts gezählt: die gehören dem
    Speicher, nicht dem Heizkreis, und würden den Taktschutz grundlos auslösen.
    """
    h = inp.heating
    prev, now = inp.flags, inp.now
    fenster = timedelta(minutes=h.antitakt_window_min)

    # Verdichterzustand fortschreiben. Ohne Rolle (oder bei nicht erreichbarer
    # Entität) hält der letzte bekannte Zustand: eine Lücke im Signal darf
    # keine Flanke erfinden, wenn der Wert zurückkommt.
    an = h.compressor_on
    res.flags.takt_verdichter_an = prev.takt_verdichter_an if an is None else an
    start = an is True and not prev.takt_verdichter_an and not h.dhw_active

    # Alte Startzeiten fällt das Fenster im selben Schritt heraus, in dem der
    # neue Start dazukommt — daher rollierend statt gekippt.
    zeiten = tuple(t for t in prev.takt_start_zeiten if now - t < fenster)
    if start:
        zeiten = (*zeiten, now)
    # Die Obergrenze wirft die ältesten Einträge weg — also genau die, an denen
    # die Schwelle hängt. Sie muss deshalb deutlich über dem größten
    # einstellbaren `antitakt_starts` (20) liegen.
    zeiten = zeiten[-ANTITAKT_ZEITEN_MAX:]
    starts = len(zeiten)

    # Im Frostschutz wird nicht pausiert: das Ziel ist dort Umwälzung, nicht
    # Komfort, und eine eingefrorene Leitung wäre teurer als jedes Takten.
    # `schuetzbar` entscheidet beides — ob eine Pause beginnen darf und ob eine
    # laufende die Empfehlung übersteuert. Eine Pause, die gerade nichts
    # unterdrückt, wird auch nicht als aktiv gemeldet; sie läuft in den Flags
    # weiter und greift wieder, sobald der Betrieb zurückkommt.
    schuetzbar = result.modus in ("kuehlen", "heizen") and not result.frostschutz

    pause_bis, frei_seit = prev.takt_pause_bis, prev.takt_frei_seit
    if pause_bis is not None and now >= pause_bis:
        # Pause abgelaufen: freigeben und mit einem frischen Fenster weiterzählen.
        pause_bis, frei_seit = None, now
        zeiten, starts = (), 0
    elif (
        pause_bis is None
        and schuetzbar
        and h.antitakt_starts > 0
        and starts >= h.antitakt_starts
        and (
            frei_seit is None
            or now - frei_seit >= timedelta(minutes=ANTITAKT_RELEASE_MIN)
        )
    ):
        pause_bis = now + timedelta(minutes=h.antitakt_pause_min)

    res.flags.takt_start_zeiten = zeiten
    res.flags.takt_pause_bis = pause_bis
    res.flags.takt_frei_seit = frei_seit

    result.verdichterstarts = starts
    # Die Pause übersteuert nur die Ausgabe. Die Latches laufen weiter mit der
    # Außentemperatur mit — würden sie zurückgesetzt, finge die Hysterese nach
    # jeder Pause von vorn an und HEMS bekäme sein eigenes Takten.
    if pause_bis is not None and schuetzbar:
        result.modus = "aus"
        result.vlt_ziel_c = None
        # Ohne Vorlauf-Soll gibt es nichts zu begrenzen; sonst stünde in der
        # Anzeige eine Anhebung, die zu keinem Sollwert gehört. Der Latch läuft
        # in den Flags weiter, damit die Grenze nach der Pause nicht von vorn
        # anfängt.
        result.taupunkt_begrenzt = False
        result.taktschutz = True
        result.taktschutz_bis = pause_bis
