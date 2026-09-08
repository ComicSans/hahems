"""EV↔Akku-Ladevorrang (strategies/coordination.py).

Vor dem Fix ignorierte die Aktuierung den priority_mode: die Wallbox bediente
sich immer zuerst am Überschuss, der Akku bekam nur den Rest ("das E-Auto
gewann die Ladehoheit immer"). Diese Tests belegen, dass der Vorrang jetzt
echt greift — und dass ein laufendes Auto nie abgeregelt wird.
"""
from __future__ import annotations

import ast
import inspect

from factories import load, lokal, plan_input, storage, zuteilung
from hems import planner as P
from hems.strategies import coordination
from hems.strategies.types import PlanFlags

# Der Vorrang wird erst dort verteilt, wo der Akku überhaupt laden WILL: die
# Ladung läuft just in time, vorher hält der Deckel auf dem Stand und der Akku
# reserviert nichts (test_charge_deckel.py). 19:00 lokal mit halb leeren
# Speichern liegt am Referenztag mitten in der Rampe.
RAMPE_LAEUFT = lokal(19)


def _run(mode, *, wb_on, socs=(40, 40, 40), saldo=-6000.0, now=RAMPE_LAEUFT):
    wb_w = 4200.0 if wb_on else 0.0
    wb = load("WB", power_w=wb_w, ist_an=wb_on, an_seit_s=3600, nachfrage=wb_on)
    flags = PlanFlags()
    r = P.compute_plan(
        plan_input(
            now=now,
            socs=list(socs),
            saldo_w=saldo,
            modulateds=[wb],
            wallbox_w=wb_w,
            priority_mode=mode,
            flags=flags,
        )
    )
    return r


def test_battery_first_gibt_akku_vorrang():
    # Gleicher Überschuss: battery_first lädt den Akku deutlich stärker und die
    # Wallbox schwächer als ev_first.
    bf = _run("battery_first", wb_on=True)
    ef = _run("ev_first", wb_on=True)
    assert sum(zuteilung(bf).values()) > sum(zuteilung(ef).values())
    assert bf.ev_regelung.soll_summe_w < ef.ev_regelung.soll_summe_w


def test_battery_first_reisst_laufendes_auto_nicht_ab():
    # Ein bereits laufendes Auto behält mindestens sein Minimum (6 A × 3 × 230).
    bf = _run("battery_first", wb_on=True)
    ev_min = 6.0 * 3 * 230.0
    assert bf.ev_regelung.soll_summe_w >= ev_min
    assert bf.ev_regelung.lasten[0].laden is True


def test_battery_first_haelt_ausgeschaltetes_auto_zurueck():
    # Akku-Vorrang: ein noch nicht laufendes Auto startet nicht, solange der
    # Akku den Überschuss braucht; der Akku lädt.
    bf = _run("battery_first", wb_on=False)
    assert bf.ev_regelung.soll_summe_w == 0
    assert bf.regelung.modus == "laden"
    assert sum(zuteilung(bf).values()) > 0


def test_am_ladedeckel_bekommt_auto_alles():
    # Akku am Tagesdeckel (95 %) reserviert nichts mehr — die Wallbox bekommt den
    # vollen Überschuss, auch bei battery_first. Und weil sie ihn wirklich nimmt,
    # bleibt nichts übrig, das der Akku statt einer Einspeisung laden müsste.
    bf = _run("battery_first", wb_on=True, socs=(96, 96, 96))
    ef = _run("ev_first", wb_on=True, socs=(96, 96, 96))
    assert bf.ev_regelung.soll_summe_w == ef.ev_regelung.soll_summe_w
    # Der Akku bekommt nur noch, was die Wallbox in ihren Ampere-Stufen übrig
    # lässt — und das lädt er über den Deckel hinaus, statt es einzuspeisen.
    assert bf.regelung.laden_statt_einspeisen
    assert 0 < sum(zuteilung(bf).values()) < bf.ev_regelung.soll_summe_w / 10


