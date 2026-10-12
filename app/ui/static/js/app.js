/**
 * Main Controller Application Script for Radxa Smart Water.
 * Strict CSP compliant: Zero inline handlers, strict HTML escaping,
 * delegated event routing, and sessionStorage/localStorage token hierarchy.
 */

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function getStoredToken() {
  return (
    sessionStorage.getItem("smart_water_token") ||
    localStorage.getItem("smart_water_token") ||
    ""
  );
}

function getAuthHeaders() {
  const headers = { "Content-Type": "application/json" };
  const token = getStoredToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

function switchTab(tabId, btn) {
  document.querySelectorAll(".tab-pane").forEach((el) => (el.style.display = "none"));
  document.querySelectorAll(".nav-tab-btn").forEach((el) => el.classList.remove("active"));
  const target = document.getElementById(tabId);
  if (target) target.style.display = "block";
  if (btn) {
    btn.classList.add("active");
  } else {
    // Find matching nav button by data-tab
    const matchingBtn = document.querySelector(`[data-action="switch-tab"][data-tab="${tabId}"]`);
    if (matchingBtn) matchingBtn.classList.add("active");
  }
  if (tabId === "tab-history") {
    fetchHistoricalCharts(currentChartWindow);
  }
}

function saveTokenToBrowser() {
  const val = document.getElementById("input-auth-token")?.value.trim() || "";
  if (!val) {
    alert("Please enter your token.");
    return;
  }
  const remember = document.getElementById("chk-remember-token")?.checked;
  sessionStorage.setItem("smart_water_token", val);
  if (remember) {
    localStorage.setItem("smart_water_token", val);
  } else {
    localStorage.removeItem("smart_water_token");
  }
  alert("Token saved in browser session" + (remember ? " and persistent localStorage." : "."));
}

async function downloadBackup() {
  const btn = document.getElementById("btn-download-backup");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch("/api/backup", {
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert("Failed to download backup: " + (err.detail || err.message || res.statusText));
      return;
    }
    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `smart_water_backup_${new Date().toISOString().replace(/[:.]/g, "-")}.db`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  } catch (err) {
    console.error("Backup download failed:", err);
    alert("Error downloading backup: " + err);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Connect SSE Live Telemetry
let evtSource = null;
function connectSSE() {
  evtSource = new EventSource("/api/telemetry/stream");
  evtSource.addEventListener("telemetry", (e) => {
    try {
      const state = JSON.parse(e.data);
      updateDashboard(state);
      if (window.CircuitSchematic) window.CircuitSchematic.updateTelemetry(state);
    } catch (err) {
      console.error("SSE parse error:", err);
    }
  });
  evtSource.onerror = () => {
    evtSource.close();
    setTimeout(connectSSE, 3000);
  };
}

function updateDashboard(state) {
  if (!state) return;
  const he = state.head_end || {};
  const motor = state.motor || {};
  const garden = state.garden || {};
  const alarms = state.alarms || {};

  // System Status Badge
  const statusBadge = document.getElementById("badge-system-status");
  if (statusBadge) {
    statusBadge.textContent = state.system_status || "SAFE-NO-HARDWARE";
    if (state.system_status === "ALARM") statusBadge.className = "badge badge-red";
    else if (state.system_status === "OK") statusBadge.className = "badge badge-green";
    else statusBadge.className = "badge badge-gray";
  }

  // Interlock Badge
  const ilBadge = document.getElementById("badge-interlock-status");
  if (ilBadge) {
    ilBadge.textContent = `Interlock: ${(he.interlock_cause || "unconfigured").toUpperCase()}`;
  }
  const gaugeIl = document.getElementById("gauge-interlock-cause");
  if (gaugeIl) {
    gaugeIl.textContent = (he.interlock_cause || "UNCONFIGURED").toUpperCase();
  }

  // Garden link badge
  const glBadge = document.getElementById("badge-garden-link");
  if (glBadge) {
    glBadge.textContent = garden.node_online && !garden.is_stale ? "Garden Node: Online" : "Garden Node: Disconnected";
    glBadge.className = garden.node_online && !garden.is_stale ? "badge badge-green" : "badge badge-gray";
  }

  const uptimeEl = document.getElementById("badge-uptime");
  if (uptimeEl) {
    uptimeEl.textContent = `Uptime: ${state.uptime_seconds || 0}s`;
  }

  // Alarms Banner
  const alarmKeys = Object.keys(alarms);
  const banner = document.getElementById("alarms-banner");
  if (banner) {
    if (alarmKeys.length > 0) {
      banner.style.display = "block";
      const alarmsText = document.getElementById("alarms-text");
      if (alarmsText) alarmsText.textContent = Object.values(alarms).join(" • ");
    } else {
      banner.style.display = "none";
    }
  }

  // Tank low text
  const tankLowEl = document.getElementById("gauge-tank-low-text");
  if (tankLowEl) {
    if (he.tank_low_float === null || he.is_stale) {
      tankLowEl.innerHTML = `Low Float: <span style="color:#94a3b8;">No reading yet</span>`;
    } else {
      tankLowEl.innerHTML = `Low Float: <strong style="color:${he.tank_low_float ? "#f59e0b" : "#10b981"}">${he.tank_low_float ? "LOW (Needs water)" : "NORMAL"}</strong>`;
    }
  }

  // Motor status
  const motorBadge = document.getElementById("gauge-motor-badge");
  const ctrlMotorBadge = document.getElementById("ctrl-motor-state-badge");
  if (motorBadge) motorBadge.textContent = motor.state || "OFF";
  if (ctrlMotorBadge) ctrlMotorBadge.textContent = motor.state || "OFF";
  if (motor.state === "RUNNING") {
    if (motorBadge) motorBadge.className = "badge badge-amber";
    if (ctrlMotorBadge) ctrlMotorBadge.className = "badge badge-amber";
  } else {
    if (motorBadge) motorBadge.className = "badge badge-gray";
    if (ctrlMotorBadge) ctrlMotorBadge.className = "badge badge-gray";
  }
  const motorRunEl = document.getElementById("gauge-motor-run-s");
  if (motorRunEl) motorRunEl.textContent = `${motor.current_run_seconds || 0}s`;

  const motorAutoEl = document.getElementById("gauge-motor-auto-text");
  if (motorAutoEl) motorAutoEl.textContent = `Auto-Replenish: ${motor.auto_mode ? "ENABLED" : "DISABLED"}`;

  // Reservoir float
  const resEl = document.getElementById("gauge-reservoir-text");
  if (resEl) {
    if (garden.reservoir_wet === null || garden.is_stale) {
      resEl.innerHTML = `Reservoir: <span style="color:#94a3b8;">No reading yet</span>`;
    } else {
      resEl.innerHTML = `Reservoir: <strong style="color:${garden.reservoir_wet ? "#10b981" : "#ef4444"}">${garden.reservoir_wet ? "AMPLE" : "DRY-RUN LOCKED"}</strong>`;
    }
  }

  // Zones Telemetry
  const z = garden.zones || {};
  for (let i = 1; i <= 4; i++) {
    const zk = `zone_${i}`;
    const zd = z[zk] || {};
    const teleEl = document.getElementById(`tele-z${i}-val`);
    const ctrlInfo = document.getElementById(`ctrl-z${i}-info`);
    const ctrlBadge = document.getElementById(`ctrl-z${i}-badge`);

    if (zd.voltage === null || garden.is_stale) {
      if (teleEl) teleEl.innerHTML = `<span style="color:#94a3b8;">No reading yet</span>`;
      if (ctrlInfo) ctrlInfo.textContent = "Reading: No reading yet";
    } else {
      const moistStr = zd.moisture_pct !== null ? `${zd.moisture_pct}%` : "Uncalibrated";
      if (teleEl) teleEl.innerHTML = `<strong>${escapeHtml(moistStr)}</strong> (${escapeHtml(zd.voltage)}V)`;
      if (ctrlInfo) ctrlInfo.textContent = `Moisture: ${moistStr} (${zd.voltage}V)`;
    }

    if (ctrlBadge) {
      ctrlBadge.textContent = zd.pump_running ? "RUNNING" : "OFF";
      ctrlBadge.className = zd.pump_running ? "badge badge-blue" : "badge badge-gray";
    }
  }

  // Wizard banner visibility check
  fetch("/healthz")
    .then((r) => r.json())
    .then((h) => {
      const wizBanner = document.getElementById("wizard-alert-banner");
      if (wizBanner) wizBanner.style.display = h.setup_complete ? "none" : "block";
    })
    .catch(() => {});
}

// Motor Actions
async function manualStartMotor() {
  const confirmChecked = document.getElementById("chk-confirm-motor")?.checked;
  if (!confirmChecked) {
    alert("Please confirm that water line is clear and safe.");
    return;
  }
  try {
    const res = await fetch("/api/motor/start", {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ confirm: true }),
    });
    const data = await res.json();
    if (!res.ok) alert(data.detail || data.message);
  } catch (e) {
    console.error(e);
  }
}

async function manualStopMotor() {
  try {
    await fetch("/api/motor/stop", { method: "POST", headers: getAuthHeaders() });
  } catch (e) {
    console.error(e);
  }
}

async function resetMotorLockout() {
  try {
    const res = await fetch("/api/motor/reset", { method: "POST", headers: getAuthHeaders() });
    const data = await res.json();
    if (!res.ok) alert(data.detail || data.message);
  } catch (e) {
    console.error(e);
  }
}

// Zone Irrigation Actions
async function triggerZoneWater(zoneId, durationSec = 15) {
  try {
    const res = await fetch(`/api/zones/${zoneId}/water`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ duration_seconds: Number(durationSec) || 15 }),
    });
    const data = await res.json();
    if (!res.ok) alert(data.detail || data.message);
  } catch (e) {
    console.error(e);
  }
}

