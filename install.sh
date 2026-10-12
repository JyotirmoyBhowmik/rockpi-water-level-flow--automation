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

DRY_RUN=false
UNINSTALL_KEEP_DATA=false
OVERLAYROOT_MODE=false

usage() {
    cat << EOF
Usage: sudo ./install.sh [OPTIONS]

Options:
  --dry-run               Run preflight checks and show actions without changing system
  --uninstall-keep-data   Remove service, binaries, and udev rules while preserving database and config
  --overlayroot           Check and prepare for read-only root with persistent /var/lib/smart-water
  -h, --help              Show this help message
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --uninstall-keep-data)
            UNINSTALL_KEEP_DATA=true
            shift
            ;;
        --overlayroot)
            OVERLAYROOT_MODE=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo -e "${RED}[ERROR] Unknown option: $1${NC}"
            usage
            ;;
    esac
done

echo -e "${BLUE}========================================================================${NC}"
echo -e "${BLUE}     Radxa ROCK Pi S Industrial Smart Water Controller Installer       ${NC}"
echo -e "${BLUE}========================================================================${NC}"

if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}[ERROR] This installer must be executed as root (sudo ./install.sh).${NC}"
   exit 1
fi

SLOT_A="/opt/smart-water-a"
SLOT_B="/opt/smart-water-b"
INSTALL_DIR="/opt/smart-water"
CONFIG_DIR="/etc/smart-water"
DATA_DIR="/var/lib/smart-water"
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_HW_CHECKS="${SKIP_HW_CHECKS:-0}"

# ------------------------------------------------------------------------------
# Mode: --uninstall-keep-data
# ------------------------------------------------------------------------------
if [[ "${UNINSTALL_KEEP_DATA}" == "true" ]]; then
    echo -e "${YELLOW}[INFO] Executing uninstallation while preserving data and configuration...${NC}"
    systemctl stop smart-water.service 2>/dev/null || true
    systemctl disable smart-water.service 2>/dev/null || true
    rm -f /etc/systemd/system/smart-water.service
    systemctl daemon-reload 2>/dev/null || true

    rm -f /etc/udev/rules.d/99-smart-water.rules
    rm -f /etc/udev/rules.d/99-rockpi-water.rules
    rm -f /etc/systemd/journald.conf.d/smart-water.conf
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true

    rm -rf "${INSTALL_DIR}"
    rm -rf /run/smart-water

    echo -e "${GREEN}[OK] Uninstallation complete.${NC}"
    echo -e "Preserved database & auth: ${DATA_DIR}"
    echo -e "Preserved configuration:   ${CONFIG_DIR}"
    exit 0
fi

# ------------------------------------------------------------------------------
# Preflight Check 1: Operating System Check (Strictly Debian 12 Bookworm)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[PREFLIGHT] Checking operating system version...${NC}"
if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    CODENAME="${VERSION_CODENAME:-}"
    VER_ID="${VERSION_ID:-}"
    if [[ "${CODENAME}" != "bookworm" && "${VER_ID}" != "12" ]]; then
        echo -e "${RED}[ERROR] Unsupported Operating System: ${PRETTY_NAME:-Linux}.${NC}"
        echo -e "${RED}[ERROR] Supported OS is strictly Debian 12 (Bookworm).${NC}"
        echo -e "${RED}[ERROR] Halting installation to protect system integrity.${NC}"
        exit 1
    fi
    echo -e "  ${GREEN}[OK]${NC} Verified Debian 12 (Bookworm): ${PRETTY_NAME}"
else
    echo -e "${RED}[ERROR] Cannot verify OS: /etc/os-release missing. Aborting.${NC}"
    exit 1
fi

# ------------------------------------------------------------------------------
# Preflight Check 2: CPU Architecture Check (aarch64 or armhf)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[PREFLIGHT] Checking CPU architecture...${NC}"
ARCH="$(uname -m)"
if [[ "${ARCH}" != "aarch64" && "${ARCH}" != "armhf" && "${ARCH}" != "armv7l" ]]; then
    if [[ "${SKIP_HW_CHECKS}" == "1" ]]; then
        echo -e "  ${YELLOW}[WARN]${NC} Non-ARM architecture detected: ${ARCH} (skipped via SKIP_HW_CHECKS=1)."
    else
        echo -e "${RED}[ERROR] Unsupported CPU architecture: ${ARCH}.${NC}"
        echo -e "${RED}[ERROR] Supported architectures for Radxa ROCK Pi S are aarch64 and armhf.${NC}"
        exit 1
    fi
else
    echo -e "  ${GREEN}[OK]${NC} Supported CPU architecture: ${ARCH}"
fi

