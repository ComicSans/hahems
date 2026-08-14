"""Ladestrategie über den Tag: Nacht-Ziel, Just-in-time-Rampe, Mittagspause.

Charakterisiert `_ladeplan` / `_lade_deckel_soc` / `_ist_ladepause`
(custom_components/hems/strategies/battery.py), deren Ergebnis in
`compute_plan.lade_deckel_soc` / `.lade_ziel_soc` / `.lade_start` / `.lade_pause`
landet, und ihre Wirkung auf Ladezuteilung und Vorrang.

Der Deckel ist die geplante Absicht, nicht der Befehl: Läuft gerade Überschuss
auf, den sonst niemand nimmt, lädt die Regelung über ihn hinaus und meldet das
als `regelung.laden_statt_einspeisen`. Tests, die die geplante Kurve prüfen,
arbeiten deshalb mit `saldo_w=0` — ohne Überschuss gibt es nichts zu überfahren.

Alle Zeiten im Test sind UTC; `lokal()` rechnet die gemeinte Uhrzeit um.
"""
from __future__ import annotations

from datetime import timedelta

from factories import (
    DAY,
    NEXT_SUNRISE,
    SUNSET,
    UTC_OFFSET_H,
    load,
    lokal,
    plan_input,
    storages,
    zuteilung,
)
from hems import planner as P
from hems.const import (
    GOAL_FULL_CHARGE,
    PRIORITY_BATTERY_FIRST,
    PRIORITY_EV_FIRST,
    STORAGE_FULL_BY_LEAD_H,
    STORAGE_NIGHT_MARGIN_SOC,
)
from hems.strategies import coordination


def _plan(**kw):
    """Planlauf ohne Überschuss — zeigt den GEPLANTEN Deckel."""
    kw.setdefault("saldo_w", 0.0)
    kw.setdefault("now", lokal(9))
    return P.compute_plan(plan_input(**kw))


def _lokal_h(t) -> float:
    """Lokale Dezimalstunde eines UTC-Zeitpunkts (für lesbare Vergleiche)."""
    lok = t + timedelta(hours=UTC_OFFSET_H)
    return lok.hour + lok.minute / 60


# --- Nacht-Ziel statt pauschal 100 % -----------------------------------------


def test_ziel_ist_nachtbedarf_plus_marge():
    r = _plan(socs=[60, 60, 60])
    # Nachtdefizit + Reserve ergibt das Nacht-Ziel, die Marge kommt obendrauf.
    assert r.lade_ziel_soc == round(r.speicher_ziel_soc + STORAGE_NIGHT_MARGIN_SOC, 1)
    # Und das liegt im Sommer deutlich unter 100 % — genau der Punkt der Übung.
    assert r.lade_ziel_soc < 95


def test_langer_nacht_bedarf_hebt_das_ziel():
    # Doppelte Nachtlast => mehr Nachtdefizit => höheres Ziel, gedeckelt bei 100.
    wenig = _plan(socs=[60, 60, 60], night_load_w=400.0).lade_ziel_soc
    viel = _plan(socs=[60, 60, 60], night_load_w=800.0).lade_ziel_soc
    assert wenig < viel <= 100.0


def test_ziel_vollladen_hebt_auf_100():
    r = _plan(socs=[60, 60, 60], goal=GOAL_FULL_CHARGE)
    assert r.lade_ziel_soc == 100.0
    assert r.lade_start is None  # sofort, keine Rampe
    assert r.lade_deckel_soc == 100.0


# --- Just-in-time-Rampe -------------------------------------------------------


def test_vormittags_haelt_der_deckel_den_stand():
    # Die Rampe steht noch bevor: der Deckel deckelt auf den aktuellen Stand,
    # der Akku drängelt sich nicht vor die Lasten.
    r = _plan(socs=[60, 60, 60])
    assert r.lade_start is not None and r.lade_start > lokal(9)
    assert r.lade_deckel_soc == r.speicher_soc