async function stopZoneWater(zoneId) {
  try {
    await fetch(`/api/zones/${zoneId}/stop`, { method: "POST", headers: getAuthHeaders() });
  } catch (e) {
    console.error(e);
  }
}

async function calibrateProbe(zoneId, step) {
  if (!confirm(`Confirm probe is ready for ${step.toUpperCase()} calibration (${step === "dry" ? "in air" : "submerged in water"})?`)) return;
  try {
    const res = await fetch(`/api/zones/${zoneId}/calibrate`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ step: step }),
    });
    const data = await res.json();
    if (res.ok) {
      alert(`Calibration point for ${zoneId} (${step}) recorded at ${data.captured_volts}V.`);
    } else {
      alert(data.detail || data.message);
    }
  } catch (e) {
    console.error(e);
  }
}

async function calibrateFlow(zone) {
  const btn = document.getElementById("btn-calibrate-flow");
  const resDiv = document.getElementById("flow-cal-result");
  if (!btn || !resDiv) return;
  if (!confirm("Run Zone 1 pump for 10s to calibrate flow meter?")) return;
  btn.disabled = true;
  btn.innerText = "Calibrating (10s)...";
  resDiv.style.display = "block";
  resDiv.innerText = "Running pump for 10s and measuring pulses...";
  resDiv.style.color = "#0284c7";
  try {
    const res = await fetch("/api/flow/calibrate", {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ zone: Number(zone) || 1 }),
    });
    const data = await res.json();
    if (res.ok && data.status === "success") {
      resDiv.innerText = `Flow: ${data.observed_flow_lpm} L/min (${data.observed_pulses_10s} pulses). Cutoff: ${data.flow_min_lpm} L/min. Saved!`;
      resDiv.style.color = "#16a34a";
    } else {
      resDiv.innerText = data.warning || data.detail || "Calibration failed";
      resDiv.style.color = "#dc2626";
    }
  } catch (err) {
    resDiv.innerText = "Error: " + err;
    resDiv.style.color = "#dc2626";
  } finally {
    btn.disabled = false;
    btn.innerText = "Calibrate Zone 1 Flow";
  }
}

