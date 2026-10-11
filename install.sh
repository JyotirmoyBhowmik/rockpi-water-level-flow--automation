#!/usr/bin/env bash
# ==============================================================================
# Radxa ROCK Pi S (RK3308) - Smart Water & Irrigation Controller Setup Script
# Target OS: Armbian / Debian 12 (Bookworm) / Debian 13 (Trixie) Headless
# Memory Budget: Strictly optimized for 512MB RAM (<35MB process footprint)
# Unprivileged execution under dedicated 'smartwater' system user
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BLUE}========================================================================${NC}"
echo -e "${BLUE}     Radxa ROCK Pi S Industrial Smart Water Controller Installer       ${NC}"
echo -e "${BLUE}========================================================================${NC}"

if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}[ERROR] This installer must be executed as root (sudo ./install.sh).${NC}"
   exit 1
fi

INSTALL_DIR="/opt/smart-water"
CONFIG_DIR="/etc/smart-water"
DATA_DIR="/var/lib/smart-water"
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${GREEN}[1/8] Updating package repositories and installing hardware packages...${NC}"
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    python3-libgpiod \
    gpiod \
    libgpiod2 \
    i2c-tools \
    espeak-ng \
    alsa-utils \
    curl \
    ca-certificates

# Tailscale setup for private secure mesh
if ! command -v tailscale &> /dev/null; then
    echo -e "${YELLOW}[INFO] Installing Tailscale mesh client for secure remote access...${NC}"
    curl -fsSL https://tailscale.com/install.sh | sh
fi

echo -e "${GREEN}[2/8] Creating unprivileged 'smartwater' system user and groups...${NC}"
groupadd -f gpio
groupadd -f i2c
groupadd -f dialout
groupadd -f audio

if ! id -u smartwater &>/dev/null; then
    useradd -r -s /usr/sbin/nologin -d "${DATA_DIR}" -M smartwater
fi
usermod -aG gpio,i2c,dialout,audio smartwater

echo -e "${GREEN}[3/8] Preparing directory structure and permissions...${NC}"
mkdir -p "${INSTALL_DIR}"
mkdir -p "${CONFIG_DIR}"
mkdir -p "${DATA_DIR}"
mkdir -p /run/smart-water

# Copy default config template if not present
if [[ ! -f "${CONFIG_DIR}/config.yaml" ]]; then
    echo -e "${YELLOW}[INFO] Deploying default unconfigured config.yaml to ${CONFIG_DIR}...${NC}"
    cp "${CURRENT_DIR}/config.example.yaml" "${CONFIG_DIR}/config.yaml"
fi

echo -e "${GREEN}[4/8] Syncing application files (excluding tests/)...${NC}"
# Exclude tests/ per Ground Rule 0.2
cp -ru "${CURRENT_DIR}/app" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/run.py" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/requirements.txt" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/systemd" "${INSTALL_DIR}/"
# Ensure tests directory is never shipped on target device
rm -rf "${INSTALL_DIR}/tests"

echo -e "${GREEN}[5/8] Setting up Python virtual environment...${NC}"
if [[ ! -d "${INSTALL_DIR}/venv" ]]; then
    python3 -m venv "${INSTALL_DIR}/venv"
fi

"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip setuptools wheel
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# Optional native fallbacks for gpiod/smbus2 if system wheel needed
"${INSTALL_DIR}/venv/bin/pip" install gpiod smbus2 || {
    echo -e "${YELLOW}[INFO] Using system-provided python3-libgpiod bindings.${NC}"
}

echo -e "${GREEN}[6/8] Configuring udev rules for persistent hardware node mapping...${NC}"
cat << 'EOF' > /etc/udev/rules.d/99-smart-water.rules
# Rock Pi S Hardware Pins & Buses
KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"
KERNEL=="i2c-*", GROUP="i2c", MODE="0660"
KERNEL=="watchdog*", GROUP="smartwater", MODE="0660"

# ESP32-C3 Gateway USB Serial Mappings (creates stable symlink /dev/garden-gateway)
# 1. ESP32-C3 Native USB-JTAG/CDC (VID: 303a, PID: 1001)
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", SYMLINK+="garden-gateway", GROUP="dialout", MODE="0660"
# 2. WCH CH340 USB-Serial (VID: 1a86, PID: 7523)
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="garden-gateway", GROUP="dialout", MODE="0660"
# 3. Silicon Labs CP2102 (VID: 10c4, PID: ea60)
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", SYMLINK+="garden-gateway", GROUP="dialout", MODE="0660"
EOF

udevadm control --reload-rules || true
udevadm trigger || true

echo -e "${GREEN}[7/8] Generating API Security Token and setting permissions...${NC}"
# Set ownership before CLI token generation
chown -R smartwater:smartwater "${INSTALL_DIR}" "${CONFIG_DIR}" "${DATA_DIR}" /run/smart-water
chmod 750 "${INSTALL_DIR}" "${CONFIG_DIR}" "${DATA_DIR}"

# Run CLI as smartwater user to generate token
su -s /bin/bash smartwater -c \
    "${INSTALL_DIR}/venv/bin/python -m app.cli rotate-token"

echo -e "${GREEN}[8/8] Installing hardened systemd service...${NC}"
cp "${INSTALL_DIR}/systemd/smart-water.service" /etc/systemd/system/smart-water.service
systemctl daemon-reload
systemctl enable smart-water.service
systemctl restart smart-water.service

echo -e "${BLUE}========================================================================${NC}"
echo -e "${GREEN}${BOLD} INSTALLATION COMPLETE! Smart Water Controller is running.${NC}"
echo -e "${BLUE}========================================================================${NC}"
echo -e "Service Status:  systemctl status smart-water.service"
echo -e "Journal Logs:    journalctl -u smart-water -f"
echo -e "Config File:     /etc/smart-water/config.yaml"
echo -e "Database Path:   /var/lib/smart-water/state.db"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo -e "1. Authenticate Tailscale mesh: ${BOLD}tailscale up --login-server=https://your-headscale.domain.com${NC}"
echo -e "2. Open Dashboard:             ${BOLD}http://<tailscale-ip>:8000${NC}"
echo -e "3. Complete 7-step Setup Wizard to verify GPIO pin lines and calibrate probes."
echo -e "${BLUE}========================================================================${NC}"
