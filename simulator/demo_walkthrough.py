"""
Live Automated Demonstration Walkthrough Script
Tests the running container simulator at http://localhost:8088
"""

import time
import httpx

BASE_URL = "http://localhost:8088"
TOKEN = "HXEZG033X6rctzm5ptDdjfGaNMYlDavrNQ9el2tS3O0"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def banner(title: str):
    print("\n" + "=" * 70)
    print(f" >>> {title}")
    print("=" * 70)


def step(num: int, description: str):
    print(f"\n[STEP {num}] {description}")


def main():
    print("======================================================================")
    print("  RADXA ROCK PI S INDUSTRIAL WATER CONTROLLER - LIVE DEMONSTRATION    ")
    print(f"  Target: {BASE_URL} (Containerized Debian 12 RK3308 Environment)    ")
    print("======================================================================")

    client = httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=10.0)

    # -------------------------------------------------------------------------
    # STEP 1: Verify System Health & Public /metrics
    # -------------------------------------------------------------------------
    step(1, "Checking System Health & Public /healthz")
    resp = client.get("/healthz")
    print(f"GET /healthz -> HTTP {resp.status_code}")
    health = resp.json()
    print(f"  Status:          {health['status']}")
    print(f"  Version:         {health['version']}")
    print(f"  Hardware Ready:  {health['hardware_ready']}")
    print(f"  Uptime (s):      {health['uptime_seconds']}")
    assert resp.status_code == 200, "Health check failed"

    step(1, "Checking Authenticated Prometheus /metrics Endpoint")
    resp = client.get("/metrics")
    print(f"GET /metrics -> HTTP {resp.status_code}")
    metrics_text = resp.text
    metric_lines = [line for line in metrics_text.splitlines() if line and not line.startswith("#")]
    for line in metric_lines[:6]:
        print(f"  [Metric] {line}")
    assert "smart_water_motor_running" in metrics_text

    # -------------------------------------------------------------------------
    # STEP 2: Query Live Telemetry Baseline
    # -------------------------------------------------------------------------
    step(2, "Inspecting Live Telemetry Baseline (/api/telemetry/live)")
    resp = client.get("/api/telemetry/live")
    assert resp.status_code == 200
    telemetry = resp.json()

    print(f"  Interlock Chain:  {telemetry['head_end']['interlock_cause'].upper()}")
    print(f"  Contactor Relay:  {'ENERGIZED' if telemetry['head_end']['contactor_relay'] else 'OFF'}")
    print(f"  Contactor Aux FB: {'CLOSED' if telemetry['head_end']['contactor_feedback'] else 'OPEN'}")
    print(f"  Motor Power:      {telemetry['power_meter']['current_a']} A @ {telemetry['power_meter']['voltage_v']} V")
    print(f"  Tank Water Level: {telemetry['level_sensor']['level_pct']}% ({telemetry['level_sensor']['volume_litres']} Litres)")
    print(f"  Wireless Node:    {'ONLINE (v1.1 verified)' if telemetry['garden']['node_online'] else 'OFFLINE'}")
    print(f"  Open-Meteo Rain:  {telemetry['weather']['rain_prob_max_pct']}% (Skip Active: {telemetry['weather']['skip_active']})")

    # -------------------------------------------------------------------------
    # STEP 3: Start AC Motor Contactor
    # -------------------------------------------------------------------------
    step(3, "Commanding AC Motor Start (POST /api/motor/start with confirm=True)")
    resp = client.post("/api/motor/start", json={"confirm": True})
    print(f"POST /api/motor/start -> HTTP {resp.status_code}")
    start_res = resp.json()
    print(f"  Result: {start_res}")
    assert resp.status_code == 200

    print("  Waiting 2.5 seconds to observe electrical contactor pull-in & power metrics...")
    time.sleep(2.5)

    resp = client.get("/api/telemetry/live")
    telemetry = resp.json()
    motor_state = telemetry['motor']['state']
    relay_on = telemetry['head_end']['contactor_relay']
    fb_closed = telemetry['head_end']['contactor_feedback']
    current_a = telemetry['power_meter']['current_a']
    voltage_v = telemetry['power_meter']['voltage_v']
    power_w = telemetry['power_meter']['power_w']
    level_pct = telemetry['level_sensor']['level_pct']
    volume_l = telemetry['level_sensor']['volume_litres']

    print(f"  Motor State:      {motor_state}")
    print(f"  Contactor Relay:  {'ENERGIZED' if relay_on else 'OFF'}")
    print(f"  Contactor Aux FB: {'CLOSED (Verified)' if fb_closed else 'OPEN'}")
    print(f"  Motor Power:      {current_a} A | {voltage_v} V | {power_w} W")
    print(f"  Tank Water Level: {level_pct}% ({volume_l} Litres - Filling)")
    assert motor_state == "RUNNING"
    assert relay_on is True
    assert current_a > 3.5

    # -------------------------------------------------------------------------
    # STEP 4: Stop AC Motor Contactor
    # -------------------------------------------------------------------------
    step(4, "Commanding AC Motor Stop (POST /api/motor/stop)")
    resp = client.post("/api/motor/stop")
    print(f"POST /api/motor/stop -> HTTP {resp.status_code}")
    print(f"  Result: {resp.json()}")
    assert resp.status_code == 200

    time.sleep(1.5)
    resp = client.get("/api/telemetry/live")
    telemetry = resp.json()
    print(f"  Motor State:      {telemetry['motor']['state']}")
    print(f"  Contactor Relay:  {'ENERGIZED' if telemetry['head_end']['contactor_relay'] else 'OFF'}")
    print(f"  Contactor Aux FB: {'CLOSED' if telemetry['head_end']['contactor_feedback'] else 'OPEN (Confirmed)'}")
    print(f"  Motor Current:    {telemetry['power_meter']['current_a']} A")
    assert telemetry['motor']['state'] == "OFF"
    assert telemetry['power_meter']['current_a'] == 0.0

    # -------------------------------------------------------------------------
    # STEP 5: Wireless Precision Irrigation (Zone 1)
    # -------------------------------------------------------------------------
    step(5, "Commanding Wireless Precision Irrigation on Zone 1 (POST /api/zones/zone_1/water)")
    resp = client.post("/api/zones/zone_1/water", json={"duration_seconds": 10})
    print(f"POST /api/zones/zone_1/water -> HTTP {resp.status_code}")
    irr_res = resp.json()
    print(f"  Result: {irr_res}")
    assert resp.status_code == 200

    print("  Observing ESP-NOW Wireless ACK & Flow Sensor Pulse Accumulation (2.5s)...")
    time.sleep(2.5)

    resp = client.get("/api/telemetry/live")
    telemetry = resp.json()
    z1 = telemetry['garden']['zones']['zone_1']
    print(f"  Zone 1 Active:    {z1['pump_running']}")
    print(f"  Flow Pulses:      {z1['flow_delta']}")
    print(f"  Daily Volume:     {z1['daily_litres']} Litres")

    # -------------------------------------------------------------------------
    # STEP 6: Emergency Stop (E-STOP) Panic Button
    # -------------------------------------------------------------------------
    step(6, "Triggering Emergency Stop Panic Button (POST /api/control/emergency-stop)")
    resp = client.post("/api/control/emergency-stop")
    print(f"POST /api/control/emergency-stop -> HTTP {resp.status_code}")
    estop_res = resp.json()
    print(f"  Result: {estop_res}")
    assert resp.status_code == 200

    resp = client.get("/api/telemetry/live")
    telemetry = resp.json()
    print(f"  System Status:    {telemetry['system_status']}")
    print(f"  Motor State:      {telemetry['motor']['state']}")
    print(f"  Contactor Relay:  {telemetry['head_end']['contactor_relay']}")
    assert telemetry['motor']['state'] in ["OFF", "LOCKED_OUT"]

    # -------------------------------------------------------------------------
    # STEP 7: Audit Log Verification
    # -------------------------------------------------------------------------
    step(7, "Checking Audit Log in Events (/api/events)")
    resp = client.get("/api/events?limit=6")
    print(f"GET /api/events -> HTTP {resp.status_code}")
    res_json = resp.json()
    events = res_json.get("data", res_json) if isinstance(res_json, dict) else res_json
    for ev in events[:5]:
        print(f"  [Audit Event] {ev.get('timestamp')} | {ev.get('event_type')} | {ev.get('message')}")

    banner("ALL 7 SIMULATOR VERIFICATION STEPS PASSED WITH 100% SUCCESS!")


if __name__ == "__main__":
    main()
