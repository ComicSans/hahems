/**
 * hems-flow-card — Lastfluss-Visualisierung für die HEMS-Integration.
 *
 * Dependency-frei (kein Lit, kein Build). Datenquelle ist ein einziger
 * Sensor (Standard: sensor.hems_lastfluss), der alle Leistungen als
 * Attribute trägt:
 *   pv_w, netz_w (positiv = Bezug), batterie_w (positiv = Entladen),
 *   haus_w, wp_w, wallbox_w, speicher_soc, pv_geschaetzt
 *
 * Die Aufteilung auf die Kanten folgt derselben Merit-Order wie der
 * Planner: PV deckt zuerst das Haus, dann die Akkus, der Rest speist ein.
 */

const NODES = {
  pv: { x: 220, y: 70, color: "#ff9800", label: "PV", icon: "☀️" },
  grid: { x: 75, y: 210, color: "#488fc2", label: "Netz", icon: "🔌" },
  batt: { x: 365, y: 210, color: "#4caf50", label: "Batterie", icon: "🔋" },
  home: { x: 220, y: 320, color: "#9c6ad6", label: "Haus", icon: "🏠" },
};

const R = 42; // Knotenradius

// Pfade jeweils von Quelle nach Ziel (Animationsrichtung = Flussrichtung)
const EDGES = {
  pv_home: { d: "M 220 114 L 220 276", color: NODES.pv.color },
  pv_grid: { d: "M 189 100 Q 95 130 90 168", color: NODES.pv.color },
  pv_batt: { d: "M 251 100 Q 345 130 350 168", color: NODES.pv.color },
  grid_home: { d: "M 103 241 Q 150 300 179 308", color: NODES.grid.color },
  batt_home: { d: "M 337 241 Q 290 300 261 308", color: NODES.batt.color },
  grid_batt: { d: "M 117 208 Q 220 170 323 208", color: NODES.grid.color },
};

const MIN_FLOW_W = 15; // darunter gilt eine Kante als inaktiv

function fmtW(w) {
  if (w === null || w === undefined) return "–";
  const abs = Math.abs(w);
  if (abs >= 1000) {
    return `${(abs / 1000).toLocaleString("de-DE", {
      minimumFractionDigits: 1,
      maximumFractionDigits: 2,
    })} kW`;
  }
  return `${Math.round(abs)} W`;
}

/** Kantenflüsse aus den vier Knotengrößen ableiten (alles >= 0). */
function computeFlows(a) {
  const pv = Math.max(0, a.pv_w ?? 0);
  const saldo = a.netz_w ?? 0;
  const batt = a.batterie_w ?? 0;
  const gridIn = Math.max(0, saldo);
  const gridOut = Math.max(0, -saldo);
  const battOut = Math.max(0, batt);
  const battIn = Math.max(0, -batt);
  const home = a.haus_w ?? Math.max(0, pv + saldo + batt);

  const pvHome = Math.min(pv, home);
  const pvBatt = Math.min(pv - pvHome, battIn);
  const pvGrid = Math.min(pv - pvHome - pvBatt, gridOut);
  const battHome = Math.min(battOut, home - pvHome);
  const gridHome = Math.max(0, Math.min(gridIn, home - pvHome - battHome));
  const gridBatt = Math.max(0, battIn - pvBatt); // Notreserve-Netzladung

  return {
    pv_home: pvHome,
    pv_grid: pvGrid,
    pv_batt: pvBatt,
    grid_home: gridHome,
    batt_home: battHome,
    grid_batt: gridBatt,
  };
}

class HemsFlowCard extends HTMLElement {
  static getStubConfig() {
    return { entity: "sensor.hems_lastfluss" };
  }

  setConfig(config) {
    this._config = {
      entity: config.entity || "sensor.hems_lastfluss",
      title: config.title,
    };
    this._lastUpdated = null;
  }