def test_ev_first_unveraendert():
    # ev_first reserviert nie — der Akku bekommt nur das Residuum.
    ef = _run("ev_first", wb_on=True)
    assert ef.ev_regelung.ueberschuss_w == 10200
    assert ef.ev_regelung.soll_summe_w == 9660


def test_auto_gibt_dem_akku_immer_vorrang():
    """23.08.2026: `auto` heißt jetzt battery_first, nicht mehr „battery_first
    an knappen Tagen". Wer keine Priorität setzt, will den Akku zuerst voll
    haben; der frühere `knapp`-Latch (Restertrag/Speicherbedarf mit Totband)
    ist damit weggefallen. Dass das nichts kostet, hängt an der Reservierung:
    Ein Akku ohne Ladebedarf reserviert auch mit Vorrang nichts
    (test_voller_akku_reserviert_nichts)."""
    auto = _run("auto", wb_on=True)
    bf = _run("battery_first", wb_on=True)
    ef = _run("ev_first", wb_on=True)
    assert auto.ev_regelung.soll_summe_w == bf.ev_regelung.soll_summe_w
    assert zuteilung(auto) == zuteilung(bf)
    assert auto.ev_regelung.soll_summe_w < ef.ev_regelung.soll_summe_w
    # Und die Empfehlungszeile zeigt dieselbe Reihenfolge, nach der verteilt
    # wird — sie liest denselben `akku_hat_vorrang` wie die Reservierung.
    akku_pos = next(i for i, t in enumerate(auto.prioritaeten) if "Akku" in t)
    auto_pos = next(i for i, t in enumerate(auto.prioritaeten) if "E-Auto" in t)
    assert akku_pos < auto_pos


def test_koordination_konvergiert_ueber_zyklen():
    """Mehrzyklus-Rückkopplung: Messleistung folgt dem Soll des Vorzyklus. Der
    gekoppelte EV-/Akku-Regelkreis muss in einen festen Punkt einlaufen, statt
    um die Ladehoheit zu pendeln (kein Dauer-Gerangel)."""
    pv, house, lag = 9000.0, 400.0, 0.6
    wallbox, bat = 4140.0, 0.0
    flags = None
    tail = []
    for i in range(20):
        saldo = house + wallbox - pv - bat
        ss = [storage(f"L{k+1}", 60.0, power_w=bat / 3) for k in range(3)]
        on = wallbox > 100
        wb = load("WB", power_w=wallbox, ist_an=on, an_seit_s=3600, nachfrage=on)
        r = P.compute_plan(
            plan_input(
                storage_states=ss,
                saldo_w=saldo,
                modulateds=[wb],
                wallbox_w=wallbox,
                priority_mode="battery_first",
                flags=flags,
                gain_level="max",
            )
        )
        flags = r.flags
        ev = r.ev_regelung.soll_summe_w
        bs = r.regelung.soll_w
        wallbox += lag * (ev - wallbox)
        bat += lag * (bs - bat)
        if i >= 15:
            tail.append((round(ev), round(bs)))
    # Im Schwanz kaum noch Bewegung -> konvergiert (kein Pendeln).
    ev_span = max(t[0] for t in tail) - min(t[0] for t in tail)
    bat_span = max(t[1] for t in tail) - min(t[1] for t in tail)
    assert ev_span < 50, f"EV pendelt: {tail}"
    assert bat_span < 50, f"Akku pendelt: {tail}"