# ------------------------------------------------------------------------------
# Preflight Check 3: Free Disk Space (>= 500 MB)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[PREFLIGHT] Checking available disk space...${NC}"
TARGET_PARTITION="/"
[[ -d "${INSTALL_DIR}" ]] && TARGET_PARTITION="${INSTALL_DIR}"
FREE_DISK_MB="$(df -m "${TARGET_PARTITION}" 2>/dev/null | awk 'NR==2 {print $4}' || echo 0)"
if [[ "${FREE_DISK_MB}" -lt 500 ]]; then
    echo -e "${RED}[ERROR] Insufficient disk space: ${FREE_DISK_MB}MB available on ${TARGET_PARTITION} (minimum 500MB required).${NC}"
    exit 1
else
    echo -e "  ${GREEN}[OK]${NC} Available disk space: ${FREE_DISK_MB}MB (>= 500MB required)"
fi

# ------------------------------------------------------------------------------
# Preflight Check 4: Free/Available RAM
# ------------------------------------------------------------------------------
echo -e "${GREEN}[PREFLIGHT] Checking available system memory...${NC}"
MEM_AVAIL_KB="$(awk '/MemAvailable/ {print $2}' /proc/meminfo 2>/dev/null || awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
MEM_AVAIL_MB=$((MEM_AVAIL_KB / 1024))
if [[ "${MEM_AVAIL_MB}" -gt 0 && "${MEM_AVAIL_MB}" -lt 50 ]]; then
    echo -e "  ${YELLOW}[WARN]${NC} Low available RAM: ${MEM_AVAIL_MB}MB. Recommended >= 64MB for RK3308 operation."
else
    echo -e "  ${GREEN}[OK]${NC} Available memory: ${MEM_AVAIL_MB}MB"
fi

# ------------------------------------------------------------------------------
# Preflight Check 5: Hardware & Overlays (Warn, not fail)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[PREFLIGHT] Checking hardware device nodes & overlays...${NC}"

# 1. /dev/watchdog
if [[ -c /dev/watchdog ]]; then
    echo -e "  ${GREEN}[OK]${NC} Hardware watchdog character device present (/dev/watchdog)."
else
    if [[ "${SKIP_HW_CHECKS}" == "1" ]]; then
        echo -e "  ${YELLOW}[WARN]${NC} /dev/watchdog not found (hardware check skipped via SKIP_HW_CHECKS=1)."
    else
        echo -e "  ${YELLOW}[WARN]${NC} /dev/watchdog character device not found. Ensure watchdog overlay is enabled."
    fi
fi

# 2. /dev/gpiochip*
if compgen -G "/dev/gpiochip*" > /dev/null; then
    echo -e "  ${GREEN}[OK]${NC} GPIO controller character devices present (/dev/gpiochip*)."
else
    if [[ "${SKIP_HW_CHECKS}" == "1" ]]; then
        echo -e "  ${YELLOW}[WARN]${NC} No /dev/gpiochip* devices found (hardware check skipped via SKIP_HW_CHECKS=1)."
    else
        echo -e "  ${YELLOW}[WARN]${NC} No /dev/gpiochip* devices found. Ensure GPIO controller is active in device tree."
    fi
fi

# 3. /dev/i2c-*
if compgen -G "/dev/i2c-*" > /dev/null; then
    echo -e "  ${GREEN}[OK]${NC} I2C bus devices present (/dev/i2c-*)."
else
    if [[ "${SKIP_HW_CHECKS}" == "1" ]]; then
        echo -e "  ${YELLOW}[WARN]${NC} No /dev/i2c-* bus devices found (hardware check skipped via SKIP_HW_CHECKS=1)."
    else
        echo -e "  ${YELLOW}[WARN]${NC} No /dev/i2c-* devices found. Ensure overlays=i2c7 is enabled in /boot/armbianEnv.txt."
    fi
fi

# ------------------------------------------------------------------------------
# Preflight Check 6: Storage & Overlayroot Checks
# ------------------------------------------------------------------------------
echo -e "${GREEN}[PREFLIGHT] Checking filesystem & storage configuration...${NC}"
if [[ "${OVERLAYROOT_MODE}" == "true" ]]; then
    echo -e "  ${YELLOW}[INFO]${NC} Overlayroot mode enabled. Inspecting partition mounts..."
    if command -v findmnt &>/dev/null; then
        ROOT_SRC="$(findmnt -n -o SOURCE / 2>/dev/null || true)"
        DATA_SRC="$(findmnt -n -o SOURCE "${DATA_DIR}" 2>/dev/null || true)"
        if [[ -n "${DATA_SRC}" && "${DATA_SRC}" != "${ROOT_SRC}" ]]; then
            echo -e "  ${GREEN}[OK]${NC} ${DATA_DIR} is mounted on dedicated partition (${DATA_SRC})."
        else
            echo -e "  ${YELLOW}[WARN]${NC} ${DATA_DIR} resides on the root filesystem (${ROOT_SRC:-/})."
            echo -e "         To run with a read-only root, ensure ${DATA_DIR} is in /etc/fstab"
            echo -e "         on a dedicated writable ext4 partition before enabling overlayroot."
        fi
    fi
    if command -v log2ram &>/dev/null; then
        echo -e "  ${GREEN}[OK]${NC} log2ram detected for zram/tmpfs journal caching."
    else
        echo -e "  ${YELLOW}[INFO]${NC} log2ram not detected. Recommended for flash longevity: https://github.com/azlux/log2ram"
    fi