  getCardSize() {
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
        <ha-card header="${this._config.title ?? "Lastfluss"}">
          <div style="padding:16px;color:var(--secondary-text-color)">
            Entität <code>${this._config.entity}</code> nicht gefunden.
          </div>
        </ha-card>`;
      return;
    }

    const a = state.attributes;
    const flows = computeFlows(a);
    const batt = a.batterie_w;
    const hasBatt = batt !== null && batt !== undefined || a.speicher_soc != null;

    const nodeValues = {
      pv: fmtW(a.pv_w ?? 0),
      grid: fmtW(a.netz_w),
      batt: fmtW(batt),
      home: fmtW(a.haus_w),
    };
    const nodeSub = {
      pv: a.pv_geschaetzt ? "geschätzt" : "",
      grid: (a.netz_w ?? 0) >= 0 ? "Bezug" : "Einspeisung",
      batt:
        a.speicher_soc != null
          ? `${Math.round(a.speicher_soc)} %`
          : (batt ?? 0) >= 0
            ? "Entladen"
            : "Laden",
      home: "Verbrauch",
    };

    const edgeSvg = Object.entries(EDGES)
      .filter(([key]) => hasBatt || !key.includes("batt"))
      .map(([key, e]) => {
        const w = flows[key];
        const active = w > MIN_FLOW_W;
        const dur = Math.min(8, Math.max(1.2, 4000 / Math.max(w, 1)));
        return `
          <path class="edge" d="${e.d}"></path>
          ${
            active
              ? `<path class="flow" d="${e.d}" stroke="${e.color}"
                   style="animation-duration:${dur.toFixed(2)}s"></path>`
              : ""
          }`;
      })
      .join("");

    const nodeSvg = Object.entries(NODES)
      .filter(([key]) => hasBatt || key !== "batt")
      .map(
        ([key, n]) => `
        <circle cx="${n.x}" cy="${n.y}" r="${R}" class="node" stroke="${n.color}"></circle>
        <text x="${n.x}" y="${n.y - 14}" class="icon">${n.icon}</text>
        <text x="${n.x}" y="${n.y + 8}" class="value">${nodeValues[key]}</text>
        <text x="${n.x}" y="${n.y + 26}" class="sub">${nodeSub[key]}</text>
        <text x="${n.x}" y="${n.y + R + 16}" class="label">${n.label}</text>`
      )
      .join("");

    const chips = [
      a.wp_w != null ? `♨️ Wärmepumpe ${fmtW(a.wp_w)}` : null,
      a.wallbox_w != null ? `🚗 Wallbox ${fmtW(a.wallbox_w)}` : null,
    ]
      .filter(Boolean)
      .map((c) => `<span class="chip">${c}</span>`)
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        ha-card { overflow: hidden; }
        .container { padding: 8px 8px 4px; }
        svg { width: 100%; height: auto; display: block; }
        .node {
          fill: var(--card-background-color, var(--ha-card-background, #fff));
          stroke-width: 2.5;
        }
        .edge {
          fill: none;
          stroke: var(--divider-color, #e0e0e0);
          stroke-width: 2;
        }
        .flow {
          fill: none;
          stroke-width: 3;
          stroke-linecap: round;
          stroke-dasharray: 5 12;
          animation: dash linear infinite;
        }
        @keyframes dash {
          from { stroke-dashoffset: 0; }
          to { stroke-dashoffset: -170; }
        }
        .icon { font-size: 17px; text-anchor: middle; }
        .value {
          font-size: 15px;
          font-weight: 600;
          text-anchor: middle;
          fill: var(--primary-text-color, #212121);
        }
        .sub, .label {
          font-size: 11px;
          text-anchor: middle;
          fill: var(--secondary-text-color, #727272);
        }
        .footer {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          padding: 0 16px 14px;
        }
        .chip {
          font-size: 12px;
          padding: 3px 10px;
          border-radius: 12px;
          background: var(--secondary-background-color, #f5f5f5);
          color: var(--primary-text-color, #212121);
        }
      </style>
      <ha-card ${this._config.title ? `header="${this._config.title}"` : ""}>
        <div class="container">
          <svg viewBox="0 0 440 390" role="img" aria-label="Lastfluss">
            ${edgeSvg}
            ${nodeSvg}
          </svg>
        </div>
        ${chips ? `<div class="footer">${chips}</div>` : ""}
      </ha-card>`;
  }
}

if (!customElements.get("hems-flow-card")) {
  customElements.define("hems-flow-card", HemsFlowCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "hems-flow-card")) {
  window.customCards.push({
    type: "hems-flow-card",
    name: "HEMS Lastfluss",
    description: "Animierter Energiefluss zwischen PV, Netz, Batterie und Haus.",
    preview: true,
  });
}