def test_akku_laedt_nicht_gegen_netzbezug_wenn_wallbox_gedrosselt_wird():
    """Regression (aus Live-Daten, 07:53): echter Netzbezug ~1,5 kW, das Auto
    zieht real ~1,8 kW, aber der Überschuss reicht nicht für das 6-A-Minimum —
    HEMS regelt die Wallbox auf 0. Der wallbox-bereinigte Saldo kippt dann in
    eine (halluzinierte) Einspeisung; ohne die Lade-Asymmetrie lädt der Akku
    dagegen und verstärkt den Bezug. Erwartung: der Akku lädt NICHT."""
    wb = load(
        "WB", min_a=6, max_a=16, phases=1, power_w=1841.0,
        ist_an=True, an_seit_s=3600.0, nachfrage=True,
    )
    r = P.compute_plan(
        plan_input(
            storage_states=[storage(f"L{i+1}", 60.0, power_w=-25.0) for i in range(3)],
            saldo_w=1573.0,
            wallbox_w=1841.0,
            modulateds=[wb],
            priority_mode="auto",
            gain_level="max",
        )
    )
    # Die Wallbox wird wegen zu kleinem Überschuss heruntergeregelt ...
    assert r.ev_regelung.soll_summe_w < 1841
    # ... und der Akku lädt trotz bereinigter „Einspeisung" nicht gegen den
    # echten Netzbezug (ohne die Asymmetrie lädt er hier ~256 W).
    assert r.regelung.modus != "laden"
    assert r.regelung.soll_w >= 0
    assert all(z.watt == 0 for z in r.regelung.zuteilung)


def _haengende_wallbox(saldo, flags=None, battery_to_ev=False):
    """Ein Auto, das seinem Abschaltbefehl nicht folgt und weiter 1,8 kW zieht."""
    wb = load(
        "WB", min_a=6, max_a=16, phases=1, power_w=1841.0,
        ist_an=True, an_seit_s=3600.0, nachfrage=True,
    )
    return P.compute_plan(
        plan_input(
            storage_states=[
                storage(f"L{i+1}", 60.0, power_w=0.0) for i in range(3)
            ],
            saldo_w=saldo,
            wallbox_w=1841.0,
            modulateds=[wb],
            priority_mode="auto",
            gain_level="max",
            flags=flags,
            battery_to_ev=battery_to_ev,
        )
    )


def test_akku_deckt_den_bezug_der_haengenden_wallbox_nicht():
    """Kein Akkustrom ins Auto (23.08.2026). HEMS hat die Wallbox auf 0
    kommandiert und kommandiert erneut 0 — das Auto zieht aber weiter 1,8 kW.
    Die Vorsteuerung ist damit verbraucht, der Regler sähe ab hier den echten
    Saldo und entlud früher den Akku in genau den Verbraucher, vor dem er laut
    Vorrang stehen sollte. Der Bezug (1573 W) stammt hier vollständig von der
    Wallbox: ohne sie speiste das Haus mit 268 W ein.

    Der Bezug bleibt also beim Netz, bis das Auto folgt. Das ist kein von HEMS
    geplanter Netzbezug — der Abschaltbefehl steht, das Gerät gehorcht ihm
    nicht."""
    erst = _haengende_wallbox(1573.0)
    assert erst.ev_regelung.soll_summe_w == 0
    assert erst.regelung.modus != "laden"
    assert erst.flags.ev_soll_w == erst.ev_regelung.soll_summe_w

    zweit = _haengende_wallbox(1573.0, erst.flags)
    assert zweit.ev_regelung.soll_summe_w == erst.ev_regelung.soll_summe_w
    assert zweit.regelung.modus != "entladen"
    assert all(z.watt == 0 for z in zweit.regelung.zuteilung)


def test_akku_deckt_den_hausbezug_neben_der_haengenden_wallbox_weiter():
    """Gegenprobe: Die Deckelung trennt Wallbox-Bezug von echtem Hausbezug,
    statt den Akku pauschal anzuhalten. Bei 2400 W Saldo und 1841 W Wallbox
    bleiben 559 W, die das Haus selbst zieht — die darf der Akku decken, sonst
    wäre die Regel eine Selbstsperre wie am 19.08.2026."""
    erst = _haengende_wallbox(2400.0)
    zweit = _haengende_wallbox(2400.0, erst.flags)
    assert zweit.regelung.modus == "entladen"
    assert sum(zuteilung(zweit).values()) > 0
    # Gedeckelt auf den Hausanteil (559 W + 25 W Zieloffset), nicht auf den
    # rohen Saldo — ohne die Deckelung stünden hier 2425 W.
    assert 0 < zweit.regelung.soll_w <= 584.0

