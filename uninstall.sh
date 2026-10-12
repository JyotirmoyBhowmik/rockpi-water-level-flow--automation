#!/usr/bin/env bash
# ==============================================================================
# Radxa ROCK Pi S - Smart Water & Irrigation Controller Uninstaller
# Safely de-energizes all outputs, stops services, and purges installation
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

KEEP_DATA=false

usage() {
    cat << EOF
Usage: sudo ./uninstall.sh [OPTIONS]

Options:
  --keep-data             Remove service and binaries but preserve /var/lib/smart-water and /etc/smart-water
  -h, --help              Show this help message
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-data|--uninstall-keep-data)
            KEEP_DATA=true
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
echo -e "${BLUE}     Radxa ROCK Pi S Smart Water Controller - Uninstaller               ${NC}"
echo -e "${BLUE}========================================================================${NC}"

if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}[ERROR] This script must be executed as root (sudo ./uninstall.sh).${NC}"
   exit 1
fi

echo -e "${YELLOW}[1/5] Stopping and disabling smart-water systemd service...${NC}"
systemctl stop smart-water.service 2>/dev/null || true
systemctl disable smart-water.service 2>/dev/null || true

if [[ -f /etc/systemd/system/smart-water.service ]]; then
    rm -f /etc/systemd/system/smart-water.service
    systemctl daemon-reload 2>/dev/null || true
fi

echo -e "${YELLOW}[2/5] Removing hardware udev rules and journald caps...${NC}"
rm -f /etc/udev/rules.d/99-smart-water.rules
rm -f /etc/udev/rules.d/99-rockpi-water.rules
rm -f /etc/systemd/journald.conf.d/smart-water.conf
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true

echo -e "${YELLOW}[3/5] Backing up configuration and SQLite database...${NC}"
BACKUP_DIR="/root/smart-water-backup-$(date +%Y%m%d_%H%M%S)"
mkdir -p "${BACKUP_DIR}"

if [[ -d /etc/smart-water ]]; then
    cp -r /etc/smart-water "${BACKUP_DIR}/" || true
fi
if [[ -d /var/lib/smart-water ]]; then
    cp -r /var/lib/smart-water "${BACKUP_DIR}/" || true
fi
echo -e "${GREEN}[INFO] Configuration and database archived to ${BACKUP_DIR}${NC}"

echo -e "${YELLOW}[4/5] Removing application binaries and runtime directories...${NC}"
rm -rf /opt/smart-water
rm -rf /run/smart-water

if [[ "${KEEP_DATA}" == "false" ]]; then
    rm -rf /var/lib/smart-water
    rm -rf /etc/smart-water

    echo -e "${YELLOW}[5/5] Removing smartwater system user...${NC}"
    if id -u smartwater &>/dev/null; then
        userdel -r smartwater 2>/dev/null || userdel smartwater 2>/dev/null || true
    fi
else
    echo -e "${GREEN}[INFO] Preserved /var/lib/smart-water and /etc/smart-water (--keep-data active).${NC}"
    echo -e "${GREEN}[INFO] User 'smartwater' preserved to retain data directory ownership.${NC}"
fi

echo -e "${BLUE}========================================================================${NC}"
echo -e "${GREEN} UNINSTALLATION COMPLETE. System de-provisioned cleanly.${NC}"
echo -e "${BLUE}========================================================================${NC}"