async function triggerEmergencyStop() {
  if (!confirm("EMERGENCY: Kill all pumps and contactor immediately?")) return;
  try {
    await fetch("/api/control/emergency-stop", { method: "POST", headers: getAuthHeaders() });
  } catch (e) {
    console.error(e);
  }
}

async function testVoiceAlert() {
  const text = document.getElementById("test-voice-input")?.value.trim();
  if (!text) return;
  try {
    await fetch("/api/control/speak", {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ text }),
    });
  } catch (e) {
    console.error(e);
  }
}

// Wizard Functions
function nextWizardStep(stepNum) {
  document.querySelectorAll(".wizard-step").forEach((el) => el.classList.remove("active"));
  const target = document.getElementById(`wstep-${stepNum}`);
  if (target) target.classList.add("active");
  const progBadge = document.getElementById("wizard-progress-badge");
  if (progBadge) progBadge.textContent = `Step ${stepNum} of 7`;

  if (stepNum === 2) loadGpioinfo();
  if (stepNum === 3) pollWizardInputs();
}

async function loadGpioinfo() {
  const box = document.getElementById("wizard-gpioinfo-box");
  if (!box) return;
  try {
    const res = await fetch("/api/gpioinfo");
    const data = await res.json();
    box.textContent = data.output || "No output returned.";
  } catch (e) {
    box.textContent = "Error executing gpioinfo.";
  }
}