def test_lade_asymmetrie_ist_noop_ohne_wallbox():
    """Ohne Wallbox-Herausrechnung (inp.saldo_w == saldo_w) greift die
    Asymmetrie nicht — bei Netzbezug entlädt der Regler unverändert normal."""
    r = P.compute_plan(plan_input(socs=[60, 60, 60], saldo_w=1500.0))
    assert r.regelung.modus == "entladen"
    assert sum(zuteilung(r).values()) > 0


def test_zwang_bei_defizit_laesst_den_akku_in_ruhe():
    """Zwangsladung + Netzbezug: die Wallbox fällt auf ihre Untergrenze, der
    Akku springt aber NICHT für sie ein.

    Der Speicher-Regler rechnet bei Zwang die gemessene Wallbox-Last komplett
    aus dem Saldo heraus ("Akku schonen", siehe README). Seit die Zwangsladung
    moduliert wird, ist diese Kopplung nicht mehr offensichtlich — der Test
    hält sie fest: ohne die Wallbox stünde der Saldo bei −200 W (Einspeisung),
    der Akku hat also keinen Grund zu entladen.
    """
    wb = load("WB", power_w=4200.0, ist_an=True, an_seit_s=3600, nachfrage=True)
    r = P.compute_plan(
        plan_input(
            socs=[60, 60, 60],
            saldo_w=4000.0,      # 4200 W Wallbox, 200 W Einspeisung sonst
            modulateds=[wb],
            wallbox_w=4200.0,
            ev_force=True,
        )
    )
    # Wallbox: läuft, aber auf der Untergrenze (kein Überschuss für mehr).
    assert r.ev_regelung.lasten[0].strom_a == wb.min_a
    # Akku: entlädt nicht, um den Zwangsbezug zu decken.
    assert r.regelung.modus != "entladen"


def test_schalter_akku_darf_wallbox_laden_bei_zwang():
    """Gegenprobe zu oben mit aktivem Grundwerte-Schalter: Steht `battery_to_ev`,
    hebt die Zwangsladungs-Bereinigung nicht mehr aus dem Saldo heraus — der
    Regler sieht den Rohsaldo (dasselbe Deficit, das ohne Schalter beim Netz
    blieb) und deckt ihn aus dem Akku. `ev_target_w` bleibt bei Zwang wie
    bisher None (planner.py), das Vorsteuer-`elif` greift also nicht — keine
    Doppelzählung."""
    wb = load("WB", power_w=4200.0, ist_an=True, an_seit_s=3600, nachfrage=True)
    r = P.compute_plan(
        plan_input(
            socs=[60, 60, 60],
            saldo_w=4000.0,      # 4200 W Wallbox, 200 W Einspeisung sonst
            modulateds=[wb],
            wallbox_w=4200.0,
            ev_force=True,
            battery_to_ev=True,
        )
    )
    # Akku: deckt jetzt den Zwangsbezug, statt ihn dem Netz zu überlassen.
    assert r.regelung.modus == "entladen"
    assert sum(zuteilung(r).values()) > 0


def test_schalter_akku_darf_wallbox_laden_am_mindeststrom():
    """Gegenprobe zu `test_akku_deckt_den_bezug_der_haengenden_wallbox_nicht`
    mit aktivem Schalter: Hängt die Wallbox an ihrem Mindeststrom (Vorsteuer-
    Delta null), greift der Entlade-Deckel nicht mehr — der Akku deckt die
    volle Last, inklusive des Wallbox-Anteils."""
    erst = _haengende_wallbox(1573.0, battery_to_ev=True)
    zweit = _haengende_wallbox(1573.0, erst.flags, battery_to_ev=True)
    assert zweit.regelung.modus == "entladen"
    assert sum(zuteilung(zweit).values()) > 0