def test_vormittags_bekommt_das_auto_den_ueberschuss():
    # Der Kern der Strategie: battery_first ist eingestellt, aber die Rampe
    # läuft noch nicht — der Akku reserviert nichts und überlässt dem Auto den
    # Vormittag. Er nimmt nur, was übrig bleibt (statt es einzuspeisen).
    wb = load("WB", power_w=0.0, ist_an=False, nachfrage=True)
    inp = plan_input(
        now=lokal(9),
        socs=[60, 60, 60],
        saldo_w=-6000,
        modulateds=[wb],
        wallbox_w=0.0,
        priority_mode=PRIORITY_BATTERY_FIRST,
    )
    res = P.compute_plan(inp)
    assert res.lade_start is not None and res.lade_start > inp.now
    assert coordination.akku_ladereservierung(inp, res) == 0.0
    assert res.ev_regelung.soll_summe_w > 0


def test_leerer_speicher_startet_frueher():
    frueh = _plan(socs=[30, 30, 30]).lade_start
    spaet = _plan(socs=[60, 60, 60]).lade_start
    assert frueh is not None and spaet is not None
    assert frueh < spaet


def test_rampe_endet_vor_sonnenuntergang():
    # Ende der Rampe: der Deckel steht spätestens STORAGE_FULL_BY_LEAD_H vor
    # Sonnenuntergang auf dem Ziel.
    ende = SUNSET - timedelta(hours=STORAGE_FULL_BY_LEAD_H)
    r = _plan(now=ende, socs=[60, 60, 60])
    assert r.lade_deckel_soc == r.lade_ziel_soc
    assert r.lade_start is None  # läuft bereits, nichts mehr anzukündigen


def test_waehrend_der_rampe_steigt_der_deckel_ueber_den_stand():
    r = _plan(now=lokal(19), socs=[40, 40, 40])
    assert r.speicher_soc < r.lade_deckel_soc < r.lade_ziel_soc


def test_deckel_nie_unter_dem_aktuellen_stand():
    # Speicher weit über dem Nachtbedarf: der Deckel hält ihn, statt das Gerät
    # mit einem Ziel-SoC unter dem Ist-Wert zum Leermachen aufzufordern.
    r = _plan(socs=[95, 95, 95])
    assert r.lade_ziel_soc < 95
    assert r.lade_deckel_soc == r.speicher_soc


def test_heute_knapp_laedt_sofort():
    # Wenig PV-Rest, hohe Grundlast: der Resttag reicht nicht einmal mehr fürs
    # Nacht-Ziel — dann wird nicht mehr gewartet.
    r = _plan(socs=[30, 30, 30], pv_remaining_kwh=1.0, baseline_load_w=1500.0)
    assert r.lade_start is None
    assert r.lade_deckel_soc == r.lade_ziel_soc


def test_nachts_laedt_sofort():
    day = DAY.replace(hour=1)  # 01:00 UTC, vor Sonnenaufgang
    r = P.compute_plan(
        plan_input(
            now=day,
            socs=[60, 60, 60],
            saldo_w=0.0,
            next_sunrise=day.replace(hour=5),
            sunrise=day.replace(hour=5),
            sunset=day.replace(hour=19),
        )
    )
    assert r.lade_start is None
    # Kein Warten mehr — der Deckel steht auf dem Ziel bzw. auf dem Stand, wenn
    # der Speicher den Rest der Nacht ohnehin schon deckt.
    assert r.lade_deckel_soc == max(r.speicher_soc, r.lade_ziel_soc)


def test_prognose_folgt_der_rampe():
    # Vor dem Rampenstart bleibt die SoC-Prognose auf dem Stand stehen, obwohl
    # die Sonne scheint — der Überschuss gehört bis dahin den Lasten.
    r = _plan(socs=[50, 50, 50], saldo_w=-3000.0)
    start = r.lade_start
    assert start is not None
    vor = [pt.soc for pt in r.soc_prognose if lokal(9) < pt.zeit < start]
    assert vor and all(s <= r.speicher_soc + 0.6 for s in vor)


# --- Bevor eingespeist wird, wird geladen ------------------------------------