async function pollWizardInputs(updatePayload = null) {
  try {
    const opts = { method: "POST", headers: getAuthHeaders() };
    if (updatePayload) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(updatePayload);
    }
    const res = await fetch("/api/setup/test-input", opts);
    const data = await res.json();
    const inputs = data.inputs || {};
    const raw = inputs.raw || {};
    const rawLevels = raw.raw_levels || {};
    const pols = data.polarities || {};

    // IN1: Tank Low Float
    const r1 = rawLevels.tank_low_float;
    const d1 = inputs.tank_low_float !== undefined ? inputs.tank_low_float : inputs.tank_low;
    const raw1El = document.getElementById("wraw-in1");
    if (raw1El) raw1El.textContent = r1 === null || r1 === undefined ? "--" : r1 === 0 ? "0 (LOW)" : "1 (HIGH)";
    const test1El = document.getElementById("wtest-in1");
    if (test1El) {
      test1El.innerHTML =
        d1 === null
          ? '<span style="color:#94a3b8">Unconfigured</span>'
          : d1
          ? '<span style="color:#dc2626; font-weight:700;">TRIPPED (Water Low)</span>'
          : '<span style="color:#16a34a; font-weight:700;">NORMAL (Water OK)</span>';
    }
    const pol1El = document.getElementById("wpol-in1");
    if (pol1El && pols.tank_low_active_low !== undefined) pol1El.checked = pols.tank_low_active_low;

    // IN2: Interlock Out
    const r2 = rawLevels.interlock_out;
    const d2 = inputs.interlock_out;
    const raw2El = document.getElementById("wraw-in2");
    if (raw2El) raw2El.textContent = r2 === null || r2 === undefined ? "--" : r2 === 0 ? "0 (LOW)" : "1 (HIGH)";
    const test2El = document.getElementById("wtest-in2");
    if (test2El) {
      test2El.innerHTML =
        d2 === null
          ? '<span style="color:#94a3b8">Unconfigured</span>'
          : d2
          ? '<span style="color:#16a34a; font-weight:700;">CLOSED (Chain Healthy)</span>'
          : '<span style="color:#dc2626; font-weight:700;">OPEN (Chain Broken)</span>';
    }
    const pol2El = document.getElementById("wpol-in2");
    if (pol2El && pols.interlock_out_active_low !== undefined) pol2El.checked = pols.interlock_out_active_low;

    // IN3: Interlock Mid
    const r3 = rawLevels.interlock_mid;
    const d3 = inputs.interlock_mid;
    const raw3El = document.getElementById("wraw-in3");
    if (raw3El) raw3El.textContent = r3 === null || r3 === undefined ? "--" : r3 === 0 ? "0 (LOW)" : "1 (HIGH)";
    const test3El = document.getElementById("wtest-in3");
    if (test3El) {
      test3El.innerHTML =
        d3 === null
          ? '<span style="color:#94a3b8">Unconfigured</span>'
          : d3
          ? '<span style="color:#16a34a; font-weight:700;">CLOSED (Mid-tap OK)</span>'
          : '<span style="color:#dc2626; font-weight:700;">OPEN (Mid-tap Open)</span>';
    }
    const pol3El = document.getElementById("wpol-in3");
    if (pol3El && pols.interlock_mid_active_low !== undefined) pol3El.checked = pols.interlock_mid_active_low;

    // IN4: Leak Sensor
    const r4 = rawLevels.leak_sensor;
    const d4 = inputs.leak_sensor !== undefined ? inputs.leak_sensor : inputs.leak_detected;
    const raw4El = document.getElementById("wraw-in4");
    if (raw4El) raw4El.textContent = r4 === null || r4 === undefined ? "--" : r4 === 0 ? "0 (LOW)" : "1 (HIGH)";
    const test4El = document.getElementById("wtest-in4");
    if (test4El) {
      test4El.innerHTML =
        d4 === null
          ? '<span style="color:#94a3b8">Unconfigured</span>'
          : d4
          ? '<span style="color:#dc2626; font-weight:700;">ALARM (Leak Detected)</span>'
          : '<span style="color:#16a34a; font-weight:700;">NORMAL (Dry)</span>';
    }
    const pol4El = document.getElementById("wpol-in4");
    if (pol4El && pols.leak_active_low !== undefined) pol4El.checked = pols.leak_active_low;
  } catch (e) {
    console.error(e);
  }
}

async function flipWizardPolarity(field, val) {
  const payload = {};
  payload[field] = val;
  await pollWizardInputs(payload);
}

async function testRelayCycle() {
  const confirmed = document.getElementById("wchk-motor-disconnected")?.checked;
  if (!confirmed) {
    alert("You must confirm that the motor is physically disconnected before testing.");
    return;
  }
  try {
    const res = await fetch("/api/setup/test-relay", {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ motor_disconnected_confirmed: true }),
    });
    const data = await res.json();
    alert(data.message || data.detail);
  } catch (e) {
    console.error(e);
  }
}