# --- Reservierung nur bei echtem Ladebedarf (23.08.2026) --------------------
# Der Betriebsfall, der die Regel erzwungen hat: Drei Hyper 2000 standen bei
# 99/100/99 % mit zusammen 0,07 kWh freier Kapazität und nahmen 0 W. Die alte
# Bedingung `soc < deckel` reservierte trotzdem 3 × 1200 W, die Wallbox blieb
# mit dem Rest unter ihrem Mindeststrom stehen, und 4466 W gingen ins Netz.


def _voll_am_nachmittag(socs, *, stale=False, saldo=-4466.0):
    """Die Anlage am 23.08.2026, 14:33 lokal: drei Speicher à 3,7 kWh, Wallbox
    aus, 4466 W Einspeisung. Nebel für morgen (Wetterfaktor 0,25) hebt das
    Ladeziel auf 100 % — sonst läge es beim Nachtbedarf und der Fall wäre schon
    über das Ziel entschieden statt über die Frist."""
    wb = load("WB", power_w=0.0, ist_an=False, nachfrage=True)
    inp = plan_input(
        now=lokal(14, 33),
        storage_states=[
            storage(f"L{i+1}", s, capacity_kwh=3.7, stale=stale)
            for i, s in enumerate(socs)
        ],
        saldo_w=saldo,
        modulateds=[wb],
        wallbox_w=0.0,
        priority_mode="auto",
        weather_factor_tomorrow=0.25,
    )
    return inp, P.compute_plan(inp)


def test_voller_akku_reserviert_nichts_und_das_auto_startet():
    inp, res = _voll_am_nachmittag([99.0, 100.0, 99.0])
    # Die Vorbedingung explizit, sonst prüfte der Test das Falsche: Der Akku
    # steht am Tagesziel, ihm fehlen 0,07 kWh von 11,1 kWh.
    assert res.lade_ziel_soc == 100.0
    assert res.speicher_bedarf_kwh < 0.1
    assert not res.lade_pause
    assert coordination.akku_ladereservierung(inp, res) == 0.0
    # Und damit reicht der Überschuss dem Auto für seinen Mindeststrom.
    assert res.ev_regelung.soll_summe_w >= 6.0 * 3 * 230.0


def test_abgemeldeter_speicher_reserviert_nichts():
    """Ein `stale` Speicher meldet einen SoC, der nur zuletzt gestimmt hat. Wer
    einer Fiktion Leistung zuteilt, hält den Rest der Anlage still — hier das
    Auto."""
    inp, res = _voll_am_nachmittag([40.0, 40.0, 40.0], stale=True)
    assert coordination.akku_ladereservierung(inp, res) == 0.0


def test_frist_trennt_fertigen_akku_vom_ladebedarf():
    """Die Grenze läuft über die Energie, nicht über den SoC-Abstand: 1200 W
    füllen in einer 10-Minuten-Mindestlaufzeit 200 Wh, bei 3,7 kWh Kapazität
    also gut 5 Prozentpunkte. Darunter ist der Akku fertig, bevor das Auto
    seine Mindestlaufzeit überhaupt hinter sich hätte — dieselbe Rechnung, mit
    der der Actuator vom 19.08.2026 bis zum 07.09.2026 „fertig" von „antwortet
    nicht" trennte. Seit Subtask C trennt der Actuator das über die feste
    physische Schwelle `SPEICHER_VOLL_SOC` (`ladeauftrag_am_ladeschluss`); die
    Frist-Arithmetik lebt seither nur noch hier, und zwar zu Recht: Sie rechnet
    gegen `max_charge_w`, eine Konfigurationsobergrenze, die mit dem Überschuss
    nicht schwankt — „reicht die Kapazität für das, was das Gerät MAXIMAL
    ziehen könnte" ist an dieser Obergrenze eine ehrliche Frage. Im
    Quittungs-Pfad ging stattdessen eine schwankende Regler-Zuteilung ein, und
    genau das Schwanken erzeugte am 07.09.2026 den Fehlalarm (Zuteilung ist
    eine Obergrenze für den Akku, keine Nachfrage).

    Beide Stände liegen unter dem Ladedeckel; allein die Frist entscheidet."""
    inp_f, res_f = _voll_am_nachmittag([96.0, 96.0, 96.0])
    inp_b, res_b = _voll_am_nachmittag([92.0, 92.0, 92.0])
    assert res_f.lade_deckel_soc > 96.0 and res_b.lade_deckel_soc > 92.0
    assert coordination.akku_ladereservierung(inp_f, res_f) == 0.0
    assert coordination.akku_ladereservierung(inp_b, res_b) == 3600.0


