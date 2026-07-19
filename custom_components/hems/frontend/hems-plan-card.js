/**
 * hems-plan-card — Einspeiseplan und PV-Prognose als Zeitstrahl.
 *
 * Dependency-frei (kein Lit, kein Build). Datenquelle ist der Sensor
 * sensor.hems_einspeiseplan mit den Attributen:
 *   slots      [{von, bis, watt, soc_erwartet}]  geplante Einspeisung (Nacht)
 *   pv_kurve   [{von, bis, watt}]                geschätzte PV-Leistung
 *   budget_kwh, pv_rest_heute_kwh, pv_morgen_kwh, speicher_soc, wetter_morgen
 *
 * Balken: PV (orange) und geplante Einspeisung (grün); Linie: erwarteter
 * Speicher-SoC (rechte Achse). Zeitraum: jetzt bis morgen Sonnenuntergang.
 */

const W = 480;
const H = 240;
const PAD = { left: 40, right: 34, top: 14, bottom: 26 };

const COLOR_PV = "#ff9800";
const COLOR_PLAN = "#4caf50";
const COLOR_SOC = "#488fc2";

function fmtKwh(v) {
  if (v === null || v === undefined) return "–";
  return `${Number(v).toLocaleString("de-DE", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} kWh`;
}

class HemsPlanCard extends HTMLElement {
  static getStubConfig() {
    return { entity: "sensor.hems_einspeiseplan" };
  }

  setConfig(config) {
    this._config = {
      entity: config.entity || "sensor.hems_einspeiseplan",
      title: config.title,
    };
    this._lastUpdated = null;
  }

  getCardSize() {
    return 4;
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
        <ha-card header="${this._config.title ?? "Einspeiseplan"}">
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
        <ha-card header="${this._config.title ?? "Einspeiseplan"}">
          <div style="padding:16px;color:var(--secondary-text-color)">
            Noch keine Plandaten (Speicher-SoC oder Prognose fehlt).
          </div>
        </ha-card>`;
      return;
    }

    const t0 = Math.min(...all.map((s) => s.von.getTime()));
    const t1 = Math.max(...all.map((s) => s.bis.getTime()));
    const maxW = Math.max(100, ...all.map((s) => s.watt));
    const yMax = Math.ceil(maxW / 500) * 500;

    const x = (t) => PAD.left + ((t - t0) / (t1 - t0)) * (W - PAD.left - PAD.right);
    const y = (w) => H - PAD.bottom - (w / yMax) * (H - PAD.top - PAD.bottom);
    const ySoc = (p) => H - PAD.bottom - (p / 100) * (H - PAD.top - PAD.bottom);
    const y0 = H - PAD.bottom;

    const bar = (s, color, cls) => {
      const bx = x(s.von.getTime());
      const bw = Math.max(1, x(s.bis.getTime()) - bx - 1);
      const by = y(s.watt);
      return `<rect class="${cls}" x="${bx.toFixed(1)}" y="${by.toFixed(1)}"
        width="${bw.toFixed(1)}" height="${Math.max(0, y0 - by).toFixed(1)}"
        fill="${color}"><title>${s.von.toLocaleTimeString("de-DE", {
          hour: "2-digit",
          minute: "2-digit",
        })} · ${s.watt} W</title></rect>`;
    };

    const pvBars = pv.map((s) => bar(s, COLOR_PV, "pv")).join("");
    const planBars = plan.map((s) => bar(s, COLOR_PLAN, "plan")).join("");

    // SoC-Verlauf: Punkte an den Slot-Enden; läuft die Nacht schon, beginnt
    // die Linie beim aktuellen Gesamt-SoC.
    let socPts = plan
      .filter((s) => s.soc !== null && s.soc !== undefined)
      .map((s) => [s.bis.getTime(), s.soc]);
    const now = Date.now();
    if (plan.length && a.speicher_soc != null && now >= plan[0].von.getTime()) {
      socPts = [[Math.max(t0, now), a.speicher_soc], ...socPts];
    }
    const socLine = socPts.length
      ? `<polyline class="soc" points="${socPts
          .map(([t, p]) => `${x(t).toFixed(1)},${ySoc(p).toFixed(1)}`)
          .join(" ")}"/>`
      : "";

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

    // Jetzt-Marker
    const nowMark =
      now >= t0 && now <= t1
        ? `<line class="now" x1="${x(now)}" y1="${PAD.top}" x2="${x(now)}" y2="${y0}"/>`
        : "";

    // Achsen: alle 3 h eine Stundenmarke, links W, rechts %
    let ticks = "";
    const tick = new Date(t0);
    tick.setMinutes(0, 0, 0);
    tick.setHours(tick.getHours() + 1);
    for (; tick.getTime() < t1; tick.setHours(tick.getHours() + 1)) {
      if (tick.getHours() % 3 !== 0) continue;
      const tx = x(tick.getTime());
      ticks += `<line class="grid" x1="${tx}" y1="${y0}" x2="${tx}" y2="${y0 + 4}"/>
        <text class="tick" x="${tx}" y="${y0 + 16}">${tick.getHours()}</text>`;
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

    const chips = [
      `☀️ Heute Rest ${fmtKwh(a.pv_rest_heute_kwh)}`,
      `🌤 Morgen ${fmtKwh(a.pv_morgen_kwh)}${a.wetter_morgen ? ` · ${a.wetter_morgen}` : ""}`,
      `🔋 Budget ${fmtKwh(a.budget_kwh)}`,
      state.state !== "unknown" && state.state !== "unavailable" && state.state !== ""
        ? `⚡ Jetzt ${Math.round(Number(state.state))} W`
        : null,
    ]
      .filter(Boolean)
      .map((c) => `<span class="chip">${c}</span>`)
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        ha-card { overflow: hidden; }
        .container { padding: 8px 8px 0; }
        svg { width: 100%; height: auto; display: block; }
        .pv { opacity: 0.55; }
        .plan { opacity: 0.9; }
        .soc {
          fill: none;
          stroke: ${COLOR_SOC};
          stroke-width: 2;
          stroke-linejoin: round;
        }
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
        .legend { display: flex; gap: 14px; padding: 4px 16px 0; }
        .legend span {
          font-size: 11px;
          color: var(--secondary-text-color, #727272);
          display: flex;
          align-items: center;
          gap: 4px;
        }
        .swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
      </style>
      <ha-card ${this._config.title ? `header="${this._config.title}"` : `header="Einspeiseplan"`}>
        <div class="legend">
          <span><i class="swatch" style="background:${COLOR_PV}"></i>PV-Prognose</span>
          <span><i class="swatch" style="background:${COLOR_PLAN}"></i>Einspeisung geplant</span>
          <span><i class="swatch" style="background:${COLOR_SOC}"></i>SoC erwartet</span>
        </div>
        <div class="container">
          <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Einspeiseplan">
            ${yTicks}
            ${sep}
            ${dayLabels}
            ${pvBars}
            ${planBars}
            ${socLine}
            ${nowMark}
            ${ticks}
            ${socTicks}
          </svg>
        </div>
        <div class="footer">${chips}</div>
      </ha-card>`;
  }
}

customElements.define("hems-plan-card", HemsPlanCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "hems-plan-card",
  name: "HEMS Einspeiseplan",
  description: "Nächtlicher Einspeiseplan mit SoC-Verlauf und PV-Prognose für heute und morgen.",
  preview: true,
});
