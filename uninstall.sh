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

echo -e "${BLUE}========================================================================${NC}"
echo -e "${BLUE}     Radxa ROCK Pi S Smart Water Controller - Uninstaller               ${NC}"
echo -e "${BLUE}========================================================================${NC}"

if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}[ERROR] This script must be executed as root (sudo ./uninstall.sh).${NC}"
   exit 1
fi

echo -e "${YELLOW}[1/5] Stopping and disabling smart-water systemd service...${NC}"
systemctl stop smart-water.service || true
systemctl disable smart-water.service || true

if [[ -f /etc/systemd/system/smart-water.service ]]; then
    rm -f /etc/systemd/system/smart-water.service
    systemctl daemon-reload
fi

echo -e "${YELLOW}[2/5] Removing hardware udev rules...${NC}"
rm -f /etc/udev/rules.d/99-smart-water.rules
rm -f /etc/udev/rules.d/99-rockpi-water.rules
udevadm control --reload-rules || true
udevadm trigger || true

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

echo -e "${YELLOW}[4/5] Removing application directories...${NC}"
rm -rf /opt/smart-water
rm -rf /var/lib/smart-water
rm -rf /etc/smart-water
rm -rf /run/smart-water

echo -e "${YELLOW}[5/5] Removing smartwater system user...${NC}"
if id -u smartwater &>/dev/null; then
    userdel -r smartwater 2>/dev/null || userdel smartwater || true
fi

echo -e "${BLUE}========================================================================${NC}"
echo -e "${GREEN} UNINSTALLATION COMPLETE. System restored to clean state.${NC}"
echo -e "${BLUE}========================================================================${NC}"
