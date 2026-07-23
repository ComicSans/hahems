/**
 * hems-flow-card — Lastfluss-Visualisierung für die HEMS-Integration.
 *
 * Dependency-frei (kein Lit, kein Build). Datenquelle ist ein einziger
 * Sensor (Standard: sensor.hems_lastfluss), der alle Leistungen als
 * Attribute trägt:
 *   pv_w, netz_w (positiv = Bezug), batterie_w (positiv = Entladen),
 *   haus_w, wp_w, wallbox_w, speicher_soc, pv_geschaetzt
 * plus Aufschlüsselungen für die Zeilen unter dem Diagramm:
 *   speicher[] (name, soc, watt), schaltlasten[] (name, prio, ist_an,
 *   soll_an, watt, erwartet_w, grund)
 * plus Status der Regelungen für die Chips:
 *   regelung_modus, regelung_w, ww_soll_c, ww_status, wp_modus, wp_vlt_c
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

// Gemeinsame Standardhöhe beider HEMS-Karten. Eine feste Höhe ist der einzige
// Weg, der in jedem Dashboard-Layout gleich hohe Karten ergibt: height:100%
// wirkt nur in Stretch-Layouts (Sections-Grid, horizontal-stack), im Masonry-
// Layout richtet sich jede Karte sonst nach ihrem eigenen Seitenverhältnis.
// Per `height:` in der Kartenkonfiguration überschreibbar ("auto" = natürlich).
const CARD_HEIGHT = 440;

function cssLength(value) {
  return typeof value === "number" ? `${value}px` : String(value);
}

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

// Wie fmtW, aber mit Vorzeichen — für die Pro-Speicher-Leistung
// (negativ = Laden, positiv = Entladen), passend zur Konvention der Entität.
function fmtWSigned(w) {
  if (w === null || w === undefined) return "–";
  return `${w < 0 ? "-" : ""}${fmtW(w)}`;
}

// Minimaler HTML-Escaper für Nutzer-Strings (Speichernamen aus der Config).
function esc(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
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
      height: config.height ?? CARD_HEIGHT,
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

    const wwLabel = {
      legionellenschutz: "Legionellenschutz",
      pv_boost: "PV-Boost",
      basis: "Basis",
    };
    const regelungLabel = { laden: "Laden", entladen: "Entladen", pausiert: "Pausiert" };
    const chips = [
      // Kein Sammel-Chip für die Wärmepumpe mehr: schaltbare Lasten stehen
      // jetzt einzeln als Zeilen (Name, Priorität, Empfehlung) unter dem
      // Diagramm — die WP ist eine davon.
      a.wp_modus != null && a.wp_vlt_c != null
        ? `🌡 WP ${a.wp_modus === "kuehlen" ? "kühlt" : a.wp_modus} · VLT ${Math.round(a.wp_vlt_c)} °C`
        : null,
      a.wallbox_w != null ? `🚗 Wallbox ${fmtW(a.wallbox_w)}` : null,
      a.regelung_modus != null
        ? `🔋 Akku-Empfehlung: ${regelungLabel[a.regelung_modus] ?? a.regelung_modus}${
            a.regelung_modus !== "pausiert" && a.regelung_w != null
              ? ` ${fmtW(a.regelung_w)}`
              : ""
          }`
        : null,
      // "aus (Sperrzeit)" zeigt nur den vom Nutzer selbst konfigurierten
      // Zustand ohne neue Information — Chip bleibt dafür weg.
      a.ww_status && a.ww_status !== "aus"
        ? a.ww_soll_c != null
          ? `🚿 WW ${Math.round(a.ww_soll_c)} °C · ${wwLabel[a.ww_status] ?? a.ww_status}`
          : `🚿 WW ${wwLabel[a.ww_status] ?? a.ww_status}`
        : null,
    ]
      .filter(Boolean)
      .map((c) => `<span class="chip">${c}</span>`)
      .join("");

    // Pro-Speicher-Zeilen: Name, SoC-Balken (dynamisch gefüllt) und Ist-
    // Leistung. Nur gerendert, wenn die Integration eine Aufschlüsselung
    // liefert; bei fehlendem SoC bleibt der Balken leer (leistungsbasierte
    // Speicher ohne SoC-Meldung).
    const storages = Array.isArray(a.speicher) ? a.speicher : [];
    const storageRows = storages
      .map((s) => {
        const soc = s.soc != null ? Math.max(0, Math.min(100, s.soc)) : null;
        const name = esc(s.name != null ? String(s.name) : "Speicher");
        return `<div class="st-row">
          <span class="st-name" title="${name}">${name}</span>
          <div class="st-bar"><div class="st-fill" style="width:${
            soc != null ? soc : 0
          }%"></div></div>
          <span class="st-soc">${soc != null ? `${Math.round(soc)} %` : "–"}</span>
          <span class="st-watt">${fmtWSigned(s.watt)}</span>
        </div>`;
      })
      .join("");
    const storageBlock = storageRows
      ? `<div class="storages">${storageRows}</div>`
      : "";

    // Pro-Schaltlast-Zeilen: Punkt = Ist-Zustand, dahinter Name, Priorität
    // (1 = wichtigste, entscheidet wer bei knappem Überschuss zuerst weicht)
    // und die Begründung der Empfehlung. Weicht die Empfehlung vom Ist ab
    // (Auto-Modus schaltet erst im nächsten Zyklus, oder HEMS beobachtet nur),
    // zeigt ein Pfeil die Zielrichtung. Rechts die gemessene Leistung, im
    // Aus-Zustand die gelernte Erwartung in Klammern.
    const loads = Array.isArray(a.schaltlasten) ? a.schaltlasten : [];
    const loadRows = loads
      .map((l) => {
        const name = esc(l.name != null ? String(l.name) : "Last");
        const an = l.ist_an === true;
        const wechsel = l.soll_an != null && l.soll_an !== an;
        const watt =
          l.watt != null
            ? fmtW(l.watt)
            : l.erwartet_w != null
              ? `(${fmtW(l.erwartet_w)})`
              : "–";
        const grund = esc(l.grund != null ? String(l.grund) : "");
        return `<div class="sw-row">
          <span class="sw-dot${an ? " on" : ""}"></span>
          <span class="st-name" title="${name}">${name}</span>
          <span class="sw-prio" title="Priorität ${l.prio ?? "?"}">P${
            l.prio ?? "?"
          }</span>
          <span class="sw-grund" title="${grund}">${
            wechsel ? `→ ${l.soll_an ? "an" : "aus"} · ` : ""
          }${grund}</span>
          <span class="st-watt">${watt}</span>
        </div>`;
      })
      .join("");
    const loadBlock = loadRows ? `<div class="storages">${loadRows}</div>` : "";

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
        .container { padding: 8px 8px 4px; flex: 1 1 auto; min-height: 0; }
        svg { width: 100%; height: 100%; display: block; }
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
        /* Pro-Speicher-Zeilen: flex:none, damit das SVG darüber schrumpft
           statt die Zeilen abzuschneiden. */
        .storages {
          flex: none;
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding: 4px 16px 6px;
        }
        .st-row {
          display: flex;
          align-items: center;
          gap: 10px;
          font-size: 12px;
        }
        .st-name {
          flex: 0 0 auto;
          max-width: 40%;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          color: var(--primary-text-color, #212121);
        }
        .st-bar {
          flex: 1 1 auto;
          min-width: 0;
          height: 8px;
          border-radius: 4px;
          background: color-mix(in srgb, ${NODES.batt.color} 22%, transparent);
          overflow: hidden;
        }
        .st-fill {
          height: 100%;
          border-radius: 4px;
          background: ${NODES.batt.color};
          transition: width 0.4s ease;
        }
        .st-soc {
          flex: 0 0 auto;
          min-width: 38px;
          text-align: right;
          font-variant-numeric: tabular-nums;
          color: var(--secondary-text-color, #727272);
        }
        .st-watt {
          flex: 0 0 auto;
          min-width: 56px;
          text-align: right;
          font-variant-numeric: tabular-nums;
          color: var(--primary-text-color, #212121);
        }
        /* Pro-Schaltlast-Zeilen — gleiches Raster wie die Speicherzeilen,
           statt SoC-Balken aber Priorität und Begründung. */
        .sw-row {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 12px;
        }
        .sw-dot {
          flex: 0 0 auto;
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: var(--divider-color, #e0e0e0);
        }
        .sw-dot.on { background: ${NODES.pv.color}; }
        .sw-prio {
          flex: 0 0 auto;
          padding: 1px 6px;
          border-radius: 8px;
          font-size: 11px;
          font-variant-numeric: tabular-nums;
          background: var(--secondary-background-color, #f5f5f5);
          color: var(--secondary-text-color, #727272);
        }
        .sw-grund {
          flex: 1 1 auto;
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          color: var(--secondary-text-color, #727272);
        }
      </style>
      <ha-card ${this._config.title ? `header="${this._config.title}"` : ""}>
        <div class="container">
          <svg viewBox="0 0 440 390" preserveAspectRatio="xMidYMid meet"
               role="img" aria-label="Lastfluss">
            ${edgeSvg}
            ${nodeSvg}
          </svg>
        </div>
        ${storageBlock}
        ${loadBlock}
        ${chips ? `<div class="footer">${chips}</div>` : ""}
      </ha-card>`;
  }
}

// Erst nach dem window-load registrieren. HA bindet Karten aus
// add_extra_js_url per dynamischem import() im <head> ein, also im Rennen mit
// dem Frontend-Bundle. Gewinnt diese Datei, landet define() in der nativen
// Registry; danach ersetzt HA window.customElements durch
// scoped-custom-element-registry mit eigener Map und findet die Karte nicht
// mehr ("Custom element not found"). Ein zweites define() ist keine Option:
// dieselbe Klasse darf nur einmal registriert werden.
function defineWhenReady(tag, cls) {
  const define = () => {
    if (!window.customElements.get(tag)) window.customElements.define(tag, cls);
  };
  if (document.readyState === "complete") define();
  else window.addEventListener("load", define, { once: true });
}

defineWhenReady("hems-flow-card", HemsFlowCard);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "hems-flow-card")) {
  window.customCards.push({
    type: "hems-flow-card",
    name: "HEMS Lastfluss",
    description: "Animierter Energiefluss zwischen PV, Netz, Batterie und Haus.",
    preview: true,
  });
}