async function finalizeSetupWizard() {
  const confirmed = document.getElementById("wchk-complete-confirm")?.checked;
  if (!confirmed) {
    alert("You must certify that tests have passed.");
    return;
  }
  try {
    const res = await fetch("/api/setup/complete", {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ confirm_all_passed: true }),
    });
    if (res.ok) {
      alert("Commissioning completed! Controller is now in live operation.");
      const wizBanner = document.getElementById("wizard-alert-banner");
      if (wizBanner) wizBanner.style.display = "none";
      switchTab("tab-circuit", document.getElementById("btn-nav-circuit"));
    } else {
      const err = await res.json();
      alert(err.detail);
    }
  } catch (e) {
    console.error(e);
  }
}

async function fetchEventJournal() {
  try {
    const res = await fetch("/api/events?limit=50", { headers: getAuthHeaders() });
    const data = await res.json();
    const container = document.getElementById("events-journal-container");
    if (!container) return;
    const items = data.data || [];
    if (items.length === 0) {
      container.innerHTML = `<div style="padding:1rem; color:#94a3b8; text-align:center;">No events logged yet. Database is clean.</div>`;
      return;
    }
    container.innerHTML = items
      .map(
        (ev) => `
      <div style="padding: 0.4rem 0.6rem; border-bottom: 1px solid #f1f5f9;">
        <span class="badge ${ev.severity === "CRITICAL" ? "badge-red" : "badge-gray"}">${escapeHtml(ev.severity)}</span>
        <span style="font-weight:600; margin-left:6px;">${escapeHtml(ev.event_type)}</span>: ${escapeHtml(ev.message)}
        <span style="color:#94a3b8; font-size:10px; margin-left:6px;">${escapeHtml(ev.timestamp)}</span>
      </div>
    `
      )
      .join("");
  } catch (e) {
    console.error(e);
  }
}

async function loadSettingsYaml() {
  try {
    const res = await fetch("/api/config", { headers: getAuthHeaders() });
    const data = await res.json();
    const preview = document.getElementById("settings-yaml-preview");
    if (preview) preview.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    console.error(e);
  }
}

function saveSettingsForm() {
  alert("Configuration file is managed on host at /etc/smart-water/config.yaml.");
}

// =============================================================================
// Lightweight Pure SVG Charting Engine (Zero External Dependencies)
// =============================================================================
let currentChartWindow = "24h";