fi

# ------------------------------------------------------------------------------
# Dry-Run Exit
# ------------------------------------------------------------------------------
if [[ "${DRY_RUN}" == "true" ]]; then
    echo -e "\n${BLUE}========================================================================${NC}"
    echo -e "${GREEN}[DRY-RUN] Preflight checks passed!${NC}"
    echo -e "${YELLOW}Planned actions (no changes made):${NC}"
    echo -e "  - Install apt packages: python3, python3-venv, python3-libgpiod, gpiod, sqlite3, chrony, alsa-utils, etc."
    echo -e "  - Configure groups (gpio, i2c, dialout, audio) and user 'smartwater'"
    echo -e "  - Prepare directories: ${INSTALL_DIR}, ${CONFIG_DIR}, ${DATA_DIR} (owned smartwater:smartwater 0750)"
    echo -e "  - Copy application files: run.py, requirements.txt, app/, protocol/, systemd/ (excluding tests/)"
    echo -e "  - Setup virtualenv with --system-site-packages and install dependencies"
    echo -e "  - Configure udev rules /etc/udev/rules.d/99-smart-water.rules"
    echo -e "  - Verify or rotate API Bearer token in ${DATA_DIR}/auth.json"
    echo -e "  - Deploy and activate systemd service smart-water.service"
    echo -e "${BLUE}========================================================================${NC}"
    exit 0
fi

# ------------------------------------------------------------------------------
# 1. Install System Dependencies (Idempotent)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[1/8] Installing system dependencies from apt...${NC}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    python3-libgpiod \
    gpiod \
    sqlite3 \
    chrony \
    i2c-tools \
    espeak-ng \
    alsa-utils \
    curl \
    ca-certificates

if [[ "${OVERLAYROOT_MODE}" == "true" ]]; then
    apt-get install -y --no-install-recommends overlayroot || true
fi

# Tailscale setup for private secure mesh
if ! command -v tailscale &> /dev/null; then
    echo -e "${YELLOW}[INFO] Installing Tailscale mesh client for secure remote access...${NC}"
    curl -fsSL https://tailscale.com/install.sh | sh || true
fi

# ------------------------------------------------------------------------------
# 2. System User & Group Provisioning (Idempotent)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[2/8] Creating unprivileged 'smartwater' system user and groups...${NC}"
groupadd -f gpio
groupadd -f i2c
groupadd -f dialout
groupadd -f audio

if ! id -u smartwater &>/dev/null; then
    useradd -r -s /usr/sbin/nologin -d "${DATA_DIR}" -M smartwater
fi
usermod -aG gpio,i2c,dialout,audio smartwater

# ------------------------------------------------------------------------------
# 3. Directory Structure & Permissions (A/B layout, backups, and DATA_DIR)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[3/8] Preparing directory structure and permissions (A/B layout)...${NC}"
mkdir -p "${SLOT_A}"
mkdir -p "${CONFIG_DIR}"
mkdir -p "${DATA_DIR}"
mkdir -p "${DATA_DIR}/backups"
mkdir -p /run/smart-water

# Provision /opt/smart-water symlink to active slot /opt/smart-water-a
if [[ ! -L "${INSTALL_DIR}" ]]; then
    if [[ -d "${INSTALL_DIR}" && "${INSTALL_DIR}" != "${SLOT_A}" ]]; then
        cp -ru "${INSTALL_DIR}/"* "${SLOT_A}/" 2>/dev/null || true
        rm -rf "${INSTALL_DIR}"
    fi
    ln -sfn "${SLOT_A}" "${INSTALL_DIR}"
fi

# Ownership and strict permissions
chown smartwater:smartwater "${DATA_DIR}"
chmod 0750 "${DATA_DIR}"
chown smartwater:smartwater "${DATA_DIR}/backups"
chmod 0700 "${DATA_DIR}/backups"

if [[ ! -f "${DATA_DIR}/install_timestamp" ]]; then
    date +%s > "${DATA_DIR}/install_timestamp"
    chown smartwater:smartwater "${DATA_DIR}/install_timestamp"
    chmod 0640 "${DATA_DIR}/install_timestamp"
