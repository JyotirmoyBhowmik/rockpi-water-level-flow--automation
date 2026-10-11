#!/usr/bin/env bash
# ==============================================================================
# Radxa ROCK Pi S (RK3308) - Smart Water & Precision Irrigation Controller
# Supported OS: Strictly Armbian Debian 12 (Bookworm) Headless
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

# ------------------------------------------------------------------------------
# 0. Enforce Supported Operating System: Armbian Debian 12 (Bookworm) (Part 0.4)
# ------------------------------------------------------------------------------
if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    CODENAME="${VERSION_CODENAME:-}"
    VER_ID="${VERSION_ID:-}"
    if [[ "${CODENAME}" != "bookworm" && "${VER_ID}" != "12" ]]; then
        echo -e "${RED}[ERROR] Unsupported Operating System: ${PRETTY_NAME:-Linux}.${NC}"
        echo -e "${RED}[ERROR] Supported OS for this release is strictly Armbian Debian 12 (Bookworm).${NC}"
        echo -e "${RED}[ERROR] Halting installation to protect system integrity.${NC}"
        exit 1
    fi
    echo -e "${GREEN}[OK] Verified supported OS: ${PRETTY_NAME} (Bookworm)${NC}"
else
    echo -e "${RED}[ERROR] Cannot verify OS: /etc/os-release missing. Aborting.${NC}"
    exit 1
fi

INSTALL_DIR="/opt/smart-water"
CONFIG_DIR="/etc/smart-water"
DATA_DIR="/var/lib/smart-water"
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${GREEN}[1/8] Installing hardware packages and libgpiod from apt...${NC}"
apt-get update -y
# Install only python3-libgpiod and gpiod from apt (Part 1.1)
apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    python3-libgpiod \
    gpiod \
    i2c-tools \
    espeak-ng \
    aplay \
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

# Record install timestamp if not present (Part 1.2)
if [[ ! -f "${DATA_DIR}/install_timestamp" ]]; then
    date +%s > "${DATA_DIR}/install_timestamp"
fi

# Deploy bootstrap config template (0640 root:smartwater) (Part 1.2)
if [[ ! -f "${CONFIG_DIR}/config.yaml" ]]; then
    echo -e "${YELLOW}[INFO] Deploying bootstrap config.yaml to ${CONFIG_DIR}...${NC}"
    cp "${CURRENT_DIR}/config.example.yaml" "${CONFIG_DIR}/config.yaml"
fi
chown root:smartwater "${CONFIG_DIR}/config.yaml"
chmod 0640 "${CONFIG_DIR}/config.yaml"

echo -e "${GREEN}[4/8] Syncing application files (excluding tests/)...${NC}"
# Exclude tests/ per Ground Rule 0.2
cp -ru "${CURRENT_DIR}/app" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/run.py" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/requirements.txt" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/systemd" "${INSTALL_DIR}/"
# Ensure tests directory is never shipped on target device
rm -rf "${INSTALL_DIR}/tests"

echo -e "${GREEN}[5/8] Setting up Python virtual environment (--system-site-packages)...${NC}"
# Virtual environment with --system-site-packages so apt python3-libgpiod is inherited
if [[ ! -d "${INSTALL_DIR}/venv" ]]; then
    python3 -m venv --system-site-packages "${INSTALL_DIR}/venv"
fi

"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip setuptools wheel
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo -e "${GREEN}[6/8] Configuring udev rules and journald caps...${NC}"
cat << 'EOF' > /etc/udev/rules.d/99-smart-water.rules
# Rock Pi S Hardware Pins & Buses
KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"
KERNEL=="i2c-*", GROUP="i2c", MODE="0660"
KERNEL=="watchdog*", GROUP="smartwater", MODE="0660"

# ESP32-C3 Gateway USB Serial Mappings (creates stable symlink /dev/garden-gateway)
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="1001", SYMLINK+="garden-gateway", GROUP="dialout", MODE="0660"
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="garden-gateway", GROUP="dialout", MODE="0660"
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", SYMLINK+="garden-gateway", GROUP="dialout", MODE="0660"
EOF

udevadm control --reload-rules || true
udevadm trigger || true

# Systemd journal 50M cap (Part 5.3)
mkdir -p /etc/systemd/journald.conf.d
cp "${INSTALL_DIR}/systemd/smart-water-journald.conf" /etc/systemd/journald.conf.d/smart-water.conf
systemctl restart systemd-journald || true

echo -e "${GREEN}[7/8] Verifying API authentication token...${NC}"
chown -R smartwater:smartwater "${INSTALL_DIR}" "${DATA_DIR}" /run/smart-water
chmod 750 "${INSTALL_DIR}"
chmod 700 "${DATA_DIR}"

# Generate token only if auth.json is missing (Part 3.5)
AUTH_FILE="${DATA_DIR}/auth.json"
if [[ ! -f "${AUTH_FILE}" ]]; then
    echo -e "${YELLOW}[INFO] No existing auth.json found. Generating fresh Bearer token...${NC}"
    su -s /bin/bash smartwater -c \
        "${INSTALL_DIR}/venv/bin/python -m app.cli rotate-token"
else
    echo -e "${GREEN}[OK] Existing auth.json found. Preserving current token.${NC}"
fi

echo -e "${GREEN}[8/8] Installing and activating systemd service...${NC}"
cp "${INSTALL_DIR}/systemd/smart-water.service" /etc/systemd/system/smart-water.service
systemctl daemon-reload
systemctl enable smart-water.service
systemctl restart smart-water.service

echo -e "${BLUE}========================================================================${NC}"
echo -e "${GREEN}${BOLD} INSTALLATION COMPLETE! Smart Water Controller is running.${NC}"
echo -e "${BLUE}========================================================================${NC}"
echo -e "Service Status:  systemctl status smart-water.service"
echo -e "Journal Logs:    journalctl -u smart-water -f"
echo -e "Bootstrap Config:/etc/smart-water/config.yaml (0640 root:smartwater)"
echo -e "Runtime Settings:/var/lib/smart-water/settings.yaml (0600)"
echo -e "Database:        /var/lib/smart-water/system.db"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo -e "1. Authenticate Tailscale mesh: ${BOLD}tailscale up --login-server=https://your-headscale.domain.com${NC}"
echo -e "2. Open Dashboard:             ${BOLD}http://<tailscale-ip>:8000${NC}"
echo -e "3. Complete Setup Wizard in UI using the generated Bearer token."
echo -e "${BLUE}========================================================================${NC}"