function renderSvgLineChart(containerId, seriesList, options = {}) {
  const container = document.getElementById(containerId);
  if (!container) return;

  const width = options.width || 560;
  const height = options.height || 200;
  const pad = { top: 20, right: 25, bottom: 30, left: 45 };

  let allPoints = [];
  seriesList.forEach((s) => {
    if (Array.isArray(s.data)) {
      s.data.forEach((pt) => {
        if (pt.y !== null && pt.y !== undefined && !isNaN(pt.y)) {
          allPoints.push(pt);
        }
      });
    }
  });

  if (allPoints.length === 0) {
    container.innerHTML = '<div style="padding: 2.5rem; color: #94a3b8; font-size: 12px; text-align: center;">No sensor telemetry recorded in this time window yet (logs downsampled every 60s).</div>';
    return;
  }

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = options.yMin !== undefined ? options.yMin : Infinity;
  let maxY = options.yMax !== undefined ? options.yMax : -Infinity;

  allPoints.forEach((pt) => {
    if (pt.x < minX) minX = pt.x;
    if (pt.x > maxX) maxX = pt.x;
    if (options.yMin === undefined && pt.y < minY) minY = pt.y;
    if (options.yMax === undefined && pt.y > maxY) maxY = pt.y;
  });

  if (minX === maxX) {
    minX -= 60000;
    maxX += 60000;
  }
  if (minY === maxY) {
    minY -= 1;
    maxY += 1;
  }
  if (minY === Infinity) minY = 0;
  if (maxY === -Infinity) maxY = 1;

  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  function toX(val) {
    return pad.left + ((val - minX) / (maxX - minX)) * plotW;
  }
  function toY(val) {
    return pad.top + plotH - ((val - minY) / (maxY - minY)) * plotH;
  }

  const svgParts = [];
  svgParts.push(`<svg viewBox="0 0 ${width} ${height}" style="width: 100%; height: auto; display: block; overflow: visible;">`);

  // Grid lines & Y labels (horizontal grid lines)
  const steps = 3;
  for (let i = 0; i <= steps; i++) {
    const yVal = minY + (i / steps) * (maxY - minY);
    const yPx = toY(yVal);
    svgParts.push(`<line x1="${pad.left}" y1="${yPx.toFixed(1)}" x2="${width - pad.right}" y2="${yPx.toFixed(1)}" stroke="#f1f5f9" stroke-width="1" />`);
    const label = (options.yDecimals !== undefined ? yVal.toFixed(options.yDecimals) : yVal.toFixed(1)) + (options.yUnit || "");
    svgParts.push(`<text x="${pad.left - 6}" y="${(yPx + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="#94a3b8" font-family="sans-serif">${escapeHtml(label)}</text>`);
  }

  // X labels (start and end timestamps)
  const startDate = new Date(minX);
  const endDate = new Date(maxX);
  const startStr = `${String(startDate.getHours()).padStart(2, '0')}:${String(startDate.getMinutes()).padStart(2, '0')}`;
  const endStr = `${String(endDate.getHours()).padStart(2, '0')}:${String(endDate.getMinutes()).padStart(2, '0')}`;
  svgParts.push(`<text x="${pad.left}" y="${height - 8}" text-anchor="start" font-size="9" fill="#94a3b8" font-family="sans-serif">${startStr}</text>`);
  svgParts.push(`<text x="${width - pad.right}" y="${height - 8}" text-anchor="end" font-size="9" fill="#94a3b8" font-family="sans-serif">${endStr}</text>`);

  // Render each series line
  seriesList.forEach((s) => {
    if (!Array.isArray(s.data) || s.data.length === 0) return;
    const pts = s.data.filter((pt) => pt.y !== null && pt.y !== undefined && !isNaN(pt.y));
    if (pts.length === 0) return;

    let pathD = "";
    if (s.stepLine) {
      pts.forEach((pt, idx) => {
        const cx = toX(pt.x).toFixed(1);
        const cy = toY(pt.y).toFixed(1);
        if (idx === 0) {
          pathD += `M ${cx} ${cy}`;
        } else {
          pathD += ` H ${cx} V ${cy}`;
        }
      });
    } else {
      pts.forEach((pt, idx) => {
        const cx = toX(pt.x).toFixed(1);
        const cy = toY(pt.y).toFixed(1);
        pathD += (idx === 0 ? "M " : " L ") + `${cx} ${cy}`;
      });
    }

    svgParts.push(`<path d="${pathD}" fill="none" stroke="${s.color || '#0284c7'}" stroke-width="${s.strokeWidth || 2}" stroke-linecap="round" stroke-linejoin="round" />`);

    if (pts.length <= 40 && !s.stepLine) {
      pts.forEach((pt) => {
        svgParts.push(`<circle cx="${toX(pt.x).toFixed(1)}" cy="${toY(pt.y).toFixed(1)}" r="2.5" fill="${s.color || '#0284c7'}" />`);
      });
    }
  });

  svgParts.push("</svg>");
  container.innerHTML = svgParts.join("");
}

async function fetchHistoricalCharts(windowStr = "24h") {
  const hoursMap = { "1h": 1, "6h": 6, "24h": 24, "48h": 48 };
  const hours = hoursMap[windowStr] || 24;
  const fromDate = new Date(Date.now() - hours * 3600 * 1000).toISOString();

  // 1. Fetch 24h Summary data
  fetch(`/api/summary/daily?hours=${hours}`, { headers: getAuthHeaders() })
    .then((r) => r.json())
    .then((resp) => {
      if (resp && resp.status === "success" && resp.data) {
        updateDailySummaryCard(resp.data);
      }
    })
    .catch((err) => console.warn("Summary fetch error:", err));

  // 2. Fetch readings history
  try {
    const res = await fetch(`/api/history?limit=300&from=${encodeURIComponent(fromDate)}`, {
      headers: getAuthHeaders(),
    });
    if (!res.ok) return;
    const json = await res.json();
    const rows = (json.data || []).reverse(); // oldest first for chronological plotting

    const z1 = [];
    const z2 = [];
    const z3 = [];
    const z4 = [];
    const motorPts = [];
    const flowPts = [];
    const tankPts = [];

    rows.forEach((row) => {
      const t = new Date(row.timestamp).getTime();
      if (isNaN(t)) return;
      if (row.z1_volts !== null && row.z1_volts !== undefined) z1.push({ x: t, y: row.z1_volts });
      if (row.z2_volts !== null && row.z2_volts !== undefined) z2.push({ x: t, y: row.z2_volts });
      if (row.z3_volts !== null && row.z3_volts !== undefined) z3.push({ x: t, y: row.z3_volts });
      if (row.z4_volts !== null && row.z4_volts !== undefined) z4.push({ x: t, y: row.z4_volts });

      if (row.motor_running !== null && row.motor_running !== undefined) {
        motorPts.push({ x: t, y: row.motor_running });
      }
      if (row.z1_flow !== null && row.z1_flow !== undefined) {
        flowPts.push({ x: t, y: row.z1_flow });
      }
      if (row.tank_low !== null && row.tank_low !== undefined) {
        tankPts.push({ x: t, y: row.tank_low === 0 ? 100 : 20 });
      }
    });

    renderSvgLineChart("chart-soil-container", [
      { name: "Zone 1", color: "#0284c7", data: z1 },
      { name: "Zone 2", color: "#10b981", data: z2 },
      { name: "Zone 3", color: "#f59e0b", data: z3 },
      { name: "Zone 4", color: "#8b5cf6", data: z4 },
    ], { yUnit: "V", yMin: 0.0, yMax: 3.3, yDecimals: 2 });

    renderSvgLineChart("chart-motor-container", [
      { name: "Motor State", color: "#0284c7", data: motorPts, stepLine: true, strokeWidth: 2.5 },
      { name: "Flow Pulses", color: "#3b82f6", data: flowPts },
    ], { yMin: 0, yMax: 1, yDecimals: 0 });

    fetchLevelConsumptionChart(hours, tankPts);
  } catch (err) {
    console.error("Historical chart fetch failed:", err);
  }
}