def test_am_deckel_ohne_andere_abnehmer_laedt_trotzdem():
    # Der Deckel hält den Stand, es gibt aber keine Last, die den Überschuss
    # nimmt — dann geht er in den Akku statt ins Netz.
    r = P.compute_plan(plan_input(now=lokal(9), socs=[60, 60, 60], saldo_w=-1500))
    assert r.regelung.modus == "laden"
    assert r.regelung.laden_statt_einspeisen
    assert sum(zuteilung(r).values()) > 0
    # Der geplante Deckel bleibt stehen — überfahren wird er, nicht verschoben.
    assert r.lade_deckel_soc == r.speicher_soc


def test_gemischte_staende_nutzen_auch_den_gedeckelten_speicher():
    # L1 steht über dem Deckel, L2 darunter — aber L2 allein kann den Überschuss
    # nicht aufnehmen (Ladegrenze 1200 W je Speicher). Der Rest ginge ins Netz,
    # also lädt auch L1 mit.
    r = P.compute_plan(
        plan_input(now=lokal(9), storage_states=storages([96, 50]), saldo_w=-6000)
    )
    z = zuteilung(r)
    assert r.regelung.laden_statt_einspeisen
    assert z["L1"] > 0 and z["L2"] > 0


def test_ungleiche_staende_lassen_den_deckel_stehen_statt_ihn_zu_ueberfahren():
    # Der Deckel liegt vormittags auf dem MITTLEREN Stand. Streuen die Speicher
    # um diesen Mittelwert, hat der schwächste noch einen Spalt freie Kapazität
    # — und weil die Zuteilung proportional zur freien Kapazität verteilt und
    # nur an der Ladegrenze (W) deckelt, nimmt dieser eine Spalt rechnerisch
    # den GANZEN Überschuss auf. `laden_statt_einspeisen` bleibt damit aus:
    # gestellt wurde ja alles.
    #
    # Genau dieser Zustand lief am 14.08.2026 26 Minuten lang: L1 bekam die
    # volle Leistung zugeteilt, der Ziel-SoC am Gerät stand auf dem Deckel und
    # damit auf dem Ist-SoC, und das Gerät hielt sich für fertig. Dass daraus
    # heute trotzdem Ladung wird, hängt allein am Kopfraum in `plan_soc_set`
    # (siehe tests/test_speicher_quittung.py) — der Plan überfährt den Deckel
    # hier bewusst NICHT.
    r = P.compute_plan(
        plan_input(
            now=lokal(8),
            storage_states=storages([26.0, 26.3, 26.6]),
            saldo_w=-1200,
        )
    )
    z = zuteilung(r)
    assert r.regelung.modus == "laden"
    assert not r.regelung.laden_statt_einspeisen
    # Alles auf dem einen Speicher, der unter dem Deckel steht.
    assert z["L1"] > 0 and z["L2"] == 0 and z["L3"] == 0
    assert r.lade_deckel_soc == r.speicher_soc


def test_gleiche_staende_am_deckel_ueberfahren_ihn():
    # Die Gegenprobe zum Test darüber: Stehen alle auf dem Deckel, ist nirgends
    # mehr ein Spalt — dann greift „lieber laden als einspeisen".
    r = P.compute_plan(
        plan_input(
            now=lokal(8),
            storage_states=storages([26.3, 26.3, 26.3]),
            saldo_w=-1200,
        )
    )
    assert r.regelung.laden_statt_einspeisen
    assert all(w > 0 for w in zuteilung(r).values())


def test_volle_speicher_laden_nicht_weiter():
    r = P.compute_plan(plan_input(now=lokal(9), socs=[100, 100, 100], saldo_w=-1500))
    assert not r.regelung.laden_statt_einspeisen
    assert sum(zuteilung(r).values()) == 0


# --- Mittags-Ladepause --------------------------------------------------------


def test_ladepause_nur_zwischen_elf_und_vierzehn():
    def pause(stunde: int) -> bool:
        return _plan(now=lokal(stunde), socs=[60, 60, 60]).lade_pause

    assert not pause(10)
    assert pause(11)
    assert pause(13)
    assert not pause(14)
    assert not pause(15)