fi

if [[ ! -f "${CONFIG_DIR}/config.yaml" ]]; then
    echo -e "${YELLOW}[INFO] Deploying bootstrap config.yaml to ${CONFIG_DIR}...${NC}"
    cp "${CURRENT_DIR}/config.example.yaml" "${CONFIG_DIR}/config.yaml"
fi
chown root:smartwater "${CONFIG_DIR}/config.yaml"
chmod 0640 "${CONFIG_DIR}/config.yaml"

# ------------------------------------------------------------------------------
# 4. Sync Application Files (run.py, requirements.txt, app/, protocol/, systemd/)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[4/8] Syncing application files (excluding tests/)...${NC}"
cp -ru "${CURRENT_DIR}/run.py" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/requirements.txt" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/app" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/protocol" "${INSTALL_DIR}/"
cp -ru "${CURRENT_DIR}/systemd" "${INSTALL_DIR}/"
# Ground Rule 0.1 & Part 0.2: tests/ directory is strictly never shipped to production board
rm -rf "${INSTALL_DIR}/tests"

# ------------------------------------------------------------------------------
# 5. Virtualenv & Python Packages (--system-site-packages)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[5/8] Setting up Python virtual environment (--system-site-packages)...${NC}"
if [[ ! -d "${INSTALL_DIR}/venv" ]]; then
    python3 -m venv --system-site-packages "${INSTALL_DIR}/venv"
fi

"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip setuptools wheel
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# ------------------------------------------------------------------------------
# 6. Udev Rules & Journald Limits
# ------------------------------------------------------------------------------
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

udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true

# Systemd journal 50M cap
mkdir -p /etc/systemd/journald.conf.d
cp "${INSTALL_DIR}/systemd/smart-water-journald.conf" /etc/systemd/journald.conf.d/smart-water.conf
systemctl restart systemd-journald 2>/dev/null || true

# ------------------------------------------------------------------------------
# 7. API Authentication Token (Stored in /var/lib/smart-water/auth.json)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[7/8] Verifying API authentication token...${NC}"
chown -R smartwater:smartwater "${INSTALL_DIR}" "${DATA_DIR}" /run/smart-water
chmod 750 "${INSTALL_DIR}"
chmod 750 "${DATA_DIR}"

AUTH_FILE="${DATA_DIR}/auth.json"
if [[ ! -f "${AUTH_FILE}" ]]; then
    echo -e "${YELLOW}[INFO] Generating initial API Bearer token...${NC}"
    TOKEN_OUTPUT=$(su -s /bin/bash smartwater -c \
        "export SMART_WATER_DATA_DIR='${DATA_DIR}'; '${INSTALL_DIR}/venv/bin/python' -m app.cli rotate-token")
    # Print the raw token once to stdout
    echo "${TOKEN_OUTPUT}"
    chown smartwater:smartwater "${AUTH_FILE}" || true
    chmod 0600 "${AUTH_FILE}" || true
else
    echo -e "${GREEN}[OK] Existing auth.json found at ${AUTH_FILE}. Preserving current token.${NC}"
fi

# ------------------------------------------------------------------------------
# 8. Deploy & Activate Systemd Service (Idempotent)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[8/8] Installing and activating systemd service...${NC}"
cp "${INSTALL_DIR}/systemd/smart-water.service" /etc/systemd/system/smart-water.service
systemctl daemon-reload 2>/dev/null || true
systemctl enable smart-water.service 2>/dev/null || true
systemctl restart smart-water.service 2>/dev/null || true

echo -e "${BLUE}========================================================================${NC}"
echo -e "${GREEN}${BOLD} INSTALLATION COMPLETE! Smart Water Controller is configured.${NC}"
echo -e "${BLUE}========================================================================${NC}"
echo -e "Service Status:  systemctl status smart-water.service"
echo -e "Journal Logs:    journalctl -u smart-water -f"
echo -e "Bootstrap Config:/etc/smart-water/config.yaml (0640 root:smartwater)"
echo -e "Runtime Settings:/var/lib/smart-water/settings.yaml (0600)"
echo -e "Database:        /var/lib/smart-water/system.db"
echo -e "Auth Token:      /var/lib/smart-water/auth.json (0600 smartwater:smartwater)"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo -e "1. Authenticate Tailscale mesh: ${BOLD}tailscale up --login-server=https://your-headscale.domain.com${NC}"
echo -e "2. Open Dashboard:             ${BOLD}http://<tailscale-ip>:8000${NC}"
echo -e "3. Complete Setup Wizard in UI using the generated Bearer token."
echo -e "${BLUE}========================================================================${NC}"
