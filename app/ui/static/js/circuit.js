/**
 * Dynamic Interactive Circuit Schematic Visualizer for Radxa ROCK Pi S.
 * Controls SVG wire pulses, pin indicators, fluid levels, and impeller rotations.
 */

window.CircuitSchematic = {
  tooltipEl: null,

  init() {
    this.tooltipEl = document.getElementById("circuit-tooltip");
    this.attachNodeListeners();
    console.log("CircuitSchematic visualizer initialized.");
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

  showTooltip(e, el, isClick = false) {
    if (!this.tooltipEl) return;
    const title = el.getAttribute("data-node-title") || "Component";
    const pin = el.getAttribute("data-node-pin") || "N/A";
    const desc = el.getAttribute("data-node-desc") || "";
    const state = el.getAttribute("data-node-state") || "Idle";

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
   * Updates schematic visuals on each telemetry tick
   */
  updateTelemetry(state) {
    if (!state) return;

    const sensors = state.sensors || {};
    const actuators = state.actuators || {};
    const probes = sensors.soil_probes || {};
    const percentages = sensors.soil_percentages || {};

    // -------------------------------------------------------------------------
    // 1. Digital Pin Status Indicators
    // -------------------------------------------------------------------------
    const tankLow = !!sensors.tank_low_float;
    const tankHigh = !!sensors.tank_high_float;
    const resEmpty = !!sensors.reservoir_low_float;

    const acOn = !!actuators.ac_contactor;
    const z1On = !!actuators.zone_1_pump;
    const z2On = !!actuators.zone_2_pump;
    const z3On = !!actuators.zone_3_pump;
    const z4On = !!actuators.zone_4_pump;

    this.setPinIndicator("pin-10", tankLow ? "#f59e0b" : "#10b981", tankLow ? "Tripped (Low)" : "Normal");
    this.setPinIndicator("pin-11", tankHigh ? "#0284c7" : "#10b981", tankHigh ? "Tripped (Full)" : "Normal");
    this.setPinIndicator("pin-12", resEmpty ? "#ef4444" : "#10b981", resEmpty ? "Tripped (Empty)" : "OK");

    this.setPinIndicator("pin-13", acOn ? "#f97316" : "#64748b", acOn ? "HIGH (ON)" : "LOW (OFF)");
    this.setPinIndicator("pin-15", z1On ? "#0284c7" : "#64748b", z1On ? "HIGH (ON)" : "LOW (OFF)");
    this.setPinIndicator("pin-22", z2On ? "#0284c7" : "#64748b", z2On ? "HIGH (ON)" : "LOW (OFF)");
    this.setPinIndicator("pin-23", z3On ? "#0284c7" : "#64748b", z3On ? "HIGH (ON)" : "LOW (OFF)");
    this.setPinIndicator("pin-24", z4On ? "#0284c7" : "#64748b", z4On ? "HIGH (ON)" : "LOW (OFF)");

    // -------------------------------------------------------------------------
    // 2. Animated Wire Currents
    // -------------------------------------------------------------------------
    this.toggleWire("wire-ac-contactor", acOn);
    this.toggleWire("wire-ac-motor", acOn);
    this.toggleWire("wire-zone-1", z1On);
    this.toggleWire("wire-zone-2", z2On);
    this.toggleWire("wire-zone-3", z3On);
    this.toggleWire("wire-zone-4", z4On);
    this.toggleWire("wire-i2c", true); // I2C always active

    // -------------------------------------------------------------------------
    // 3. Rotating Impellers
    // -------------------------------------------------------------------------
    this.toggleImpeller("impeller-ac", acOn);
    this.toggleImpeller("impeller-p1", z1On);
    this.toggleImpeller("impeller-p2", z2On);
    this.toggleImpeller("impeller-p3", z3On);
    this.toggleImpeller("impeller-p4", z4On);

    // -------------------------------------------------------------------------
    // 4. Fluid Levels in Tanks
    // -------------------------------------------------------------------------
    // Overhead Tank Level (max height 80 in SVG)
    const overheadEl = document.getElementById("water-overhead");
    if (overheadEl) {
      let pct = 50;
      if (tankHigh) pct = 95;
      else if (tankLow) pct = 20;
      else pct = 60;

      const maxHeight = 70;
      const h = (pct / 100) * maxHeight;
      const yBase = 150; // base y coordinate
      overheadEl.setAttribute("height", Math.max(5, h));
      overheadEl.setAttribute("y", yBase - h);
    }

    // Reservoir Level (max height 70 in SVG)
    const reservoirEl = document.getElementById("water-reservoir");
    if (reservoirEl) {
      let pct = resEmpty ? 12 : 75;
      const maxHeight = 65;
      const h = (pct / 100) * maxHeight;
      const yBase = 390;
      reservoirEl.setAttribute("height", Math.max(5, h));
      reservoirEl.setAttribute("y", yBase - h);
    }

    // -------------------------------------------------------------------------
    // 5. Update Dynamic Node Labels
    // -------------------------------------------------------------------------
    this.setNodeState("node-ac-contactor", acOn ? "ENERGIZED (25A Flowing)" : "DE-ENERGIZED (OFF)");
    this.setNodeState("node-zone1-pump", z1On ? `RUNNING (Moisture ${percentages.zone_1 || 0}%)` : `IDLE (${percentages.zone_1 || 0}%)`);
    this.setNodeState("node-zone2-pump", z2On ? `RUNNING (Moisture ${percentages.zone_2 || 0}%)` : `IDLE (${percentages.zone_2 || 0}%)`);
    this.setNodeState("node-zone3-pump", z3On ? `RUNNING (Moisture ${percentages.zone_3 || 0}%)` : `IDLE (${percentages.zone_3 || 0}%)`);
    this.setNodeState("node-zone4-pump", z4On ? `RUNNING (Moisture ${percentages.zone_4 || 0}%)` : `IDLE (${percentages.zone_4 || 0}%)`);
    this.setNodeState("node-ads1115", `Probes: Z1=${probes.zone_1_voltage}V, Z2=${probes.zone_2_voltage}V, Z3=${probes.zone_3_voltage}V, Z4=${probes.zone_4_voltage}V`);
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
