#!/usr/bin/env bash
# ==============================================================================
# Radxa ROCK Pi S (RK3308) - Smart Water & Irrigation Controller Setup Script
# Target OS: Armbian / Debian 12 (Bookworm) / Debian 13 (Trixie) Headless
# Memory Budget: Strictly optimized for 512MB RAM (<35MB process footprint)
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}========================================================================${NC}"
echo -e "${BLUE}     Radxa ROCK Pi S Industrial Smart Water Controller Installer       ${NC}"
echo -e "${BLUE}========================================================================${NC}"

if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}[ERROR] This installer must be executed as root (sudo ./install.sh).${NC}"
   exit 1
fi

INSTALL_DIR="/opt/smart-water"
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${GREEN}[1/7] Updating package repositories and installing hardware packages...${NC}"
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    libgpiod2 \
    gpiod \
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

echo -e "${GREEN}[2/7] Preparing application deployment directory at ${INSTALL_DIR}...${NC}"
mkdir -p "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}/data"
chmod 755 "${INSTALL_DIR}"
chmod 700 "${INSTALL_DIR}/data"

echo -e "${GREEN}[3/7] Syncing project files to ${INSTALL_DIR}...${NC}"
cp -ru "${CURRENT_DIR}/app" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/run.py" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/requirements.txt" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/systemd" "${INSTALL_DIR}/"

echo -e "${GREEN}[4/7] Setting up isolated Python virtual environment...${NC}"
if [[ ! -d "${INSTALL_DIR}/venv" ]]; then
    python3 -m venv "${INSTALL_DIR}/venv"
fi

"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip setuptools wheel
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# Install hardware character device bindings for Linux
echo -e "${GREEN}[5/7] Installing hardware bindings (gpiod & smbus2)...${NC}"
"${INSTALL_DIR}/venv/bin/pip" install gpiod smbus2 || {
    echo -e "${YELLOW}[WARN] Could not compile native gpiod/smbus2 wheel. Fallback mock driver will be active if hardware devices are absent.${NC}"
}

echo -e "${GREEN}[6/7] Enabling I2C7 and GPIO hardware permissions...${NC}"
# Add root/daemon to dialout, gpio, and i2c groups
groupadd -f gpio
groupadd -f i2c
usermod -aG gpio,i2c root || true

# Udev rules for Rock Pi S hardware access
cat << 'EOF' > /etc/udev/rules.d/99-rockpi-water.rules
KERNEL=="gpiochip1", GROUP="gpio", MODE="0660"
KERNEL=="i2c-7", GROUP="i2c", MODE="0660"
KERNEL=="watchdog*", MODE="0660"
EOF
udevadm control --reload-rules || true

echo -e "${GREEN}[7/7] Installing systemd hardened service...${NC}"
cp "${INSTALL_DIR}/systemd/smart-water.service" /etc/systemd/system/smart-water.service
systemctl daemon-reload
systemctl enable smart-water.service
systemctl restart smart-water.service

echo -e "${BLUE}========================================================================${NC}"
echo -e "${GREEN} INSTALLATION COMPLETE! Controller is running via systemd.${NC}"
echo -e "${BLUE}========================================================================${NC}"
echo -e "Service Status:  systemctl status smart-water.service"
echo -e "Journal Logs:    journalctl -u smart-water -f"
echo -e "Local Access:    http://localhost:8000"
echo ""
echo -e "${YELLOW}Next Steps for Headscale / Tailscale Mesh Access:${NC}"
echo -e "1. Authenticate to your private mesh:"
echo -e "   tailscale up --login-server=https://your-headscale.domain.com"
echo -e "2. Access the controller dashboard over your private mesh IP:"
echo -e "   http://<tailscale-ip>:8000"
echo -e "${BLUE}========================================================================${NC}"
