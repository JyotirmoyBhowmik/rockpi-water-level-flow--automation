/**
 * Visual Low-Code Rule Builder for Radxa Smart Water.
 * Zero pre-seeded templates. Allows saving custom rules as reusable templates in SQLite.
 */

window.RulesBuilder = {
  rules: [],
  templates: [],

  getHeaders() {
    const headers = { "Content-Type": "application/json" };
    const token = localStorage.getItem("smart_water_token");
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  },

  async init() {
    await this.fetchTemplates();
    await this.fetchRules();
    this.renderTemplates();
    this.renderRulesList();
    this.resetEditorForm();
  },

  async fetchRules() {
    try {
      const res = await fetch("/api/rules", { headers: this.getHeaders() });
      if (res.ok) {
        this.rules = await res.json();
        this.renderRulesList();
      }
    } catch (e) {
      console.error("Failed to load user rules:", e);
    }
  },

  async fetchTemplates() {
    try {
      const res = await fetch("/api/rules/templates", { headers: this.getHeaders() });
      if (res.ok) {
        this.templates = await res.json();
        this.renderTemplates();
      }
    } catch (e) {
      console.error("Failed to load user templates:", e);
    }
  },

  renderTemplates() {
    const container = document.getElementById("template-cards-container");
    if (!container) return;

    if (this.templates.length === 0) {
      container.innerHTML = `
        <div style="grid-column: span 4; text-align: center; padding: 1rem; color: var(--text-muted); font-size: 12px; background: #f8fafc; border-radius: 0.5rem; border: 1px dashed #cbd5e1;">
          No saved templates yet. Compose a custom rule below and click "Save as Template" to build your library.
        </div>
      `;
      return;
    }

    container.innerHTML = this.templates
      .map(
        (tpl) => `
      <div class="card" style="border-left: 4px solid var(--accent-blue);">
        <div class="flex items-center justify-between">
          <div style="font-weight: 700; font-size: 13px; color: var(--text-primary);">${tpl.name}</div>
          <button class="btn btn-secondary btn-sm" onclick="RulesBuilder.deleteTemplate('${tpl.id}')" title="Delete Template">✕</button>
        </div>
        <div style="font-size: 11px; color: var(--text-secondary); margin: 6px 0;">${tpl.description || "User-saved template"}</div>
        <button class="btn btn-secondary btn-sm" onclick="RulesBuilder.loadTemplate('${tpl.id}')" type="button">Apply Template</button>
      </div>
    `
      )
      .join("");
  },

  loadTemplate(tplId) {
    const tpl = this.templates.find((t) => t.id === tplId);
    if (!tpl) return;

    document.getElementById("rule-name-input").value = tpl.name;
    document.getElementById("rule-combinator-select").value = tpl.combinator || "AND";

    const condContainer = document.getElementById("conditions-container");
    condContainer.innerHTML = "";
    (tpl.conditions || []).forEach((c) => this.addConditionRow(c.source, c.operator, c.value));

    const actContainer = document.getElementById("actions-container");
    actContainer.innerHTML = "";
    (tpl.actions || []).forEach((a) => this.addActionRow(a.target, a.duration_seconds, a.speech_text, a.message));

    document.getElementById("rule-editor-card")?.scrollIntoView({ behavior: "smooth" });
  },

  renderRulesList() {
    const container = document.getElementById("active-rules-container");
    if (!container) return;

    if (this.rules.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 2rem; color: var(--text-muted); font-size: 13px;">
          No automation rules deployed yet. Compose a new rule below.
        </div>
      `;
      return;
    }

    container.innerHTML = this.rules
      .map((r) => {
        const condText = (r.conditions || [])
          .map((c) => `<strong>${c.source}</strong> ${c.operator} <em>${c.value}</em>`)
          .join(` <span class="badge badge-gray">${r.combinator || "AND"}</span> `);

        const actText = (r.actions || [])
          .map((a) => {
            if (a.target === "voice.announce") return `Voice: "${a.speech_text}"`;
            if (a.target === "telegram.send") return `Telegram: "${a.message}"`;
            if (a.target === "motor.start") return `Motor: START`;
            if (a.target === "motor.stop") return `Motor: STOP`;
            return `Water <strong>${a.target}</strong> (${a.duration_seconds}s)`;
          })
          .join(" + ");

        return `
        <div class="card" style="margin-bottom: 0.75rem;">
          <div class="flex items-center justify-between" style="margin-bottom: 0.5rem;">
            <div class="flex items-center gap-2">
              <span class="badge ${r.enabled ? "badge-green" : "badge-gray"}">${r.enabled ? "ACTIVE" : "DISABLED"}</span>
              <span style="font-weight: 700; font-size: 14px;">${r.name}</span>
            </div>
            <div class="flex items-center gap-2">
              <button class="btn btn-secondary btn-sm" onclick="RulesBuilder.promptSaveTemplate('${r.id}')" title="Save this rule as reusable template">
                ⭐ Save Template
              </button>
              <button class="btn btn-secondary btn-sm" onclick="RulesBuilder.toggleRule('${r.id}')">
                ${r.enabled ? "Disable" : "Enable"}
              </button>
              <button class="btn btn-danger btn-sm" onclick="RulesBuilder.deleteRule('${r.id}')">
                Delete
              </button>
            </div>
          </div>
          <div style="font-size: 12px; color: var(--text-secondary); background: #f8fafc; padding: 0.5rem 0.75rem; border-radius: 0.375rem; border: 1px solid #e2e8f0;">
            <div><strong>IF:</strong> ${condText || "No conditions"}</div>
            <div style="margin-top: 4px;"><strong>THEN:</strong> ${actText || "No actions"}</div>
          </div>
        </div>
      `;
      })
      .join("");
  },

  addConditionRow(source = "zone_1.moisture", operator = "<", value = 35) {
    const container = document.getElementById("conditions-container");
    if (!container) return;

    const rowId = `cond-${Date.now()}-${Math.random().toString(36).substr(2, 4)}`;
    const div = document.createElement("div");
    div.id = rowId;
    div.className = "flex items-center gap-2";
    div.style.marginBottom = "0.5rem";
    div.innerHTML = `
      <select class="form-select cond-source" style="flex: 2;">
        <option value="tank.low" ${source === "tank.low" ? "selected" : ""}>Overhead Tank Low Float (True/False)</option>
        <option value="motor.state" ${source === "motor.state" ? "selected" : ""}>Motor State (RUNNING/OFF/COOLDOWN_LOCKED)</option>
        <option value="interlock.state" ${source === "interlock.state" ? "selected" : ""}>Interlock State (healthy/tank_full/dry_sump/wiring_fault)</option>
        <option value="reservoir.state" ${source === "reservoir.state" ? "selected" : ""}>Reservoir State (wet/dry)</option>
        <option value="leak.state" ${source === "leak.state" ? "selected" : ""}>Leak Sensor Tripped (True/False)</option>
        <option value="link.state" ${source === "link.state" ? "selected" : ""}>Garden Link State (online/offline)</option>
        <option value="time.clock" ${source === "time.clock" ? "selected" : ""}>Time Clock (HH:MM)</option>
        <option value="zone_1.moisture" ${source === "zone_1.moisture" ? "selected" : ""}>Zone 1 Soil Moisture (%)</option>
        <option value="zone_2.moisture" ${source === "zone_2.moisture" ? "selected" : ""}>Zone 2 Soil Moisture (%)</option>
        <option value="zone_3.moisture" ${source === "zone_3.moisture" ? "selected" : ""}>Zone 3 Soil Moisture (%)</option>
        <option value="zone_4.moisture" ${source === "zone_4.moisture" ? "selected" : ""}>Zone 4 Soil Moisture (%)</option>
        <option value="zone_1.volts" ${source === "zone_1.volts" ? "selected" : ""}>Zone 1 Voltage (V)</option>
        <option value="zone_2.volts" ${source === "zone_2.volts" ? "selected" : ""}>Zone 2 Voltage (V)</option>
        <option value="zone_3.volts" ${source === "zone_3.volts" ? "selected" : ""}>Zone 3 Voltage (V)</option>
        <option value="zone_4.volts" ${source === "zone_4.volts" ? "selected" : ""}>Zone 4 Voltage (V)</option>
      </select>
      <select class="form-select cond-operator" style="flex: 1;">
        <option value="<" ${operator === "<" ? "selected" : ""}>&lt; Less Than</option>
        <option value=">" ${operator === ">" ? "selected" : ""}>&gt; Greater Than</option>
        <option value="==" ${operator === "==" ? "selected" : ""}>== Equals</option>
        <option value="!=" ${operator === "!=" ? "selected" : ""}>!= Not Equals</option>
      </select>
      <input type="text" class="form-input cond-value" style="flex: 1;" value="${value}" placeholder="Value" />
      <button class="btn btn-secondary btn-sm" type="button" onclick="document.getElementById('${rowId}').remove()">✕</button>
    `;
    container.appendChild(div);
  },

  addActionRow(target = "pump.zone_1", duration = 15, speech = "", message = "") {
    const container = document.getElementById("actions-container");
    if (!container) return;

    const rowId = `act-${Date.now()}-${Math.random().toString(36).substr(2, 4)}`;
    const div = document.createElement("div");
    div.id = rowId;
    div.className = "flex items-center gap-2";
    div.style.marginBottom = "0.5rem";
    div.innerHTML = `
      <select class="form-select act-target" style="flex: 2;" onchange="RulesBuilder.onTargetChanged('${rowId}', this.value)">
        <option value="pump.zone_1" ${target === "pump.zone_1" ? "selected" : ""}>Water Zone 1 (ESP32 DC Pump)</option>
        <option value="pump.zone_2" ${target === "pump.zone_2" ? "selected" : ""}>Water Zone 2 (ESP32 DC Pump)</option>
        <option value="pump.zone_3" ${target === "pump.zone_3" ? "selected" : ""}>Water Zone 3 (ESP32 DC Pump)</option>
        <option value="pump.zone_4" ${target === "pump.zone_4" ? "selected" : ""}>Water Zone 4 (ESP32 DC Pump)</option>
        <option value="motor.start" ${target === "motor.start" ? "selected" : ""}>Start Main AC Motor (Contactor)</option>
        <option value="motor.stop" ${target === "motor.stop" ? "selected" : ""}>Stop Main AC Motor</option>
        <option value="voice.announce" ${target === "voice.announce" ? "selected" : ""}>Voice Alert (espeak-ng)</option>
        <option value="telegram.send" ${target === "telegram.send" ? "selected" : ""}>Send Telegram Alert</option>
      </select>
      <div id="${rowId}-params" style="flex: 3;" class="flex gap-2">
        ${this.renderTargetParams(target, duration, speech, message)}
      </div>
      <button class="btn btn-secondary btn-sm" type="button" onclick="document.getElementById('${rowId}').remove()">✕</button>
    `;
    container.appendChild(div);
  },

  onTargetChanged(rowId, targetValue) {
    const container = document.getElementById(`${rowId}-params`);
    if (container) {
      container.innerHTML = this.renderTargetParams(targetValue, 15, "", "");
    }
  },

  renderTargetParams(target, duration = 15, speech = "", message = "") {
    if (target === "voice.announce") {
      return `<input type="text" class="form-input act-speech" placeholder="Announcement phrase..." value="${speech || ""}" style="width: 100%;" />`;
    }
    if (target === "telegram.send") {
      return `<input type="text" class="form-input act-message" placeholder="Telegram message text..." value="${message || ""}" style="width: 100%;" />`;
    }
    if (target === "motor.start" || target === "motor.stop") {
      return `<span style="font-size: 11px; color: var(--text-muted); align-self: center;">Obeys all hardware interlocks</span>`;
    }
    return `
      <input type="number" class="form-input act-duration" placeholder="Seconds" value="${duration || 15}" min="1" max="60" style="flex: 1;" />
      <span style="font-size: 11px; color: var(--text-muted); align-self: center;">seconds (max 60s)</span>
    `;
  },

  resetEditorForm() {
    const nameInput = document.getElementById("rule-name-input");
    if (nameInput) nameInput.value = "";
    const condContainer = document.getElementById("conditions-container");
    if (condContainer) condContainer.innerHTML = "";
    const actContainer = document.getElementById("actions-container");
    if (actContainer) actContainer.innerHTML = "";

    this.addConditionRow();
    this.addActionRow();
  },

  async saveCurrentRule() {
    const name = document.getElementById("rule-name-input").value.trim();
    if (!name) {
      alert("Please provide a name for this rule.");
      return;
    }

    const combinator = document.getElementById("rule-combinator-select").value;
    const condRows = document.querySelectorAll("#conditions-container > div");
    const conditions = [];

    condRows.forEach((row) => {
      const src = row.querySelector(".cond-source")?.value;
      const op = row.querySelector(".cond-operator")?.value;
      let val = row.querySelector(".cond-value")?.value;
      if (val !== undefined && val !== "") {
        if (!isNaN(Number(val))) val = Number(val);
        else if (val.toLowerCase() === "true") val = true;
        else if (val.toLowerCase() === "false") val = false;
        conditions.push({ source: src, operator: op, value: val });
      }
    });

    const actRows = document.querySelectorAll("#actions-container > div");
    const actions = [];
    actRows.forEach((row) => {
      const target = row.querySelector(".act-target")?.value;
      const speech = row.querySelector(".act-speech")?.value;
      const msg = row.querySelector(".act-message")?.value;
      const dur = row.querySelector(".act-duration")?.value;

      const actObj = { target };
      if (target === "voice.announce") actObj.speech_text = speech || "Automation triggered";
      else if (target === "telegram.send") actObj.message = msg || "Automation triggered";
      else if (target.startsWith("pump.")) actObj.duration_seconds = Math.min(60, Number(dur) || 15);

      actions.push(actObj);
    });

    if (conditions.length === 0 || actions.length === 0) {
      alert("A rule requires at least one condition and one action.");
      return;
    }

    const payload = { name, enabled: true, combinator, conditions, actions };

    try {
      const res = await fetch("/api/rules", {
        method: "POST",
        headers: this.getHeaders(),
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        this.resetEditorForm();
        await this.fetchRules();
        alert("Rule saved and activated.");
      } else {
        const err = await res.json();
        alert("Save failed: " + (err.detail || err.message));
      }
    } catch (e) {
      console.error(e);
      alert("Network error saving rule.");
    }
  },

  async promptSaveTemplate(ruleId) {
    const tplName = prompt("Enter a name for this template:");
    if (!tplName) return;
    const desc = prompt("Optional description for this template:") || "";

    try {
      const res = await fetch(`/api/rules/${ruleId}/save-template`, {
        method: "POST",
        headers: this.getHeaders(),
        body: JSON.stringify({ name: tplName, description: desc }),
      });
      if (res.ok) {
        await this.fetchTemplates();
        alert("Rule saved into template library.");
      } else {
        alert("Failed to save template.");
      }
    } catch (e) {
      console.error(e);
    }
  },

  async deleteTemplate(templateId) {
    if (!confirm("Delete this saved template?")) return;
    try {
      const res = await fetch(`/api/rules/templates/${templateId}`, {
        method: "DELETE",
        headers: this.getHeaders(),
      });
      if (res.ok) {
        await this.fetchTemplates();
      }
    } catch (e) {
      console.error(e);
    }
  },

  async toggleRule(ruleId) {
    try {
      const res = await fetch(`/api/rules/${ruleId}/toggle`, {
        method: "POST",
        headers: this.getHeaders(),
      });
      if (res.ok) await this.fetchRules();
    } catch (e) {
      console.error(e);
    }
  },

  async deleteRule(ruleId) {
    if (!confirm("Delete this rule?")) return;
    try {
      const res = await fetch(`/api/rules/${ruleId}`, {
        method: "DELETE",
        headers: this.getHeaders(),
      });
      if (res.ok) await this.fetchRules();
    } catch (e) {
      console.error(e);
    }
  },
};

document.addEventListener("DOMContentLoaded", () => {
  window.RulesBuilder.init();
});
