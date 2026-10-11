/**
 * Visual Low-Code / No-Code Rule Builder for Radxa Smart Water.
 * Manages rule composition cards, condition blocks, actions, and API synchronization.
 */

window.RulesBuilder = {
  rules: [],
  templates: [],

  async init() {
    await this.fetchTemplates();
    await this.fetchRules();
    this.renderTemplates();
    this.renderRulesList();
    this.resetEditorForm();
    console.log("RulesBuilder studio initialized.");
  },

  async fetchRules() {
    try {
      const res = await fetch("/api/rules");
      if (res.ok) {
        this.rules = await res.json();
        this.renderRulesList();
      }
    } catch (e) {
      console.error("Failed to load rules:", e);
    }
  },

  async fetchTemplates() {
    try {
      const res = await fetch("/api/rules/templates");
      if (res.ok) {
        this.templates = await res.json();
      }
    } catch (e) {
      console.error("Failed to load templates:", e);
    }
  },

  renderTemplates() {
    const container = document.getElementById("template-cards-container");
    if (!container) return;

    container.innerHTML = this.templates
      .map(
        (tpl) => `
      <div class="card" style="border-left: 4px solid var(--accent-blue); cursor: pointer;" onclick="RulesBuilder.loadTemplate('${tpl.id}')">
        <div style="font-weight: 700; font-size: 14px; margin-bottom: 4px; color: var(--text-primary);">${tpl.name}</div>
        <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 8px;">${tpl.description}</div>
        <button class="btn btn-secondary btn-sm" type="button">Use Template</button>
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

    // Clear and populate conditions
    const condContainer = document.getElementById("conditions-container");
    condContainer.innerHTML = "";
    tpl.conditions.forEach((c) => this.addConditionRow(c.source, c.operator, c.value));

    // Clear and populate actions
    const actContainer = document.getElementById("actions-container");
    actContainer.innerHTML = "";
    tpl.actions.forEach((a) => this.addActionRow(a.target, a.duration_seconds, a.speech_text));

    // Scroll to editor
    document.getElementById("rule-editor-card")?.scrollIntoView({ behavior: "smooth" });
  },

  renderRulesList() {
    const container = document.getElementById("active-rules-container");
    if (!container) return;

    if (this.rules.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 2rem; color: var(--text-muted); font-size: 13px;">
          No automation rules created yet. Pick a template above or construct a new rule.
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
            if (a.target === "voice.announce") return `Announce: "${a.speech_text}"`;
            return `Water via <strong>${a.target}</strong> (${a.duration_seconds}s)`;
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
              <button class="btn btn-secondary btn-sm" onclick="RulesBuilder.toggleRule('${r.id}')">
                ${r.enabled ? "Disable" : "Enable"}
              </button>
              <button class="btn btn-danger btn-sm" onclick="RulesBuilder.deleteRule('${r.id}')">
                Delete
              </button>
            </div>
          </div>
          <div style="font-size: 13px; color: var(--text-secondary); background: #f8fafc; padding: 0.5rem 0.75rem; border-radius: 0.375rem; border: 1px solid #e2e8f0;">
            <div><strong>IF:</strong> ${condText || "No conditions"}</div>
            <div style="margin-top: 4px;"><strong>THEN:</strong> ${actText || "No actions"}</div>
          </div>
        </div>
      `;
      })
      .join("");
  },

  addConditionRow(source = "sensor.zone_1_moisture", operator = "<", value = 35) {
    const container = document.getElementById("conditions-container");
    if (!container) return;

    const rowId = `cond-${Date.now()}-${Math.random().toString(36).substr(2, 4)}`;
    const div = document.createElement("div");
    div.id = rowId;
    div.className = "flex items-center gap-2";
    div.style.marginBottom = "0.5rem";
    div.innerHTML = `
      <select class="form-select cond-source" style="flex: 2;">
        <option value="sensor.zone_1_moisture" ${source === "sensor.zone_1_moisture" ? "selected" : ""}>Zone 1 Soil Moisture (%)</option>
        <option value="sensor.zone_2_moisture" ${source === "sensor.zone_2_moisture" ? "selected" : ""}>Zone 2 Soil Moisture (%)</option>
        <option value="sensor.zone_3_moisture" ${source === "sensor.zone_3_moisture" ? "selected" : ""}>Zone 3 Soil Moisture (%)</option>
        <option value="sensor.zone_4_moisture" ${source === "sensor.zone_4_moisture" ? "selected" : ""}>Zone 4 Soil Moisture (%)</option>
        <option value="sensor.zone_1_voltage" ${source === "sensor.zone_1_voltage" ? "selected" : ""}>Zone 1 Voltage (V)</option>
        <option value="sensor.zone_2_voltage" ${source === "sensor.zone_2_voltage" ? "selected" : ""}>Zone 2 Voltage (V)</option>
        <option value="sensor.zone_3_voltage" ${source === "sensor.zone_3_voltage" ? "selected" : ""}>Zone 3 Voltage (V)</option>
        <option value="sensor.zone_4_voltage" ${source === "sensor.zone_4_voltage" ? "selected" : ""}>Zone 4 Voltage (V)</option>
        <option value="tank.low_float" ${source === "tank.low_float" ? "selected" : ""}>Overhead Tank Low Float</option>
        <option value="reservoir.empty" ${source === "reservoir.empty" ? "selected" : ""}>Reservoir Empty Float</option>
        <option value="reservoir.state" ${source === "reservoir.state" ? "selected" : ""}>Reservoir State (ok/empty)</option>
      </select>
      <select class="form-select cond-operator" style="flex: 1;">
        <option value="<" ${operator === "<" ? "selected" : ""}>&lt; (Less Than)</option>
        <option value=">" ${operator === ">" ? "selected" : ""}>&gt; (Greater Than)</option>
        <option value="==" ${operator === "==" ? "selected" : ""}>== (Equals)</option>
        <option value="!=" ${operator === "!=" ? "selected" : ""}>!= (Not Equals)</option>
      </select>
      <input type="text" class="form-input cond-value" style="flex: 1;" value="${value}" placeholder="Value" />
      <button class="btn btn-secondary btn-sm" type="button" onclick="document.getElementById('${rowId}').remove()">✕</button>
    `;
    container.appendChild(div);
  },

  addActionRow(target = "pump.zone_1", duration = 15, speech = "") {
    const container = document.getElementById("actions-container");
    if (!container) return;

    const rowId = `act-${Date.now()}-${Math.random().toString(36).substr(2, 4)}`;
    const div = document.createElement("div");
    div.id = rowId;
    div.className = "flex items-center gap-2";
    div.style.marginBottom = "0.5rem";
    div.innerHTML = `
      <select class="form-select act-target" style="flex: 2;" onchange="RulesBuilder.onTargetChanged('${rowId}', this.value)">
        <option value="pump.zone_1" ${target === "pump.zone_1" ? "selected" : ""}>Drip Zone 1 (DC Pump)</option>
        <option value="pump.zone_2" ${target === "pump.zone_2" ? "selected" : ""}>Drip Zone 2 (DC Pump)</option>
        <option value="pump.zone_3" ${target === "pump.zone_3" ? "selected" : ""}>Drip Zone 3 (DC Pump)</option>
        <option value="pump.zone_4" ${target === "pump.zone_4" ? "selected" : ""}>Drip Zone 4 (DC Pump)</option>
        <option value="motor.ac" ${target === "motor.ac" ? "selected" : ""}>Overhead AC Contactor Pump</option>
        <option value="voice.announce" ${target === "voice.announce" ? "selected" : ""}>Local Voice Alert (espeak)</option>
      </select>
      <div id="${rowId}-params" style="flex: 3;" class="flex gap-2">
        ${this.renderTargetParams(target, duration, speech)}
      </div>
      <button class="btn btn-secondary btn-sm" type="button" onclick="document.getElementById('${rowId}').remove()">✕</button>
    `;
    container.appendChild(div);
  },

  onTargetChanged(rowId, targetValue) {
    const container = document.getElementById(`${rowId}-params`);
    if (container) {
      container.innerHTML = this.renderTargetParams(targetValue, 15, "");
    }
  },

  renderTargetParams(target, duration = 15, speech = "") {
    if (target === "voice.announce") {
      return `<input type="text" class="form-input act-speech" placeholder="Announcement phrase..." value="${speech || ""}" style="width: 100%;" />`;
    }
    return `
      <input type="number" class="form-input act-duration" placeholder="Duration (sec)" value="${duration || 15}" min="1" max="900" style="flex: 1;" />
      <span style="font-size: 12px; color: var(--text-muted); align-self: center;">seconds</span>
    `;
  },

  resetEditorForm() {
    const nameInput = document.getElementById("rule-name-input");
    if (nameInput) nameInput.value = "";
    const condContainer = document.getElementById("conditions-container");
    if (condContainer) condContainer.innerHTML = "";
    const actContainer = document.getElementById("actions-container");
    if (actContainer) actContainer.innerHTML = "";

    // Add default initial row
    this.addConditionRow();
    this.addActionRow();
  },

  async saveCurrentRule() {
    const name = document.getElementById("rule-name-input").value.trim();
    if (!name) {
      alert("Please provide a name for this automation rule.");
      return;
    }

    const combinator = document.getElementById("rule-combinator-select").value;

    // Harvest conditions
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

    // Harvest actions
    const actRows = document.querySelectorAll("#actions-container > div");
    const actions = [];
    actRows.forEach((row) => {
      const target = row.querySelector(".act-target")?.value;
      const speech = row.querySelector(".act-speech")?.value;
      const dur = row.querySelector(".act-duration")?.value;

      const actObj = { target: target };
      if (target === "voice.announce") {
        actObj.speech_text = speech || "Automation alert triggered";
      } else {
        actObj.duration_seconds = Number(dur) || 15;
      }
      actions.push(actObj);
    });

    if (conditions.length === 0 || actions.length === 0) {
      alert("A rule requires at least one condition and one action.");
      return;
    }

    const payload = {
      name,
      enabled: true,
      combinator,
      conditions,
      actions,
    };

    try {
      const res = await fetch("/api/rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        this.resetEditorForm();
        await this.fetchRules();
        alert("Automation rule saved and activated successfully!");
      } else {
        const err = await res.json();
        alert("Failed to save rule: " + (err.detail || err.message || "Unknown error"));
      }
    } catch (e) {
      console.error("Save rule error:", e);
      alert("Network error saving rule.");
    }
  },

  async toggleRule(ruleId) {
    try {
      const res = await fetch(`/api/rules/${ruleId}/toggle`, { method: "POST" });
      if (res.ok) {
        await this.fetchRules();
      }
    } catch (e) {
      console.error("Toggle error:", e);
    }
  },

  async deleteRule(ruleId) {
    if (!confirm("Are you sure you want to delete this rule?")) return;
    try {
      const res = await fetch(`/api/rules/${ruleId}`, { method: "DELETE" });
      if (res.ok) {
        await this.fetchRules();
      }
    } catch (e) {
      console.error("Delete error:", e);
    }
  },
};

document.addEventListener("DOMContentLoaded", () => {
  window.RulesBuilder.init();
});
