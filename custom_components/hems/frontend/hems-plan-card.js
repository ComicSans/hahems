/**
 * hems-plan-card — Entladeplan und PV-Prognose als Zeitstrahl.
 *
 * "Entladung" meint die Akku-Abgabe ins Haus zur Nachtdeckung (Ziel:
 * Nulleinspeisung), nicht die Einspeisung ins öffentliche Netz — die zeigt
 * die Lastfluss-Karte über sensor.hems_netzsaldo.
 *
 * Dependency-frei (kein Lit, kein Build). Datenquelle ist der Sensor
 * sensor.hems_entladeplan mit den Attributen:
 *   slots           [{von, bis, watt, soc_erwartet}]  geplante Entladung (Nacht)
 *   pv_kurve        [{von, bis, watt}]                geschätzte PV-Leistung
 *   ww_sperren      [{von, bis}]                      WW-Sperrzeiten
 *   ww_legionellen  [{von, bis}]                      Legionellenschutz-Fenster
 *   ww_soll_c, ww_status                              WW-Sollwert-Empfehlung
 *   regelung_modus, regelung_w, reserve_aktiv         Speicher-Saldo-Regelung
 *   wp_modus, wp_vlt_c                                Heizkreis-Empfehlung
 *   budget_kwh, pv_rest_heute_kwh, pv_morgen_kwh, speicher_soc, wetter_morgen
 *
 * Balken: PV (orange) und geplante Entladung (grün); Linie: erwarteter
 * Speicher-SoC (rechte Achse). Unten: WW-Band (verfügbar/gesperrt/
 * Legionellenschutz) und Status-Chips der drei Regelungen.
 */

const W = 480;
const H = 300;
// bottom trägt Stundenachse und Warmwasser-Band
const PAD = { left: 40, right: 34, top: 16, bottom: 52 };
const WW_BAND = { y: H - 26, h: 11 };

const COLOR_PV = "#ff9800";
const COLOR_PLAN = "#4caf50";
const COLOR_SOC = "#488fc2";
const COLOR_WW_OK = "#26a69a";
const COLOR_WW_BLOCK = "#b0bec5";
const COLOR_WW_LEGIO = "#7e57c2";

// Anzeigetexte für den WW-Status aus dem Planner
const WW_STATUS_LABEL = {
  aus: "aus (Sperrzeit)",
  legionellenschutz: "Legionellenschutz",
  pv_boost: "PV-Boost",
  basis: "Basis",
};
const WP_MODUS_LABEL = {
  heizen: "heizen",
  kuehlen: "kühlen",
  aus: "aus",
  unbekannt: "unbekannt",
};
const REGELUNG_MODUS_LABEL = {
  laden: "Laden",
  entladen: "Entladen",
  pausiert: "Pausiert",
};

// Muss mit CARD_HEIGHT in hems-flow-card.js übereinstimmen, damit beide
// Karten nebeneinander gleich hoch sind. Per `height:` überschreibbar.
const CARD_HEIGHT = 440;

function cssLength(value) {
  return typeof value === "number" ? `${value}px` : String(value);
}

function fmtKwh(v) {
  if (v === null || v === undefined) return "–";
  return `${Number(v).toLocaleString("de-DE", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} kWh`;
}

class HemsPlanCard extends HTMLElement {
  static getStubConfig() {
    return { entity: "sensor.hems_entladeplan" };
  }

  setConfig(config) {
    this._config = {
      entity: config.entity || "sensor.hems_entladeplan",
      title: config.title,
      height: config.height ?? CARD_HEIGHT,
    };
    this._lastUpdated = null;
  }

  getCardSize() {
    // gleiche Höhe wie die Flow-Card, damit beide nebeneinander passen
    return 5;
  }

  set hass(hass) {
    this._hass = hass;
    const state = hass.states[this._config.entity];
    const stamp = state ? state.last_updated : "missing";
    if (stamp === this._lastUpdated) return;
    this._lastUpdated = stamp;
    this._render(state);
  }

