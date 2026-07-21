/**
 * hems-panel — eigenes HEMS-Panel für die Home-Assistant-Seitenleiste.
 *
 * Dependency-frei (kein Lit, kein Build), wie die HEMS-Karten. HA setzt die
 * Properties `hass`, `narrow`, `route`, `panel`. Phase 1: reines Frontend —
 * bettet die bestehenden Karten ein, schaltet Mode/Ziel/Force über die schon
 * vorhandenen Entitäten (`hass.callService`) und zeigt den Config-Sanity-Check.
 * Kein neuer Backend-Code; die Geräte-Eingabe bleibt (vorerst) im Options-Flow.
 *
 * Die DOM-Struktur wird einmal gebaut; jeder hass-Tick aktualisiert nur die
 * Live-Werte (Button-Zustände, Diagnose) und reicht hass an die Karten weiter —
 * die Karten werden nicht neu erzeugt (kein Flackern).
 */

// Erste passende Entität einer Domain finden, deren id einen der Teilstrings
// enthält (Slugs sind instanzabhängig; Standard zuerst).
function resolveEntity(hass, domain, ...needles) {
  const ids = Object.keys(hass.states);
  for (const needle of needles) {
    const exact = `${domain}.${needle}`;
    if (hass.states[exact]) return exact;
  }
  for (const needle of needles) {
    const hit = ids.find(
      (id) => id.startsWith(`${domain}.`) && id.includes(needle),
    );
    if (hit) return hit;
  }
  return null;
}

const TABS = [
  { id: "overview", label: "Übersicht" },
  { id: "control", label: "Steuerung" },
  { id: "diagnostics", label: "Diagnose" },
  { id: "config", label: "Konfiguration" },
  { id: "logs", label: "Logs" },
];

// Zeitspannen-Optionen des Logs-Reiters (Stunden). Standard: „letzte Stunden".
const LOG_SPANS = [
  { h: 1, label: "letzte Stunde" },
  { h: 6, label: "letzte 6 Stunden" },
  { h: 24, label: "letzte 24 Stunden" },
  { h: 168, label: "letzte Woche" },
];
const LOG_SPAN_DEFAULT = 6;