def test_ladender_akku_drosselt_laufendes_auto_bis_zum_aus():
    """Spec vom 23.08.2026: „Fängt er an zu laden, dann muss die Wallbox
    gedrosselt werden." Bis dahin nahm `_modulated_control` die Minima
    laufender Lasten von der Reservierung aus — das Auto behielt seinen
    Mindeststrom und der Vorrang war genau dann wirkungslos, wenn er zählt.

    Unterhalb von min_w kann eine Wallbox nicht laufen; Drosseln heißt hier
    also Abschalten. Netzbezug entsteht dabei nicht: Was das Auto verliert,
    geht in den Akku."""
    wb = load("WB", power_w=4140.0, ist_an=True, an_seit_s=3600, nachfrage=True)
    res = P.compute_plan(
        plan_input(
            now=RAMPE_LAEUFT,
            socs=[40, 40, 40],
            saldo_w=-860.0,
            modulateds=[wb],
            wallbox_w=4140.0,
            priority_mode="battery_first",
        )
    )
    # 5000 W Überschuss, davon 3 × 1200 W reserviert — 1400 W bleiben übrig,
    # zu wenig für 6 A dreiphasig.
    assert res.ev_regelung.ueberschuss_w == 1400
    assert res.ev_regelung.soll_summe_w == 0
    assert res.ev_regelung.lasten[0].grund == "Überschuss zu klein"
    assert sum(zuteilung(res).values()) > 0


def test_lange_mindestlaufzeit_schaltet_den_vorrang_nicht_stumm_ab():
    """`min_on_min` ist bis 240 Minuten konfigurierbar. Ungedeckelt schriebe die
    Frist bei 1200 W Ladeleistung 4,8 kWh als „schon fertig" ab — jeder Speicher
    unter dieser Größe reservierte nie wieder, und der Akku-Vorrang wäre stumm
    abgeschaltet, ohne dass irgendwo eine Warnung stünde.

    Der Deckel an der halben Nachtmarge (10 % von 3,7 kWh, davon die Hälfte =
    185 Wh) macht die Reservierung unabhängig von der Wallbox-Einstellung."""
    wb_kurz = load("WB", power_w=0.0, ist_an=False, nachfrage=True, min_on_min=10)
    wb_lang = load("WB", power_w=0.0, ist_an=False, nachfrage=True, min_on_min=240)

    def _res(wb):
        inp = plan_input(
            now=lokal(14, 33),
            storage_states=[
                storage(f"L{i+1}", 92.0, capacity_kwh=3.7) for i in range(3)
            ],
            saldo_w=-4466.0,
            modulateds=[wb],
            wallbox_w=0.0,
            priority_mode="auto",
            weather_factor_tomorrow=0.25,
        )
        return coordination.akku_ladereservierung(inp, P.compute_plan(inp))

    assert _res(wb_kurz) == 3600.0
    assert _res(wb_lang) == _res(wb_kurz)