async function fetchLevelConsumptionChart(hours, fallbackTankPts) {
  try {
    const res = await fetch(`/api/level/consumption?hours=${hours}`, { headers: getAuthHeaders() });
    if (!res.ok) {
      renderSvgLineChart("chart-level-container", [
        { name: "Water Level", color: "#0284c7", data: fallbackTankPts },
      ], { yUnit: "%", yMin: 0, yMax: 100, yDecimals: 0 });
      return;
    }
    const resp = await res.json();
    const hourly = resp.data?.hourly_usage || [];
    const levelPts = [];
    const consPts = [];

    hourly.forEach((item) => {
      const t = new Date(item.hour).getTime();
      if (!isNaN(t)) {
        if (item.litres !== null && item.litres !== undefined) consPts.push({ x: t, y: item.litres });
        if (item.level_pct !== null && item.level_pct !== undefined) levelPts.push({ x: t, y: item.level_pct });
      }
    });

    const series = [];
    if (levelPts.length > 0) {
      series.push({ name: "Water Level %", color: "#0284c7", data: levelPts });
    } else if (fallbackTankPts && fallbackTankPts.length > 0) {
      series.push({ name: "Water Level %", color: "#0284c7", data: fallbackTankPts });
    }
    if (consPts.length > 0) {
      series.push({ name: "Hourly Consumption (L)", color: "#10b981", data: consPts });
    }

    renderSvgLineChart("chart-level-container", series, { yDecimals: 1 });
  } catch (err) {
    renderSvgLineChart("chart-level-container", [
      { name: "Water Level", color: "#0284c7", data: fallbackTankPts },
    ], { yUnit: "%", yMin: 0, yMax: 100, yDecimals: 0 });
  }
}

function updateDailySummaryCard(data) {
  if (!data) return;
  const m = data.motor || {};
  const elRuntime = document.getElementById("summary-motor-runtime");
  if (elRuntime) elRuntime.textContent = `${m.runtime_s || 0}s`;
  const elStarts = document.getElementById("summary-motor-starts");
  if (elStarts) elStarts.textContent = `${m.starts || 0} starts`;
  const elPumped = document.getElementById("summary-water-pumped");
  if (elPumped) elPumped.textContent = `~${m.est_litres || 0} L`;
  const elEnergy = document.getElementById("summary-motor-energy");
  if (elEnergy) elEnergy.textContent = `${m.energy_kwh || 0} kWh`;

  const zones = data.zones || {};
  let totalIrrL = 0;
  let totalRuns = 0;
  Object.values(zones).forEach((z) => {
    totalIrrL += z.litres || 0;
    totalRuns += z.runs || 0;
  });
  const elIrr = document.getElementById("summary-irrigation-total");
  if (elIrr) elIrr.textContent = `${totalIrrL.toFixed(1)} L`;
  const elIrrRuns = document.getElementById("summary-irrigation-runs");
  if (elIrrRuns) elIrrRuns.textContent = `${totalRuns} total cycles`;

  const alarms = data.alarms || [];
  const elAlarms = document.getElementById("summary-alarms-count");
  if (elAlarms) {
    elAlarms.textContent = alarms.length;
    elAlarms.style.color = alarms.length > 0 ? "#dc2626" : "#0f172a";
  }
  const elAlarmStatus = document.getElementById("summary-alarms-status");
  if (elAlarmStatus) {
    elAlarmStatus.textContent = alarms.length > 0 ? `${alarms.length} safety trips logged` : "All interlocks healthy";
    elAlarmStatus.style.color = alarms.length > 0 ? "#dc2626" : "#10b981";
  }
}