class HemsPanel extends HTMLElement {
  constructor() {
    super();
    this._tab = "overview";
    this._built = false;
    this._cards = [];
    this._overviewReady = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  set narrow(v) {
    this._narrow = v;
  }
  set route(v) {
    this._route = v;
  }
  set panel(v) {
    this._panel = v;
  }

  connectedCallback() {
    if (this._hass && !this._built) this._build();
  }

  // --- Aufbau (einmalig) --------------------------------------------------

  _build() {
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>${STYLE}</style>
      <div class="wrap">
        <header>
          <button class="menu" title="Menü">☰</button>
          <h1>${(this._panel && this._panel.title) || "HEMS"}</h1>
        </header>
        <nav class="tabs">
          ${TABS.map(
            (t) => `<button data-tab="${t.id}">${t.label}</button>`,
          ).join("")}
        </nav>
        <main>
          <section data-panel="overview" class="grid"></section>
          <section data-panel="control" hidden></section>
          <section data-panel="diagnostics" hidden></section>
          <section data-panel="config" hidden></section>
          <section data-panel="logs" hidden></section>
        </main>
      </div>`;

    root.querySelector(".menu").addEventListener("click", () => {
      // HA-Standardweg, die Seitenleiste zu öffnen — ohne ha-menu-button.
      this.dispatchEvent(
        new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }),
      );
    });

    this._tabButtons = [...root.querySelectorAll(".tabs button")];
    this._tabButtons.forEach((b) =>
      b.addEventListener("click", () => this._selectTab(b.dataset.tab)),
    );
    this._sections = {
      overview: root.querySelector('[data-panel="overview"]'),
      control: root.querySelector('[data-panel="control"]'),
      diagnostics: root.querySelector('[data-panel="diagnostics"]'),
      config: root.querySelector('[data-panel="config"]'),
      logs: root.querySelector('[data-panel="logs"]'),
    };

    this._buildOverview();
    this._buildControl();
    this._selectTab(this._tab);
  }

  _buildOverview() {
    const flowEntity = resolveEntity(this._hass, "sensor", "hems_lastfluss", "lastfluss");
    const planEntity = resolveEntity(this._hass, "sensor", "hems_entladeplan", "entladeplan");
    // Nach einem HA-Neustart sind die hems_*-Entities beim ersten hass-Tick
    // oft noch nicht registriert. Dann NICHT mit null-Entitäten fest verdrahten
    // (das bliebe bis zum manuellen Reload als "nicht gefunden" stehen), sondern
    // einen Platzhalter zeigen und in _ensureEntities erneut versuchen.
    if (!flowEntity || !planEntity) {
      this._sections.overview.innerHTML =
        `<div class="panel-card"><span class="missing">HEMS-Entitäten werden geladen…</span></div>`;
      return;
    }
    this._sections.overview.innerHTML = "";
    this._cards = [];
    this._sections.overview.append(
      this._makeCard("hems-flow-card", { entity: flowEntity }, "Lastfluss"),
      this._makeCard("hems-plan-card", { entity: planEntity }, "Entlade- & PV-Plan"),
    );
    this._overviewReady = true;
  }

  // Karte in einen ha-card-losen Rahmen setzen; die Karten bringen ihre
  // eigene ha-card mit. Robust gegen noch nicht geladenes customElement.
  _makeCard(tag, config, title) {
    const holder = document.createElement("div");
    holder.className = "card-holder";
    const mount = () => {
      if (!config.entity) {
        holder.innerHTML = `<div class="missing">${title}: Entität nicht gefunden.</div>`;
        return;
      }
      const el = document.createElement(tag);
      if (el.setConfig) el.setConfig(config);
      el.hass = this._hass;
      this._cards.push({ el, config });
      holder.appendChild(el);
    };
    if (window.customElements.get(tag)) mount();
    else window.customElements.whenDefined(tag).then(mount);
    return holder;
  }

  _buildControl() {
    const s = this._sections.control;
    s.innerHTML = `
      <div class="panel-card">
        <h2>Betriebsmodus</h2>
        <p class="hint">beobachten = nur empfehlen · auto = schalten · aus = Stopp</p>
        <div class="segmented" data-role="mode"></div>
      </div>
      <div class="panel-card">
        <h2>Optimierungsziel</h2>
        <div class="segmented" data-role="goal"></div>
      </div>
      <div class="panel-card">
        <h2>E-Auto Zwangsladung</h2>
        <div class="toggle-row"><button data-role="force" class="toggle"></button>
          <span class="hint" data-role="force-hint"></span></div>
      </div>`;
    this._ctrl = {
      mode: s.querySelector('[data-role="mode"]'),
      goal: s.querySelector('[data-role="goal"]'),
      force: s.querySelector('[data-role="force"]'),
      forceHint: s.querySelector('[data-role="force-hint"]'),
    };
    this._modeEntity = resolveEntity(this._hass, "select", "hems_modus", "modus");
    this._goalEntity = resolveEntity(this._hass, "select", "hems_optimierungsziel", "optimierungsziel");
    this._forceEntity = resolveEntity(this._hass, "switch", "hems_e_auto_zwangsladung", "zwangsladung");
    this._checkEntity = resolveEntity(this._hass, "binary_sensor", "hems_konfiguration", "konfiguration");

    this._ctrl.force.addEventListener("click", () => {
      const st = this._hass.states[this._forceEntity];
      if (!st) return;
      this._hass.callService("switch", st.state === "on" ? "turn_off" : "turn_on", {
        entity_id: this._forceEntity,
      });
    });
  }

  _selectTab(tab) {
    this._tab = tab;
    this._tabButtons.forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === tab),
    );
    for (const [id, el] of Object.entries(this._sections)) el.hidden = id !== tab;
    if (tab === "config" && !this._cfg) this._loadConfig();
    if (tab === "logs") this._openLogs();
  }

  // --- Live-Aktualisierung (jeder hass-Tick) ------------------------------

  _update() {
    if (!this._built) return;
    this._ensureEntities();
    for (const c of this._cards) c.el.hass = this._hass;
    this._renderSegmented("mode", this._modeEntity, "select");
    this._renderSegmented("goal", this._goalEntity, "select");
    this._renderForce();
    this._renderDiagnostics();
  }

  // Entitäts-IDs (instanzabhängige Slugs) lazy auflösen und nur nachziehen,
  // solange sie fehlen — nach einem HA-Neustart tauchen die hems_*-Entities
  // erst ein paar Ticks nach dem ersten Aufbau in hass.states auf. Ohne dieses
  // Nachziehen blieben die einmal als null gecachten IDs dauerhaft "nicht
  // gefunden", bis der Nutzer die Seite manuell neu lädt (kein JS-Fehler).
  _ensureEntities() {
    this._modeEntity ||= resolveEntity(this._hass, "select", "hems_modus", "modus");
    this._goalEntity ||= resolveEntity(this._hass, "select", "hems_optimierungsziel", "optimierungsziel");
    this._forceEntity ||= resolveEntity(this._hass, "switch", "hems_e_auto_zwangsladung", "zwangsladung");
    this._checkEntity ||= resolveEntity(this._hass, "binary_sensor", "hems_konfiguration", "konfiguration");
    if (!this._overviewReady) this._buildOverview();
  }

  _renderSegmented(role, entity, domain) {
    const box = this._ctrl[role];
    const st = entity && this._hass.states[entity];
    if (!st) {
      box.innerHTML = `<span class="missing">Entität nicht gefunden.</span>`;
      return;
    }
    const options = st.attributes.options || [];
    const current = st.state;
    // Nur neu bauen, wenn sich Optionen/Auswahl geändert haben.
    const sig = options.join("|") + "#" + current;
    if (box.dataset.sig === sig) return;
    box.dataset.sig = sig;
    box.innerHTML = "";
    for (const opt of options) {
      const b = document.createElement("button");
      // Roh-Slugs (z. B. „eigenverbrauch") lesbar anzeigen; der Service-Call
      // unten nutzt weiter den unveränderten Optionswert.
      b.textContent = opt.charAt(0).toUpperCase() + opt.slice(1);
      b.className = opt === current ? "seg active" : "seg";
      b.addEventListener("click", () =>
        this._hass.callService(domain, "select_option", {
          entity_id: entity,
          option: opt,
        }),
      );
      box.appendChild(b);
    }
  }

  _renderForce() {
    const st = this._forceEntity && this._hass.states[this._forceEntity];
    if (!st) {
      this._ctrl.force.textContent = "—";
      this._ctrl.force.disabled = true;
      return;
    }
    const on = st.state === "on";
    this._ctrl.force.textContent = on ? "AN" : "AUS";
    this._ctrl.force.classList.toggle("on", on);
    this._ctrl.forceHint.textContent = on
      ? "Lädt zwangsweise, Akku wird geschont."
      : "Aus — reguläres Überschussladen.";
  }

  _renderDiagnostics() {
    const s = this._sections.diagnostics;
    const st = this._checkEntity && this._hass.states[this._checkEntity];
    if (!st) {
      s.innerHTML = `<div class="panel-card"><span class="missing">binary_sensor.hems_konfiguration nicht gefunden.</span></div>`;
      return;
    }
    const a = st.attributes;
    const problem = st.state === "on";
    const list = (arr) =>
      arr && arr.length
        ? `<ul>${arr.map((x) => `<li>${escapeHtml(String(x))}</li>`).join("")}</ul>`
        : `<p class="ok-line">—</p>`;
    s.innerHTML = `
      <div class="panel-card banner ${problem ? "bad" : "good"}">
        ${problem ? "⚠️ Konfiguration hat Probleme" : "✓ Konfiguration bereit"}
        <span class="hint">bereit für Auto-Modus: ${a.bereit_fuer_auto ? "ja" : "nein"}
          · Überlappungsprüfung: ${a.ueberlappungspruefung || "?"}</span>
      </div>
      <div class="panel-card">
        <h2>Auto-Modus schaltet</h2>${list(a.auto_schaltet)}
      </div>
      <div class="panel-card">
        <h2>Fehler</h2>${list(a.fehler)}
        <h2>Warnungen</h2>${list(a.warnungen)}
        <h2>Überlappung mit aktiven Automationen</h2>${list(a.ueberlappung)}
        <h2>Hinweise</h2>${list(a.hinweise)}
      </div>`;
  }

  // --- Konfiguration (Editor, lazy geladen) -------------------------------

  async _loadConfig() {
    const box = this._sections.config;
    box.innerHTML = `<div class="panel-card"><span class="missing">Lade Konfiguration…</span></div>`;
    try {
      this._cfg = await this._hass.callWS({ type: "hems/config/get" });
    } catch (err) {
      box.innerHTML = `<div class="panel-card"><span class="missing">Konfiguration nicht ladbar: ${escapeHtml(
        String(err && err.message ? err.message : err),
      )}</span></div>`;
      return;
    }
    this._editing = null;
    this._renderConfig();
  }

  _renderConfig() {
    const box = this._sections.config;
    if (this._editing) return this._renderEditForm();
    const { roles, devices } = this._cfg;
    box.innerHTML =
      `<div class="cfg-head"><button class="btn ghost" data-act="reload">↻ Aktualisieren</button>
       <span class="hint">Änderungen laden die Integration neu.</span></div>` +
      `<div class="panel-card">
        <div class="role-head"><h2>Grundeinstellungen</h2>
          <button class="btn small" data-act="edit-general">Bearbeiten</button></div>
        <div class="hint">Zähler, Grundlasten, Wetter und Prioritätsmodus.</div>
      </div>` +
      roles
        .map((r) => {
          const own = devices.filter((d) => d.role === r.role);
          const rows =
            own
              .map(
                (d) => `<div class="dev-row">
              <span class="dev-name">${escapeHtml(d.name || "(ohne Name)")}</span>
              <span class="dev-actions">
                <button class="btn small" data-edit="${d.id}">Bearbeiten</button>
                <button class="btn small danger" data-remove="${d.id}">Entfernen</button>
              </span></div>`,
              )
              .join("") || `<div class="hint">— keine —</div>`;
          return `<div class="panel-card">
            <div class="role-head"><h2>${escapeHtml(r.label)}</h2>
              <button class="btn small" data-add="${r.role}">+ Hinzufügen</button></div>
            ${rows}</div>`;
        })
        .join("");

    box.querySelector('[data-act="reload"]').addEventListener("click", () => {
      this._cfg = null;
      this._loadConfig();
    });
    box
      .querySelector('[data-act="edit-general"]')
      .addEventListener("click", () => {
        this._editing = { general: true };
        this._renderEditForm();
      });
    box.querySelectorAll("[data-add]").forEach((b) =>
      b.addEventListener("click", () => this._startEdit(b.dataset.add, null)),
    );
    box.querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => {
        const dev = this._cfg.devices.find((d) => d.id === b.dataset.edit);
        this._startEdit(dev.role, dev);
      }),
    );
    box.querySelectorAll("[data-remove]").forEach((b) =>
      b.addEventListener("click", () => this._removeDevice(b.dataset.remove)),
    );
  }

  _startEdit(role, device) {
    this._editing = { role, device };
    this._renderEditForm();
  }

  _renderEditForm() {
    const box = this._sections.config;
    let title;
    let fields;
    let val;
    if (this._editing.general) {
      title = "Grundeinstellungen bearbeiten";
      fields = this._cfg.general.fields;
      const values = this._cfg.general.values || {};
      val = (f) => (values[f.key] !== undefined ? values[f.key] : f.default);
    } else {
      const { role, device } = this._editing;
      const roleObj = this._cfg.roles.find((r) => r.role === role);
      const label = (roleObj && roleObj.label) || role;
      title = `${label} ${device ? "bearbeiten" : "hinzufügen"}`;
      fields = this._cfg.schema[role] || [];
      val = (f) =>
        device && device[f.key] !== undefined ? device[f.key] : f.default;
    }
    box.innerHTML = `
      <div class="panel-card">
        <div class="role-head"><h2>${escapeHtml(title)}</h2></div>
        <form class="cfg-form">
          ${fields.map((f) => this._fieldControl(f, val(f))).join("")}
          <div class="err" data-role="err" hidden></div>
          <div class="form-actions">
            <button type="button" class="btn primary" data-act="save">Speichern</button>
            <button type="button" class="btn ghost" data-act="cancel">Abbrechen</button>
          </div>
        </form>
      </div>`;
    box.querySelector('[data-act="cancel"]').addEventListener("click", () => {
      this._editing = null;
      this._renderConfig();
    });
    box
      .querySelector('[data-act="save"]')
      .addEventListener("click", () => this._save());
    // Entity-Felder mit echten HA-Pickern bestücken (async, s. u.).
    this._entityValues = {};
    this._mountEntityPickers();
  }

  // Jeden entity-slot mit einem ha-selector (Entity-Picker) füllen. Die Werte
  // laufen über this._entityValues, weil ha-selector über Properties und ein
  // value-changed-Event arbeitet, nicht über ein <input> im DOM.
  _mountEntityPickers() {
    const box = this._sections.config;
    box.querySelectorAll(".entity-slot").forEach((slot) => {
      const key = slot.dataset.key;
      const domain = (slot.dataset.domain || "")
        .split(",")
        .filter(Boolean);
      const dc = slot.dataset.deviceClass || "";
      const current = slot.dataset.value || "";
      this._entityValues[key] = current;

      const mount = () => {
        const picker = document.createElement("ha-selector");
        picker.hass = this._hass;
        const entityCfg = {};
        if (domain.length) {
          entityCfg.domain = domain.length === 1 ? domain[0] : domain;
        }
        if (dc) entityCfg.device_class = dc;
        picker.selector = { entity: entityCfg };
        picker.value = current || undefined;
        picker.style.display = "block";
        picker.addEventListener("value-changed", (e) => {
          this._entityValues[key] = e.detail && e.detail.value != null
            ? e.detail.value
            : "";
        });
        slot.innerHTML = "";
        slot.appendChild(picker);
      };

      if (window.customElements.get("ha-selector")) {
        mount();
        return;
      }
      // ha-selector wird im Frontend teils lazy geladen. Auf die Definition
      // warten; kommt sie nicht zeitnah, auf die Datalist zurückfallen.
      let done = false;
      window.customElements
        .whenDefined("ha-selector")
        .then(() => {
          if (!done) {
            done = true;
            mount();
          }
        });
      setTimeout(() => {
        if (!done && !window.customElements.get("ha-selector")) {
          done = true;
          this._entityFallback(slot, key, domain, dc, current);
        }
      }, 2500);
    });
  }

  // Fallback ohne ha-selector: das frühere Datalist-Textfeld. Der Wert kommt
  // dann wieder aus dem DOM, daher den Key aus _entityValues entfernen.
  _entityFallback(slot, key, domain, dc, current) {
    delete this._entityValues[key];
    const opts = entityOptions(this._hass, domain, dc)
      .map(
        (e) =>
          `<option value="${escapeHtml(e.id)}">${escapeHtml(e.name)}</option>`,
      )
      .join("");
    slot.innerHTML = `<input list="dl_${key}" data-key="${key}" data-type="entity"
        value="${current ? escapeHtml(current) : ""}"
        placeholder="Entität wählen…" autocomplete="off">
      <datalist id="dl_${key}">${opts}</datalist>`;
  }

  _fieldControl(f, value) {
    const id = `f_${f.key}`;
    const req = f.required ? " <span class='req'>*</span>" : "";
    const lbl = `<label for="${id}">${escapeHtml(f.label || f.key)}${req}</label>`;
    let input;
    if (f.type === "entity") {
      // Platzhalter; nach dem Rendern mit einem echten HA-Entity-Picker
      // (ha-selector) bestückt — zeigt Klarnamen, Suche und Icons. Fällt auf
      // die Datalist zurück, falls ha-selector im Panel nicht verfügbar ist.
      input = `<div class="entity-slot" data-key="${f.key}"
                 data-domain="${(f.domain || []).join(",")}"
                 data-device-class="${f.device_class || ""}"
                 data-value="${value != null ? escapeHtml(String(value)) : ""}"></div>`;
    } else if (f.type === "number") {
      const a = [
        f.min != null ? `min="${f.min}"` : "",
        f.max != null ? `max="${f.max}"` : "",
        f.step != null ? `step="${f.step}"` : "",
      ].join(" ");
      input = `<input id="${id}" type="number" ${a} data-key="${f.key}" data-type="number"
                 value="${value != null ? value : ""}">${
                   f.unit ? `<span class="unit">${escapeHtml(f.unit)}</span>` : ""
                 }`;
    } else if (f.type === "boolean") {
      input = `<input id="${id}" type="checkbox" data-key="${f.key}" data-type="boolean" ${
        value ? "checked" : ""
      }>`;
    } else if (f.type === "time") {
      const v = value ? String(value).slice(0, 5) : "";
      input = `<input id="${id}" type="time" data-key="${f.key}" data-type="time" value="${v}">`;
    } else if (f.type === "select") {
      const labels = f.option_labels || {};
      const opts = (f.options || [])
        .map(
          (o) =>
            `<option value="${escapeHtml(o)}" ${
              o === value ? "selected" : ""
            }>${escapeHtml(labels[o] || o)}</option>`,
        )
        .join("");
      input = `<select id="${id}" data-key="${f.key}" data-type="select">${opts}</select>`;
    } else {
      input = `<input id="${id}" type="text" data-key="${f.key}" data-type="text"
                 value="${value != null ? escapeHtml(String(value)) : ""}">`;
    }
    return `<div class="field">${lbl}<div class="field-input">${input}</div></div>`;
  }

  _collectValues() {
    const box = this._sections.config;
    const values = {};
    // Nur echte Form-Controls — die entity-slot-<div>s (data-key ohne .value)
    // liefern ihren Wert über _entityValues.
    box
      .querySelectorAll("input[data-key], select[data-key], textarea[data-key]")
      .forEach((el) => {
        const key = el.dataset.key;
        const type = el.dataset.type;
        if (type === "boolean") {
          values[key] = el.checked;
        } else if (type === "number") {
          if (el.value !== "") values[key] = Number(el.value);
        } else if (type === "time") {
          if (el.value)
            values[key] = el.value.length === 5 ? `${el.value}:00` : el.value;
        } else {
          const v = el.value.trim();
          if (v !== "") values[key] = v;
        }
      });
    // Werte der ha-selector-Entity-Picker.
    for (const [key, v] of Object.entries(this._entityValues || {})) {
      if (v !== "" && v != null) values[key] = v;
    }
    return values;
  }

  async _save() {
    const box = this._sections.config;
    const errBox = box.querySelector('[data-role="err"]');
    const values = this._collectValues();
    try {
      if (this._editing.general) {
        await this._hass.callWS({ type: "hems/config/set_general", values });
      } else {
        const device = { role: this._editing.role, ...values };
        if (this._editing.device) device.id = this._editing.device.id;
        await this._hass.callWS({ type: "hems/config/upsert", device });
      }
    } catch (err) {
      errBox.hidden = false;
      errBox.textContent = `Fehler: ${err && err.message ? err.message : err}`;
      return;
    }
    this._editing = null;
    this._cfg = null;
    await this._loadConfig();
  }

  async _removeDevice(id) {
    const dev = this._cfg.devices.find((d) => d.id === id);
    if (!confirm(`„${(dev && dev.name) || id}" wirklich entfernen?`)) return;
    try {
      await this._hass.callWS({ type: "hems/config/remove", device_id: id });
    } catch (err) {
      alert(`Entfernen fehlgeschlagen: ${err && err.message ? err.message : err}`);
      return;
    }
    this._cfg = null;
    await this._loadConfig();
  }

  // --- Logs (Entscheidungs-Änderungen, lazy geladen) ----------------------

  // Beim Öffnen des Reiters einmalig die Filterleiste bauen und immer neu vom
  // Backend abrufen (der Log ist klein — nur Änderungen, max. eine Woche).
  _openLogs() {
    if (!this._logsBuilt) this._buildLogs();
    this._loadLogs();
  }

  _buildLogs() {
    this._logsBuilt = true;
    const s = this._sections.logs;
    s.innerHTML = `
      <div class="panel-card">
        <div class="logs-bar">
          <input type="search" class="logs-filter" placeholder="Nach Wort filtern…"
                 autocomplete="off">
          <select class="logs-span">
            ${LOG_SPANS.map(
              (o) =>
                `<option value="${o.h}"${
                  o.h === LOG_SPAN_DEFAULT ? " selected" : ""
                }>${o.label}</option>`,
            ).join("")}
          </select>
          <button class="btn ghost logs-reload" title="Aktualisieren">↻</button>
        </div>
        <div class="hint logs-status"></div>
      </div>
      <div class="logs-list"></div>`;
    this._logsUi = {
      filter: s.querySelector(".logs-filter"),
      span: s.querySelector(".logs-span"),
      status: s.querySelector(".logs-status"),
      list: s.querySelector(".logs-list"),
    };
    // Filtern läuft rein clientseitig auf den zuletzt geladenen Einträgen.
    this._logsUi.filter.addEventListener("input", () => this._renderLogs());
    this._logsUi.span.addEventListener("change", () => this._renderLogs());
    s.querySelector(".logs-reload").addEventListener("click", () =>
      this._loadLogs(),
    );
  }

  async _loadLogs() {
    this._logsUi.status.textContent = "Lade…";
    try {
      const res = await this._hass.callWS({ type: "hems/logs/get" });
      this._logs = Array.isArray(res && res.entries) ? res.entries : [];
    } catch (err) {
      this._logs = [];
      this._logsUi.status.textContent = `Nicht ladbar: ${
        err && err.message ? err.message : err
      }`;
      this._logsUi.list.innerHTML = "";
      return;
    }
    this._renderLogs();
  }

  _renderLogs() {
    const { filter, span, status, list } = this._logsUi;
    const all = this._logs || [];
    const spanH = Number(span.value) || LOG_SPAN_DEFAULT;
    const cutoff = Date.now() / 1000 - spanH * 3600;
    const q = filter.value.trim().toLowerCase();
    const hits = all
      .filter((e) => Number(e.ts) >= cutoff)
      .filter(
        (e) =>
          !q ||
          `${e.titel || ""} ${e.text || ""} ${e.cat || ""}`
            .toLowerCase()
            .includes(q),
      )
      .sort((a, b) => Number(b.ts) - Number(a.ts)); // neueste zuerst

    status.textContent = q
      ? `${hits.length} von ${all.length} Einträgen (Filter „${filter.value.trim()}")`
      : `${hits.length} Einträge`;

    if (!hits.length) {
      list.innerHTML = `<div class="panel-card"><span class="missing">Keine Änderungen im gewählten Zeitraum.</span></div>`;
      return;
    }
    list.innerHTML = `<div class="panel-card">${hits
      .map(
        (e) => `<div class="log-row cat-${escapeHtml(e.cat || "")}">
          <span class="log-time">${fmtLogTime(e.ts)}</span>
          <span class="log-body"><span class="log-titel">${escapeHtml(
            e.titel || "",
          )}</span> ${escapeHtml(e.text || "")}</span>
        </div>`,
      )
      .join("")}</div>`;
  }
}

// Log-Zeitstempel (Unix-Sekunden) als „Wochentag HH:MM" bzw. mit Datum, wenn
// nicht von heute — kompakt genug für die Liste.
function fmtLogTime(ts) {
  const d = new Date(Number(ts) * 1000);
  const today = new Date();
  const sameDay =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate();
  const time = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return time;
  const date = d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
  return `${date} ${time}`;
}

function entityOptions(hass, domains, deviceClass) {
  const doms = domains && domains.length ? domains : null;
  return Object.values(hass.states)
    .filter((s) => {
      const dom = s.entity_id.split(".")[0];
      if (doms && !doms.includes(dom)) return false;
      if (deviceClass && s.attributes.device_class !== deviceClass) return false;
      return true;
    })
    .map((s) => ({
      id: s.entity_id,
      name: s.attributes.friendly_name || s.entity_id,
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

const STYLE = `
  :host { display: block; background: var(--primary-background-color); min-height: 100vh; }
  /* [hidden] muss auch explizite display-Regeln (z. B. .grid) schlagen, sonst
     bliebe die Übersicht in jedem Tab sichtbar. */
  [hidden] { display: none !important; }
  .wrap { color: var(--primary-text-color); }
  header {
    display: flex; align-items: center; gap: 12px;
    height: var(--header-height, 56px); padding: 0 16px;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, #fff);
  }
  header h1 { font-size: 20px; font-weight: 400; margin: 0; }
  .menu {
    background: none; border: none; color: inherit; font-size: 22px;
    cursor: pointer; padding: 4px 8px; border-radius: 8px;
  }
  .menu:hover { background: rgba(255,255,255,.15); }
  nav.tabs { display: flex; gap: 4px; padding: 8px 12px 0;
    border-bottom: 1px solid var(--divider-color); background: var(--card-background-color); }
  nav.tabs button {
    background: none; border: none; color: var(--secondary-text-color);
    padding: 10px 16px; cursor: pointer; font-size: 14px;
    border-bottom: 3px solid transparent; border-radius: 6px 6px 0 0;
  }
  nav.tabs button.active { color: var(--primary-color); border-bottom-color: var(--primary-color); }
  nav.tabs button:hover { background: var(--secondary-background-color); }
  main { padding: 16px; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); }
  .card-holder { min-width: 0; }
  .panel-card {
    background: var(--card-background-color); border-radius: 12px;
    padding: 16px 20px; margin-bottom: 16px;
    box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,.1));
  }
  .panel-card h2 { font-size: 15px; margin: 12px 0 8px; font-weight: 500; }
  .panel-card h2:first-child { margin-top: 0; }
  .hint { color: var(--secondary-text-color); font-size: 12px; }
  .segmented { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
  .seg {
    padding: 8px 16px; border-radius: 20px; cursor: pointer; font-size: 14px;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color); color: var(--primary-text-color);
  }
  .seg.active { background: var(--primary-color); color: #fff; border-color: var(--primary-color); }
  .toggle-row { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
  .toggle {
    padding: 8px 20px; border-radius: 20px; cursor: pointer; font-weight: 600;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color); color: var(--primary-text-color);
  }
  .toggle.on { background: var(--primary-color); color: #fff; border-color: var(--primary-color); }
  .banner { font-size: 16px; display: flex; flex-direction: column; gap: 4px; }
  .banner.good { border-left: 4px solid var(--success-color, #4caf50); }
  .banner.bad { border-left: 4px solid var(--error-color, #f44336); }
  ul { margin: 4px 0 8px; padding-left: 20px; }
  li { margin: 2px 0; font-size: 13px; }
  .ok-line { color: var(--secondary-text-color); margin: 4px 0 8px; }
  .missing { color: var(--secondary-text-color); font-style: italic; }
  .cfg-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .role-head { display: flex; align-items: center; justify-content: space-between; }
  .dev-row { display: flex; align-items: center; justify-content: space-between;
    padding: 8px 0; border-top: 1px solid var(--divider-color); }
  .dev-name { font-size: 14px; }
  .dev-actions { display: flex; gap: 8px; }
  .btn {
    padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 14px;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color); color: var(--primary-text-color);
  }
  .btn.small { padding: 5px 12px; font-size: 13px; }
  .btn.primary { background: var(--primary-color); color: #fff; border-color: var(--primary-color); }
  .btn.ghost { background: none; }
  .btn.danger { color: var(--error-color, #f44336); }
  .cfg-form { display: flex; flex-direction: column; gap: 12px; margin-top: 8px; }
  .field { display: flex; flex-direction: column; gap: 4px; }
  .field > label { font-size: 13px; color: var(--secondary-text-color); }
  .field .req { color: var(--error-color, #f44336); }
  .field-input { display: flex; align-items: center; gap: 8px; }
  .field-input input[type=text], .field-input input[type=number],
  .field-input input[list], .field-input input[type=time], .field-input select {
    flex: 1; min-width: 0; padding: 8px 10px; border-radius: 8px; font-size: 14px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color);
  }
  .field-input .unit { color: var(--secondary-text-color); font-size: 13px; }
  .entity-slot { flex: 1; min-width: 0; }
  .entity-slot ha-selector, .entity-slot ha-entity-picker { display: block; width: 100%; }
  .form-actions { display: flex; gap: 8px; margin-top: 8px; }
  .err { color: var(--error-color, #f44336); font-size: 13px; }
  .logs-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .logs-filter, .logs-span {
    padding: 8px 10px; border-radius: 8px; font-size: 14px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color);
  }
  .logs-filter { flex: 1; min-width: 160px; }
  .logs-status { margin-top: 8px; }
  .logs-list .panel-card { padding: 4px 20px; }
  .log-row {
    display: flex; gap: 12px; align-items: baseline;
    padding: 8px 0; border-top: 1px solid var(--divider-color);
    border-left: 3px solid var(--divider-color); padding-left: 10px;
  }
  .log-row:first-child { border-top: none; }
  .log-time {
    color: var(--secondary-text-color); font-size: 12px;
    font-variant-numeric: tabular-nums; white-space: nowrap; min-width: 84px;
  }
  .log-body { font-size: 14px; min-width: 0; }
  .log-titel { font-weight: 600; }
  .log-row.cat-modus { border-left-color: var(--primary-color); }
  .log-row.cat-akku { border-left-color: #4caf50; }
  .log-row.cat-ww { border-left-color: #26a69a; }
  .log-row.cat-wp { border-left-color: #ef6c00; }
  .log-row.cat-ev { border-left-color: #9c6ad6; }
  .log-row.cat-ziel { border-left-color: #488fc2; }
`;

if (!window.customElements.get("hems-panel")) {
  window.customElements.define("hems-panel", HemsPanel);
}