def test_fertig_rechnet_gegen_max_charge_w_nicht_gegen_die_zuteilung():
    """Auflage aus dem Review von Subtask C (07.09.2026,
    Aufgabe „Speicher-Selbstsperre", Git 129880c).

    `_kapazitaet_in_frist_erschoepft` zog am 19.08.2026 als
    `ladeauftrag_in_frist_erfuellbar` in `actuation.py` ein und wurde in
    Subtask C hierher verschoben, weil `_fertig` (in `akku_ladereservierung`)
    dieselbe Arithmetik für eine andere Frage braucht. Das trägt nur, weil der
    einzige verbliebene Aufruf `zugeteilt_w=s.max_charge_w` übergibt — eine
    Konfigurationsobergrenze, die mit dem gerade verfügbaren Überschuss nicht
    schwankt. Die gelöschte Fassung übergab dort eine Regler-Zuteilung, und
    genau deren Schwanken erzeugte am 07.09.2026 den Fehlalarm: Bei 37 Wh
    Restkapazität trug die Rechnung erst ab 444 W Zuteilung, der
    Nachmittags-Restüberschuss lag darunter. Ein Ergebnis-Test sieht das
    Argument nicht — tauschte ein künftiger Umbau `s.max_charge_w` gegen einen
    Zuteilungswert, bliebe es unbemerkt, und die Falle vom 07.09. stünde an
    einer Stelle wieder offen, an der niemand sie sucht. Deshalb hier
    strukturell gepinnt, nicht über einen Wert.
    """
    baum = ast.parse(inspect.getsource(coordination))
    fertig = next(
        (
            knoten
            for knoten in ast.walk(baum)
            if isinstance(knoten, ast.FunctionDef) and knoten.name == "_fertig"
        ),
        None,
    )
    assert fertig is not None, "_fertig nicht in coordination.py gefunden"
    aufrufe = [
        k
        for k in ast.walk(fertig)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Name)
        and k.func.id == "_kapazitaet_in_frist_erschoepft"
    ]
    assert len(aufrufe) == 1, "genau ein Aufruf der Arithmetik in _fertig"
    zugeteilt = next(
        (kw.value for kw in aufrufe[0].keywords if kw.arg == "zugeteilt_w"), None
    )
    assert zugeteilt is not None, "zugeteilt_w wird nicht als Keyword übergeben"
    assert isinstance(zugeteilt, ast.Attribute) and zugeteilt.attr == "max_charge_w", (
        "zugeteilt_w muss aus max_charge_w kommen, nicht aus einer "
        "Regler-Zuteilung — sonst öffnet sich die Falle vom 07.09.2026 wieder"
    )


def test_der_deckel_gilt_auch_fuer_die_kurze_frist():
    """Gegenprobe zur Grenze selbst: 296 Wh Rest (92 % von 3,7 kWh bis 100 %)
    liegen über der halben Nachtmarge, 148 Wh (96 %) darunter. Die Grenze läuft
    damit über die Energie, nicht über einen SoC-Abstand — bis zum 07.09.2026
    dieselbe Rechnung, mit der auch der Actuator „fertig" von „antwortet
    nicht" trennte. Seit Subtask C fragt der Actuator dafür nur noch die feste
    physische Schwelle `SPEICHER_VOLL_SOC` ab; die Frist-Arithmetik rechnet nur
    noch hier weiter, und zwar zu Recht: `max_charge_w` ist eine technische
    Obergrenze, die mit dem Überschuss nicht schwankt, die Frage bleibt also
    eine ehrliche Kapazitätsfrage. Der Quittungs-Pfad rechnete gegen eine
    schwankende Regler-Zuteilung — genau das Schwanken maskierte am
    07.09.2026 den vollen, fertigen Akku als offenen Ladeauftrag."""
    inp_f, res_f = _voll_am_nachmittag([96.0, 96.0, 96.0])
    inp_b, res_b = _voll_am_nachmittag([92.0, 92.0, 92.0])
    assert coordination.akku_ladereservierung(inp_f, res_f) == 0.0
    assert coordination.akku_ladereservierung(inp_b, res_b) == 3600.0