def test_ladepause_reserviert_keinen_ueberschuss_fuer_den_akku():
    inp = plan_input(
        now=lokal(13),
        socs=[60, 60, 60],
        saldo_w=-1500,
        modulateds=[load()],
        priority_mode=PRIORITY_BATTERY_FIRST,
    )
    res = P.compute_plan(inp)
    assert res.lade_pause
    assert coordination.akku_ladereservierung(inp, res) == 0.0


def test_ausserhalb_der_pause_reserviert_der_akku_wieder():
    # Nachmittags, mit laufender Rampe: der Deckel liegt über dem Stand, also
    # reserviert der Akku wieder Ladeleistung vor der Wallbox.
    inp = plan_input(
        now=lokal(19),
        socs=[40, 40, 40],
        saldo_w=-1500,
        modulateds=[load()],
        priority_mode=PRIORITY_BATTERY_FIRST,
    )
    res = P.compute_plan(inp)
    assert not res.lade_pause
    assert coordination.akku_ladereservierung(inp, res) > 0


def test_ladepause_faellt_weg_wenn_voll_geladen_werden_muss():
    assert not _plan(now=lokal(13), socs=[60, 60, 60], goal=GOAL_FULL_CHARGE).lade_pause


def test_ladepause_laedt_weiter_wenn_sonst_eingespeist_wuerde():
    # In der Pause tritt der Akku nur im VORRANG zurück. Bleibt Überschuss
    # übrig, lädt er ihn trotzdem — die Zuteilung steht.
    r = P.compute_plan(plan_input(now=lokal(13), socs=[60, 60, 60], saldo_w=-1500))
    assert r.lade_pause
    assert r.regelung.modus == "laden"
    assert sum(zuteilung(r).values()) > 0


# --- Notstromreserve ----------------------------------------------------------


def _notstrom(**kw):
    kw.setdefault("now", lokal(13))
    kw.setdefault("socs", [60, 60, 60])
    return plan_input(emergency_reserve=True, **kw)


def test_notstromreserve_zielt_sofort_auf_100():
    r = P.compute_plan(_notstrom(saldo_w=0.0))
    assert r.lade_ziel_soc == 100.0
    assert r.lade_start is None
    assert r.lade_deckel_soc == 100.0
    assert not r.lade_pause  # auch mittags nicht


def test_notstromreserve_hat_vorrang_vor_der_wallbox():
    # Selbst mit ev_first und mitten in der Mittagspause: die Reserve zuerst.
    inp = _notstrom(saldo_w=-1500, modulateds=[load()], priority_mode=PRIORITY_EV_FIRST)
    res = P.compute_plan(inp)
    assert coordination.akku_hat_vorrang(inp)
    assert coordination.akku_ladereservierung(inp, res) > 0


def test_notstromreserve_laedt_mit_voller_schrittweite():
    kw = dict(now=lokal(13), socs=[60, 60, 60], saldo_w=-1500.0)
    normal = P.compute_plan(plan_input(**kw))
    notstrom = P.compute_plan(plan_input(emergency_reserve=True, **kw))
    assert sum(zuteilung(notstrom).values()) > sum(zuteilung(normal).values())


def test_notstromreserve_steht_in_der_empfehlung():
    r = P.compute_plan(_notstrom(socs=[40, 40, 40], saldo_w=-1500))
    assert "Notstromreserve" in r.empfehlung


def test_ohne_notstromreserve_kuendigt_die_empfehlung_den_ladebeginn_an():
    r = _plan(socs=[60, 60, 60], modulateds=[load()])
    start = r.lade_start
    assert start is not None
    assert f"ab {_lokal_h(start):02.0f}:" in r.empfehlung or "ab " in r.empfehlung


def test_sonnenuntergang_bestimmt_das_rampenende():
    # Früherer Sonnenuntergang (Winter) zieht Ende UND Start mit nach vorn.
    frueh = _plan(socs=[60, 60, 60], sunset=SUNSET - timedelta(hours=4)).lade_start
    spaet = _plan(socs=[60, 60, 60]).lade_start
    assert frueh is not None and spaet is not None
    assert frueh < spaet


def test_naechster_sonnenaufgang_bleibt_unberuehrt():
    # Sanity: die Fixture liefert den Aufgang NACH dem Sonnenuntergang.
    assert NEXT_SUNRISE > SUNSET
