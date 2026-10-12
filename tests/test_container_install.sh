#!/usr/bin/env bash
set -euo pipefail

echo "=== TEST 1: Full install ==="
./install.sh

echo "=== Verify files copied ==="
test -f /opt/smart-water/run.py
test -f /opt/smart-water/requirements.txt
test -d /opt/smart-water/app
test -d /opt/smart-water/protocol
test -d /opt/smart-water/systemd
test ! -d /opt/smart-water/tests
echo "Files copied correctly (no tests/): OK"

echo "=== Verify permissions ==="
test -f /var/lib/smart-water/auth.json
DIR_PERM="$(stat -c '%a %U:%G' /var/lib/smart-water)"
FILE_PERM="$(stat -c '%a %U:%G' /var/lib/smart-water/auth.json)"
echo "Directory: ${DIR_PERM}"
echo "Auth file: ${FILE_PERM}"
test "${DIR_PERM}" = "750 smartwater:smartwater"
test "${FILE_PERM}" = "600 smartwater:smartwater"
echo "Permissions verified: OK"

TOKEN1="$(cat /var/lib/smart-water/auth.json)"

echo "=== TEST 2: Idempotent re-run ==="
./install.sh
TOKEN2="$(cat /var/lib/smart-water/auth.json)"
test "${TOKEN1}" = "${TOKEN2}"
echo "Token preserved across re-run: OK"

echo "=== TEST 3: Uninstall keep data ==="
./install.sh --uninstall-keep-data
test ! -d /opt/smart-water
test -f /var/lib/smart-water/auth.json
test -f /etc/smart-water/config.yaml
echo "Uninstall keep data verified: OK"

echo "=== ALL CONTAINER TESTS PASSED SUCCESSFULLY ==="
