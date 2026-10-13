#!/bin/bash
set -e

echo "========================================================================"
echo "    RADXA ROCK PI S WATER LEVEL & IRRIGATION CONTROLLER SIMULATOR       "
echo "    Running in Debian 12 (Bookworm) Containerized Environment          "
echo "========================================================================"

mkdir -p /var/lib/smart-water/backups
mkdir -p /etc/smart-water
cp /opt/smart-water/simulator/config.simulator.yaml /etc/smart-water/config.yaml

export SMART_WATER_SIMULATOR=1
export SMART_WATER_CONFIG=/etc/smart-water/config.yaml
export SMART_WATER_DATA_DIR=/var/lib/smart-water
export PYTHONUNBUFFERED=1
export PYTHONPATH=/opt/smart-water

# 1. Setup Virtual Serial PTY Link for ESP32 Gateway and Node
echo "[SIM] Starting virtual serial bridge (/dev/garden-gateway <-> /tmp/node-gateway)..."
rm -f /dev/garden-gateway /tmp/node-gateway
socat -d -d pty,raw,echo=0,link=/dev/garden-gateway pty,raw,echo=0,link=/tmp/node-gateway 2>/dev/null &
SOCAT_PID=$!

# Wait for serial endpoints to be established
for i in {1..20}; do
    if [ -e /dev/garden-gateway ] && [ -e /tmp/node-gateway ]; then
        break
    fi
    sleep 0.2
done

# 2. Start Virtual ESP32-C3 Node & Telemetry Feeder
echo "[SIM] Starting Virtual ESP32 Node & Protocol v1.1 telemetry generator..."
python -u simulator/virtual_esp32_node.py &
NODE_PID=$!

# 3. Start Physics & Telemetry Harness
echo "[SIM] Starting Physics Harness (Tank level & Motor power dynamics)..."
python -u simulator/harness.py &
HARNESS_PID=$!

# 4. Start Smart Water Controller Application
echo "[SIM] Starting FastAPI Controller on port 8000..."
exec python run.py