  _render(state) {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });

    if (!state) {
      this.shadowRoot.innerHTML = `
        <ha-card header="${this._config.title ?? "Entladeplan"}">
          <div style="padding:16px;color:var(--secondary-text-color)">
            Entität <code>${this._config.entity}</code> nicht gefunden.
          </div>
        </ha-card>`;
      return;
    }

    const a = state.attributes;
    const plan = (a.slots || []).map((s) => ({
      von: new Date(s.von),
      bis: new Date(s.bis),
      watt: s.watt,
      soc: s.soc_erwartet,
    }));
    const pv = (a.pv_kurve || []).map((s) => ({
      von: new Date(s.von),
      bis: new Date(s.bis),
      watt: s.watt,
    }));

    const all = [...plan, ...pv];
    if (!all.length) {
      this.shadowRoot.innerHTML = `
        <ha-card header="${this._config.title ?? "Entladeplan"}">
          <div style="padding:16px;color:var(--secondary-text-color)">
            Noch keine Plandaten (Speicher-SoC oder Prognose fehlt).
          </div>
        </ha-card>`;
      return;
    }

    // Feste Achse: kompletter heutiger und kompletter morgiger Kalendertag,
    // unabhängig davon, ab wann Plandaten vorliegen.
    const dayStart = new Date();
    dayStart.setHours(0, 0, 0, 0);
    const dayEnd = new Date(dayStart);
    dayEnd.setDate(dayEnd.getDate() + 2);
    const t0 = dayStart.getTime();
    const t1 = dayEnd.getTime();
    const maxW = Math.max(100, ...all.map((s) => s.watt));
    const yMax = Math.ceil(maxW / 500) * 500;

    const x = (t) => PAD.left + ((t - t0) / (t1 - t0)) * (W - PAD.left - PAD.right);
    const y = (w) => H - PAD.bottom - (w / yMax) * (H - PAD.top - PAD.bottom);
    const ySoc = (p) => H - PAD.bottom - (p / 100) * (H - PAD.top - PAD.bottom);
    const y0 = H - PAD.bottom;

    const now = Date.now();
    const fmtTime = (d) =>
      d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });

    const bar = (s, color, cls) => {
      const bx = x(s.von.getTime());
      const bw = Math.max(1, x(s.bis.getTime()) - bx - 1);
      const by = y(s.watt);
      const past = s.bis.getTime() <= now ? " past" : "";
      return `<rect class="${cls}${past}" x="${bx.toFixed(1)}" y="${by.toFixed(1)}"
        width="${bw.toFixed(1)}" height="${Math.max(0, y0 - by).toFixed(1)}"
        fill="${color}"><title>${fmtTime(s.von)} · ${s.watt} W</title></rect>`;
    };

    const pvBars = pv.map((s) => bar(s, COLOR_PV, "pv")).join("");
    const planBars = plan.map((s) => bar(s, COLOR_PLAN, "plan")).join("");

    // SoC-Prognose ab jetzt, gestrichelt: die Linie zeigt eine Erwartung,
    // keinen gemessenen Verlauf. Für die Vergangenheit gibt es bewusst keine
    // Kurve — bekannt ist nur der aktuelle Stand.
    const socPts = (a.soc_prognose || [])
      .map((p) => [new Date(p.zeit).getTime(), p.soc])
      .filter(([t, p]) => p != null && t >= t0 && t <= t1);
    const socLine = socPts.length
      ? `<polyline class="soc" points="${socPts
          .map(([t, p]) => `${x(t).toFixed(1)},${ySoc(p).toFixed(1)}`)
          .join(" ")}"/>`
      : "";

    // Warmwasser-Band: durchgehend "verfügbar", darüber Sperrfenster (grau)
    // und Legionellenschutz-Fenster (violett, erhöhter Sollwert).
    const wwWindows = (key) =>
      (a[key] || [])
        .map((s) => ({ von: new Date(s.von), bis: new Date(s.bis) }))
        .filter((s) => s.bis.getTime() > t0 && s.von.getTime() < t1);
    const wwSegment = (s, color, cls, title) => {
      const bx = x(Math.max(t0, s.von.getTime()));
      const bw = Math.max(1, x(Math.min(t1, s.bis.getTime())) - bx);
      return `<rect class="${cls}" x="${bx.toFixed(1)}" y="${WW_BAND.y}"
        width="${bw.toFixed(1)}" height="${WW_BAND.h}" fill="${color}"
        ><title>${title} ${fmtTime(s.von)} – ${fmtTime(s.bis)}</title></rect>`;
    };
    const wwBlocks = wwWindows("ww_sperren")
      .map((s) => wwSegment(s, COLOR_WW_BLOCK, "ww-block", "Warmwasser gesperrt"))
      .join("");
    const wwLegio = wwWindows("ww_legionellen")
      .map((s) =>
        wwSegment(s, COLOR_WW_LEGIO, "ww-legio", "Legionellenschutz")
      )
      .join("");
    const wwBand = `
      <rect class="ww-ok" x="${PAD.left}" y="${WW_BAND.y}"
        width="${W - PAD.left - PAD.right}" height="${WW_BAND.h}"
        fill="${COLOR_WW_OK}"><title>Warmwasser verfügbar</title></rect>
      ${wwBlocks}
      ${wwLegio}
      <text class="ylabel" x="${PAD.left - 4}" y="${WW_BAND.y + WW_BAND.h - 2}">WW</text>`;

    // Mitternachts-Trenner + Tageslabels
    const days = [];
    const mid = new Date(t0);
    mid.setHours(24, 0, 0, 0);
    let sep = "";
    if (mid.getTime() < t1) {
      const mx = x(mid.getTime());
      sep = `<line class="grid mid" x1="${mx}" y1="${PAD.top}" x2="${mx}" y2="${y0}"/>`;
      days.push([(x(t0) + mx) / 2, "Heute"], [(mx + x(t1)) / 2, "Morgen"]);
    }
    const dayLabels = days
      .map(([dx, label]) => `<text class="day" x="${dx}" y="${PAD.top - 2}">${label}</text>`)
      .join("");

    // Bereits vergangener Teil des heutigen Tages: Für ihn liegen keine
    // Verlaufsdaten vor (die Kurven sind Prognosen ab jetzt), deshalb bleibt
    // er leer und wird nur ausgegraut.
    const pastZone =
      now > t0
        ? `<rect class="past-zone" x="${PAD.left}" y="${PAD.top}"
             width="${(x(Math.min(now, t1)) - PAD.left).toFixed(1)}"
             height="${(y0 - PAD.top).toFixed(1)}"/>`
        : "";
    const pastLabel =
      x(Math.min(now, t1)) - PAD.left > 70
        ? `<text class="past-label" x="${((PAD.left + x(Math.min(now, t1))) / 2).toFixed(1)}"
             y="${((PAD.top + y0) / 2).toFixed(1)}">keine Verlaufsdaten</text>`
        : "";

    // Jetzt-Marker
    const nowMark =
      now >= t0 && now <= t1
        ? `<line class="now" x1="${x(now)}" y1="${PAD.top}" x2="${x(now)}" y2="${y0}"/>`
        : "";

    // Achsen: über 48 h alle 3 h ein Strich, beschriftet alle 6 h
    let ticks = "";
    const tick = new Date(t0);
    tick.setMinutes(0, 0, 0);
    tick.setHours(tick.getHours() + 1);
    for (; tick.getTime() < t1; tick.setHours(tick.getHours() + 1)) {
      if (tick.getHours() % 3 !== 0) continue;
      const tx = x(tick.getTime());
      ticks += `<line class="grid" x1="${tx}" y1="${y0}" x2="${tx}" y2="${y0 + 4}"/>`;
      if (tick.getHours() % 6 === 0) {
        ticks += `<text class="tick" x="${tx}" y="${y0 + 16}">${tick.getHours()}</text>`;
      }
    }
    let yTicks = "";
    for (let v = 0; v <= yMax; v += yMax / 2) {
      yTicks += `<line class="grid" x1="${PAD.left}" y1="${y(v)}" x2="${W - PAD.right}" y2="${y(v)}"/>
        <text class="ylabel" x="${PAD.left - 4}" y="${y(v) + 3}">${v >= 1000 ? `${v / 1000}k` : v}</text>`;
    }
    const socTicks = [0, 50, 100]
      .map(
        (p) => `<text class="ylabel right" x="${W - PAD.right + 4}" y="${ySoc(p) + 3}">${p}%</text>`
      )
      .join("");

    // Status der drei Regelungen: WW-Sollwert, Speicher-Saldo-Regelung
    // und Heizkreis-Empfehlung, sofern konfiguriert bzw. Daten vorliegen.
    // "aus (Sperrzeit)" zeigt nur den vom Nutzer selbst konfigurierten
    // Zustand ohne neue Information — Chip bleibt dafür weg.
    const wwChip =
      a.ww_status && a.ww_status !== "" && a.ww_status !== "aus"
        ? a.ww_soll_c != null
          ? `🚿 WW ${Math.round(a.ww_soll_c)} °C · ${WW_STATUS_LABEL[a.ww_status] ?? a.ww_status}`
          : `🚿 WW ${WW_STATUS_LABEL[a.ww_status] ?? a.ww_status}`
        : null;
    const regelungChip =
      a.regelung_modus != null
        ? `🔋 Akku-Empfehlung: ${REGELUNG_MODUS_LABEL[a.regelung_modus] ?? a.regelung_modus}${
            a.regelung_modus !== "pausiert" && a.regelung_w != null
              ? ` ${Math.round(Math.abs(a.regelung_w))} W`
              : ""
          }${a.reserve_aktiv ? " · Kaltreserve" : ""}`
        : null;
    const wpChip =
      a.wp_modus != null
        ? `♨️ WP ${WP_MODUS_LABEL[a.wp_modus] ?? a.wp_modus}${
            a.wp_vlt_c != null ? ` · VLT ${Math.round(a.wp_vlt_c)} °C` : ""
          }`
        : null;

    const chips = [
      `☀️ Heute Rest ${fmtKwh(a.pv_rest_heute_kwh)}`,
      `🌤 Morgen ${fmtKwh(a.pv_morgen_kwh)}${a.wetter_morgen ? ` · ${a.wetter_morgen}` : ""}`,
      `🔋 Budget ${fmtKwh(a.budget_kwh)}`,
      state.state !== "unknown" && state.state !== "unavailable" && state.state !== ""
        ? `⚡ Jetzt ${Math.round(Number(state.state))} W`
        : null,
      wwChip,
      regelungChip,
      wpChip,
    ]
      .filter(Boolean)
      .map((c) => `<span class="chip">${c}</span>`)
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        /* Feste Höhe (siehe CARD_HEIGHT): das SVG skaliert mit, statt die
           Kartenhöhe zu bestimmen. So sind beide Karten in jedem Layout
           gleich hoch. */
        ha-card {
          overflow: hidden;
          height: ${cssLength(this._config.height)};
          display: flex;
          flex-direction: column;
          box-sizing: border-box;
        }
        .container { padding: 8px 8px 0; flex: 1 1 auto; min-height: 0; }
        svg { width: 100%; height: 100%; display: block; }
        .pv { opacity: 0.55; }
        .plan { opacity: 0.9; }
        .past { opacity: 0.3; }
        .past-zone {
          fill: var(--secondary-text-color, #727272);
          opacity: 0.07;
        }
        .past-label {
          font-size: 10px;
          fill: var(--secondary-text-color, #727272);
          text-anchor: middle;
          opacity: 0.8;
        }
        .soc {
          fill: none;
          stroke: ${COLOR_SOC};
          stroke-width: 2;
          stroke-dasharray: 5 4;
          stroke-linejoin: round;
        }
        .ww-ok { opacity: 0.8; }
        .ww-block { opacity: 0.9; }
        .ww-legio { opacity: 0.95; }
        .grid { stroke: var(--divider-color, #e0e0e0); stroke-width: 1; }
        .grid.mid { stroke-dasharray: 4 4; }
        .now { stroke: var(--error-color, #f44336); stroke-width: 1.5; }
        .tick, .ylabel, .day {
          font-size: 10px;
          fill: var(--secondary-text-color, #727272);
          text-anchor: middle;
        }
        .ylabel { text-anchor: end; }
        .ylabel.right { text-anchor: start; }
        .day { font-weight: 600; }
        .footer {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          padding: 8px 16px 14px;
        }
        .chip {
          font-size: 12px;
          padding: 3px 10px;
          border-radius: 12px;
          background: var(--secondary-background-color, #f5f5f5);
          color: var(--primary-text-color, #212121);
        }
        .legend {
          display: flex;
          flex-wrap: wrap;
          gap: 6px 14px;
          padding: 4px 16px 0;
        }
        .legend span {
          font-size: 11px;
          color: var(--secondary-text-color, #727272);
          display: flex;
          align-items: center;
          gap: 4px;
        }
        .swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
        /* gestrichelt wie die Linie im Diagramm */
        .soc-swatch {
          height: 0;
          border-top: 2px dashed ${COLOR_SOC};
          border-radius: 0;
          width: 12px;
        }
      </style>
      <ha-card ${this._config.title ? `header="${this._config.title}"` : `header="Entladeplan"`}>
        <div class="legend">
          <span><i class="swatch" style="background:${COLOR_PV}"></i>PV-Prognose</span>
          <span><i class="swatch" style="background:${COLOR_PLAN}"></i>Entladung geplant</span>
          <span><i class="swatch soc-swatch"></i>SoC-Prognose</span>
          <span><i class="swatch" style="background:${COLOR_WW_OK}"></i>WW verfügbar</span>
          <span><i class="swatch" style="background:${COLOR_WW_BLOCK}"></i>WW gesperrt</span>
          <span><i class="swatch" style="background:${COLOR_WW_LEGIO}"></i>Legionellenschutz</span>
        </div>
        <div class="container">
          <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet"
               role="img" aria-label="Entladeplan">
            ${pastZone}
            ${yTicks}
            ${sep}
            ${dayLabels}
            ${pvBars}
            ${planBars}
            ${socLine}
            ${nowMark}
            ${ticks}
            ${socTicks}
            ${pastLabel}
            ${wwBand}
          </svg>
        </div>
        <div class="footer">${chips}</div>
      </ha-card>`;
  }
}

// Erst nach dem window-load registrieren, sonst landet die Definition in der
// nativen Registry, bevor HA window.customElements durch
// scoped-custom-element-registry ersetzt (siehe hems-flow-card.js).
function defineWhenReady(tag, cls) {
  const define = () => {
    if (!window.customElements.get(tag)) window.customElements.define(tag, cls);
  };
  if (document.readyState === "complete") define();
  else window.addEventListener("load", define, { once: true });
}

defineWhenReady("hems-plan-card", HemsPlanCard);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "hems-plan-card")) {
  window.customCards.push({
    type: "hems-plan-card",
    name: "HEMS Entladeplan",
    description: "Nächtlicher Entladeplan mit SoC-Verlauf und PV-Prognose für heute und morgen.",
    preview: true,
  });
}
