/**
 * Hybrid Circuit Schematic Visualizer for Radxa ROCK Pi S + Wireless ESP32 Garden Node.
 * Driven EXCLUSIVELY by live telemetry. Zero simulated or fabricated states.
 * Missing or unconfigured components are displayed grey with "Not configured".
 */

window.CircuitSchematic = {
  tooltipEl: null,

  init() {
    this.tooltipEl = document.getElementById("circuit-tooltip");
    this.attachNodeListeners();
    console.log("Hybrid CircuitSchematic visualizer initialized.");
  },

  attachNodeListeners() {
    const nodes = document.querySelectorAll("[data-circuit-node]");
    nodes.forEach((el) => {
      el.addEventListener("mouseenter", (e) => this.showTooltip(e, el));
      el.addEventListener("mousemove", (e) => this.moveTooltip(e));
      el.addEventListener("mouseleave", () => this.hideTooltip());
      el.addEventListener("click", (e) => this.showTooltip(e, el, true));
    });
  },

  showTooltip(e, el) {
    if (!this.tooltipEl) return;
    const title = el.getAttribute("data-node-title") || "Component";
    const pin = el.getAttribute("data-node-pin") || "Not configured";
    const desc = el.getAttribute("data-node-desc") || "";
    const state = el.getAttribute("data-node-state") || "No reading yet";

    this.tooltipEl.innerHTML = `
      <div style="font-weight: 700; color: #38bdf8; margin-bottom: 2px;">${title}</div>
      <div style="font-family: monospace; font-size: 11px; color: #94a3b8; margin-bottom: 4px;">Hardware: ${pin}</div>
      <div style="font-size: 11px; margin-bottom: 4px;">${desc}</div>
      <div style="display: inline-block; font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 4px; background: #1e293b; color: #e2e8f0;">State: ${state}</div>
    `;
    this.tooltipEl.style.display = "block";
    this.moveTooltip(e);
  },

  moveTooltip(e) {
    if (!this.tooltipEl) return;
    const offset = 15;
    this.tooltipEl.style.left = `${e.pageX + offset}px`;
    this.tooltipEl.style.top = `${e.pageY + offset}px`;
  },

  hideTooltip() {
    if (!this.tooltipEl) return;
    this.tooltipEl.style.display = "none";
  },

  /**
   * Updates hybrid topology visuals solely from live telemetry diffs.
   */
  updateTelemetry(state) {
    if (!state) return;

    const he = state.head_end || {};
    const motor = state.motor || {};
    const garden = state.garden || {};
    const zones = garden.zones || {};

    // 1. Head-End PC817 Optocoupler Inputs
    const tankLow = he.tank_low_float;
    const interlockOut = he.interlock_out;
    const interlockMid = he.interlock_mid;
    const leak = he.leak_detected;
    const relayOn = he.contactor_relay;
    const heStale = he.is_stale;

    // IN1: Tank Low Float
    if (tankLow === null || heStale) {
      this.setPinIndicator("pin-in1", "#94a3b8", "No reading yet / Stale");
    } else {
      this.setPinIndicator("pin-in1", tankLow ? "#f59e0b" : "#10b981", tankLow ? "Tripped (Tank Low)" : "Normal (Covered)");
    }

    // IN2: Interlock Out (HIGH = closed)
    if (interlockOut === null || heStale) {
      this.setPinIndicator("pin-in2", "#94a3b8", "No reading yet / Stale");
    } else {
      this.setPinIndicator("pin-in2", interlockOut ? "#10b981" : "#ef4444", interlockOut ? "HIGH (Chain Closed)" : "LOW (Interlock Broken)");
    }

    // IN3: Interlock Mid
    if (interlockMid === null || heStale) {
      this.setPinIndicator("pin-in3", "#94a3b8", "No reading yet / Stale");
    } else {
      this.setPinIndicator("pin-in3", interlockMid ? "#10b981" : "#f59e0b", interlockMid ? "HIGH (Full Float Closed)" : "LOW (Full Float Open)");
    }

    // IN4: Leak Sensor
    if (leak === null || heStale) {
      this.setPinIndicator("pin-in4", "#94a3b8", "No reading yet / Stale");
    } else {
      this.setPinIndicator("pin-in4", leak ? "#ef4444" : "#10b981", leak ? "TRIPPED (Water Leak Detected)" : "Normal (Dry)");
    }

    // Contactor Relay Output
    this.setPinIndicator("pin-relay-out", relayOn ? "#f97316" : "#64748b", relayOn ? "HIGH (Energized)" : "LOW (De-energized)");
    this.toggleWire("wire-contactor-coil", relayOn);
    this.toggleImpeller("impeller-ac", relayOn);

    // Interlock Cause description
    const cause = he.interlock_cause || "unconfigured";
    this.setNodeState("node-interlock-chain", `Status: ${cause.toUpperCase()}`);

    // 2. Wireless Gateway & Link Status
    const gatewayConnected = garden.gateway_connected;
    const nodeOnline = garden.node_online;
    const gStale = garden.is_stale;

    const linkEl = document.getElementById("wire-wireless-link");
    if (linkEl) {
      if (nodeOnline && !gStale) {
        linkEl.setAttribute("stroke", "#10b981");
        linkEl.classList.add("wire-active");
      } else {
        linkEl.setAttribute("stroke", "#94a3b8");
        linkEl.classList.remove("wire-active");
      }
    }
    this.setNodeState("node-gateway", gatewayConnected ? `USB Connected (${garden.rssi ? garden.rssi + " dBm" : "Online"})` : "Not connected");
    this.setNodeState("node-garden-node", nodeOnline && !gStale ? `ESP-NOW Online (Uptime ${garden.uptime_s || 0}s)` : "Offline / Stale");

    // 3. Plant Drip Pumps
    for (let i = 1; i <= 4; i++) {
      const zKey = `zone_${i}`;
      const zData = zones[zKey] || {};
      const pumpRunning = !!zData.pump_running;
      this.toggleImpeller(`impeller-p${i}`, pumpRunning);
      this.toggleWire(`wire-zone-${i}`, pumpRunning);

      const voltsStr = zData.voltage !== null ? `${zData.voltage}V` : "No reading yet";
      const moistStr = zData.moisture_pct !== null ? `${zData.moisture_pct}%` : "Uncalibrated";
      this.setNodeState(`node-pump-${i}`, pumpRunning ? `RUNNING (${moistStr})` : `OFF (${moistStr}, ${voltsStr})`);
    }

    // 4. Reservoir Wet Float
    const resWet = garden.reservoir_wet;
    const resPin = document.getElementById("pin-res-float");
    if (resPin) {
      if (resWet === null || gStale) {
        resPin.setAttribute("fill", "#94a3b8");
        this.setNodeState("node-reservoir", "No telemetry received");
      } else if (resWet === true) {
        resPin.setAttribute("fill", "#10b981");
        this.setNodeState("node-reservoir", "Ample water (Float submerged)");
      } else {
        resPin.setAttribute("fill", "#ef4444");
        this.setNodeState("node-reservoir", "DRY-RUN LOCK (Float open)");
      }
    }
  },

  setPinIndicator(id, color, stateDesc) {
    const el = document.getElementById(id);
    if (el) {
      el.setAttribute("fill", color);
      el.setAttribute("data-node-state", stateDesc);
    }
  },

  toggleWire(id, isActive) {
    const el = document.getElementById(id);
    if (el) {
      if (isActive) {
        el.classList.add("wire-active");
      } else {
        el.classList.remove("wire-active");
      }
    }
  },

  toggleImpeller(id, isSpinning) {
    const el = document.getElementById(id);
    if (el) {
      if (isSpinning) {
        el.classList.add("impeller-rotating");
      } else {
        el.classList.remove("impeller-rotating");
      }
    }
  },

  setNodeState(id, text) {
    const el = document.getElementById(id);
    if (el) {
      el.setAttribute("data-node-state", text);
    }
  },
};

document.addEventListener("DOMContentLoaded", () => {
  window.CircuitSchematic.init();
});