async function triggerDailySummarySend() {
  try {
    const res = await fetch("/api/summary/daily/send?hours=24", {
      method: "POST",
      headers: getAuthHeaders(),
    });
    if (res.ok) {
      alert("24-Hour summary digest dispatched to Telegram and ntfy!");
    } else {
      const err = await res.json().catch(() => ({}));
      alert("Failed to send summary: " + (err.message || res.statusText));
    }
  } catch (err) {
    alert("Error sending daily summary: " + err);
  }
}

// Global Delegated Event Listeners (Strict CSP compliant)
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;

  const action = btn.getAttribute("data-action");

  switch (action) {
    case "emergency-stop":
      triggerEmergencyStop();
      break;
    case "reset-lockout":
      resetMotorLockout();
      break;
    case "switch-tab": {
      const tabId = btn.getAttribute("data-tab");
      if (tabId) switchTab(tabId, btn);
      break;
    }
    case "motor-start":
      manualStartMotor();
      break;
    case "motor-stop":
      manualStopMotor();
      break;
    case "zone-water": {
      const zone = btn.getAttribute("data-zone");
      const duration = btn.getAttribute("data-duration") || 15;
      if (zone) triggerZoneWater(zone, duration);
      break;
    }
    case "zone-stop": {
      const zone = btn.getAttribute("data-zone");
      if (zone) stopZoneWater(zone);
      break;
    }
    case "probe-calibrate": {
      const zone = btn.getAttribute("data-zone");
      const step = btn.getAttribute("data-step");
      if (zone && step) calibrateProbe(zone, step);
      break;
    }
    case "flow-calibrate": {
      const zone = btn.getAttribute("data-zone") || 1;
      calibrateFlow(zone);
      break;
    }
    case "speak":
      testVoiceAlert();
      break;
    case "save-token":
      saveTokenToBrowser();
      break;
    case "wizard-step": {
      const step = Number(btn.getAttribute("data-step"));
      if (step) nextWizardStep(step);
      break;
    }
    case "poll-wizard-inputs":
      pollWizardInputs();
      break;
    case "test-relay-cycle":
      testRelayCycle();
      break;
    case "finalize-wizard":
      finalizeSetupWizard();
      break;
    case "save-settings":
      saveSettingsForm();
      break;
    case "refresh-events":
      fetchEventJournal();
      break;
    case "download-backup":
      downloadBackup();
      break;
    case "rules-clear":
      if (window.RulesBuilder) window.RulesBuilder.resetEditorForm();
      break;
    case "rules-add-trigger":
      if (window.RulesBuilder) window.RulesBuilder.addConditionRow();
      break;
    case "rules-add-action":
      if (window.RulesBuilder) window.RulesBuilder.addActionRow();
      break;
    case "rules-refresh":
      if (window.RulesBuilder) window.RulesBuilder.fetchRules();
      break;
    case "set-chart-window": {
      const win = btn.getAttribute("data-window") || "24h";
      document.querySelectorAll(".chart-window-btn").forEach((b) => {
        b.className = b === btn ? "btn btn-primary btn-sm chart-window-btn" : "btn btn-secondary btn-sm chart-window-btn";
      });
      currentChartWindow = win;
      fetchHistoricalCharts(win);
      break;
    }
    case "refresh-charts":
      fetchHistoricalCharts(currentChartWindow);
      break;
    case "send-daily-summary":
      triggerDailySummarySend();
      break;
    default:
      break;
  }
});

// Change listeners for polarity checkboxes
document.addEventListener("change", (e) => {
  const target = e.target;
  if (target && target.getAttribute("data-action") === "flip-polarity") {
    const field = target.getAttribute("data-field");
    if (field) {
      flipWizardPolarity(field, target.checked);
    }
  }
});

document.addEventListener("DOMContentLoaded", () => {
  // Populate token from storage if present
  const savedToken = getStoredToken();
  if (savedToken) {
    const tokenInput = document.getElementById("input-auth-token");
    if (tokenInput) tokenInput.value = savedToken;
    if (localStorage.getItem("smart_water_token")) {
      const chk = document.getElementById("chk-remember-token");
      if (chk) chk.checked = true;
    }
  }

  connectSSE();
  fetchEventJournal();
  loadSettingsYaml();
});
