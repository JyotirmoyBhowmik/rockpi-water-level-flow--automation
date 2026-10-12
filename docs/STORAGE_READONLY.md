# Storage Architecture, Read-Only Root, Backups & Update System

This document outlines the flash-memory resilience, SQLite database tuning, backup automation, A/B application update workflow, and firmware security policies for the **Radxa ROCK Pi S Smart Water & Precision Irrigation Controller**.

---

## 1. Storage Architecture & Flash Endurance

Headless embedded controllers operating in outdoor or pump house utility cabinets are vulnerable to sudden power outages, lightning-induced grid surges, and SD card flash wear. MicroSD and eMMC flash memory degrade rapidly when subjected to high-frequency synchronous writes.

To guarantee indefinite field reliability, the controller implements a decoupled storage model:

```
+-------------------------------------------------------------------------------+
|                               ROCK PI S STORAGE                               |
+-------------------------------------------------------------------------------+
|  PARTITION 1 (/boot)          : Read-Only bootloader & kernel dtb             |
|  PARTITION 2 (/)              : Read-Only Root Filesystem (overlayroot tmpfs)  |
|                                 - /opt/smart-water (Symlink to A/B Slot)      |
|                                 - /opt/smart-water-a (Slot A binary image)    |
|                                 - /opt/smart-water-b (Slot B binary image)    |
|                                 - /etc/smart-water/config.yaml (Bootstrap)    |
+-------------------------------------------------------------------------------+
|  PARTITION 3 (/var/lib/...)   : Dedicated Persistent Writable ext4 Partition  |
|                                 - Mount: /var/lib/smart-water                 |
|                                 - Options: noatime,commit=60,errors=remount-ro|
|                                 - system.db & system.db-wal (SQLite)          |
|                                 - settings.yaml & auth.json (Runtime state)   |
|                                 - backups/ (Rotated 7-day atomic backups)     |
+-------------------------------------------------------------------------------+
|  RAM (tmpfs / zram)           : Volatile System Logs & PID Sockets            |
|                                 - /run/smart-water                            |
|                                 - /var/log (log2ram or systemd-journald)      |
+-------------------------------------------------------------------------------+
```

---

## 2. Read-Only Root Filesystem with `overlayroot`

Armbian and Debian 12 Bookworm support `overlayroot`, which mounts the root partition (`/`) as read-only and overlays a RAM-backed `tmpfs` union filesystem. Any writes to system paths (such as `/etc` or `/tmp`) exist solely in RAM and are discarded upon reboot, completely eliminating filesystem corruption on sudden power cuts.

### 2.1 Enabling `overlayroot`
1. Ensure `/var/lib/smart-water` is mounted on a dedicated partition in `/etc/fstab`:
   ```fstab
   UUID=xxxx-xxxx-xxxx  /var/lib/smart-water  ext4  noatime,commit=60,errors=remount-ro  0  2
   ```
2. Install the `overlayroot` package:
   ```bash
   sudo apt-get install -y overlayroot
   ```
3. Configure `/etc/overlayroot.conf`:
   ```conf
   overlayroot="tmpfs:swap=1,recurse=0"
   ```
4. Reboot the ROCK Pi S:
   ```bash
   sudo reboot
   ```
5. Verify with `df -h`: root `/` will be mounted as `overlayroot`.

### 2.2 Performing Maintenance on Read-Only Root
To update system packages or modify bootstrap configuration:
```bash
# Enter chroot with direct write access to the underlying block device:
sudo overlayroot-chroot

# Perform maintenance, then exit:
exit
sudo reboot
```

---

## 3. `log2ram` Alternative (RAM-Based Logging)

If full `overlayroot` is not desired or while developing on a standard writable root, **log2ram** provides a lightweight mechanism to eliminate flash drive degradation:

1. `log2ram` creates a `zram` / `tmpfs` mount at `/var/log`.
2. All `systemd-journald` output and syslog entries are written directly into RAM.
3. A daily cron job synchronizes logs back to disk before rotation.

### 3.1 Installation & Configuration
```bash
curl -fsSL https://github.com/azlux/log2ram/archive/master.tar.gz | tar xz
cd log2ram-master
sudo ./install.sh
```

Configure `/etc/log2ram.conf`:
```conf
SIZE=50M
USE_ZRAM=true
LOG_DISK_SIZE=100M
```
Activate service:
```bash
sudo systemctl enable --now log2ram
```

Combined with the systemd journal cap (`SystemMaxUse=50M` deployed in `/etc/systemd/journald.conf.d/smart-water.conf`), memory consumption is strictly bounded.

---

## 4. SQLite Production Configuration & Backups

The SQLite engine stores runtime lockouts, audit events, downsampled telemetry, user rules, and energy records in `/var/lib/smart-water/system.db`.

### 4.1 Pragmas & Concurrency Tuning
The database connection pool enforces the following pragmas on every connection:
- **`PRAGMA journal_mode = WAL;`**: Write-Ahead Logging allows concurrent readers to query historical data without blocking the asynchronous writer task.
- **`PRAGMA busy_timeout = 5000;`**: Configurable via `system_config.database.busy_timeout_ms` (default 5000 ms). Eliminates `database is locked` exceptions during concurrent burst writes.
- **`PRAGMA synchronous = NORMAL;`**: In WAL mode, `NORMAL` guarantees database integrity across abrupt power cuts and OS crashes while eliminating ~90% of disk sync operations compared to `FULL`.
- **`PRAGMA cache_size = -2000;`**: Bounds SQLite in-memory cache to ~2 MB, preserving available RAM for the Rock Pi S.
- **`PRAGMA temp_store = MEMORY;`**: Forces temporary tables and indices into RAM.
- **`PRAGMA auto_vacuum = INCREMENTAL;`**: Enables space reclamation via background `incremental_vacuum` without table locks.

### 4.2 Nightly Atomic `VACUUM INTO` Backups
Every 24 hours, the scheduler runs `state_manager.run_nightly_backup()`:
1. Calls `VACUUM INTO '/var/lib/smart-water/backups/smart_water_backup_YYYYMMDD_HHMMSS.db'`.
   - `VACUUM INTO` creates an atomic, defragmented snapshot of the live database without stopping readers or writers.
2. Applies strict `0600` permissions (`smartwater:smartwater`).
3. Prunes backups older than the configured retention window (default: 7 days, keeping at least the latest backup).
4. **Optional Cloud Sync (`rclone`)**: If `system_config.database.rclone_remote` is configured (e.g. `gdrive:smart-water-backups` or `b2:mybucket/smart-water`), the controller executes:
   ```bash
   rclone copy /var/lib/smart-water/backups/smart_water_backup_YYYYMMDD_HHMMSS.db <rclone_remote>
   ```

### 4.3 Manual On-Demand Backup & Restore CLI
```bash
# Trigger an immediate atomic backup:
python -m app.cli backup

# Restore from a backup archive:
python -m app.cli restore /var/lib/smart-water/backups/smart_water_backup_20261013_020000.db
```

---

## 5. Safe A/B Updates with Automatic Rollback

Production installations use an A/B directory scheme to prevent bricking the controller during remote updates over Tailscale:
- Slot A: `/opt/smart-water-a`
- Slot B: `/opt/smart-water-b`
- Active Pointer: `/opt/smart-water` (symlink pointing to Slot A or Slot B)

### 5.1 Update Command Workflow
```bash
python -m app.cli update --from release-1.2.1.tar.gz --sha256 <expected-hex-digest>
```

#### Step-by-Step Update Execution:
1. **Cryptographic Verification**:
   - Computes the SHA-256 hash of the tarball.
   - Compares against `--sha256` or an adjacent `.sha256` file. If hashes do not match, the process halts immediately with exit code 1.
2. **Target Slot Detection**:
   - Inspects `/opt/smart-water` symlink target.
   - If Slot A is active, targets Slot B. If Slot B is active, targets Slot A.
3. **Safe Sandbox Extraction**:
   - Wipes and recreates the inactive target slot.
   - Validates each tar member against directory traversal (`..` or root escapes) before unpacking.
   - Preserves virtualenv from the active slot.
4. **Atomic Symlink Flip**:
   - Switches `/opt/smart-water` symlink to the target slot.
5. **Service Restart & Health Check**:
   - Restarts `smart-water.service` via `systemctl restart smart-water.service`.
   - Polls `http://127.0.0.1:8000/healthz` every second for up to 20 seconds.
6. **Automatic Rollback**:
   - If `/healthz` fails to respond with `200 OK` within the timeout window, the CLI automatically:
     1. Reverts `/opt/smart-water` symlink back to the previous slot.
     2. Restarts `smart-water.service`.
     3. Exits with code 1, reporting failure and rollback confirmation.

---

## 6. Firmware OTA Policy for Garden Node (ESP-NOW)

> [!CAUTION]
> **FIRMWARE OTA FOR THE GARDEN NODE OVER ESP-NOW IS STRICTLY OUT OF SCOPE.**

### 6.1 Security Rationale
1. **Packet Constraints**: ESP-NOW uses unencrypted or single-key 802.11 vendor action frames with a maximum payload of 250 bytes. Chunking binary firmware across unreliable 2.4 GHz RF links without end-to-end transport acknowledgement exposes the node to flashing corruption and bricking.
2. **Safety Criticality**: The garden node controls high-current solenoid valves and submersible pump relays. Flashing unsigned or incomplete firmware can cause solenoids to remain permanently open, draining reservoirs or burning out pumps.
3. **Sole Supported Flashing Method**: Node and gateway firmware updates are strictly restricted to physical USB-UART programming via PlatformIO:
   ```bash
   pio run -d firmware/node -t upload
   ```

### 6.2 Pre-requisites for Any Future OTA Proposal
Firmware updates over radio will only be evaluated if all of the following requirements are satisfied:
1. **Signed Binary Images**: Firmware images must be cryptographically signed using asymmetric ECDSA (secp256r1) with private keys held exclusively in an offline HSM or CI release vault, verified in hardware by the ESP32-C3 bootloader (`esp_https_ota` standard).
2. **Dual-Partition Rollback**: Partition table must contain `ota_0` and `ota_1`. The hardware RTC watchdog must automatically revert to the previous partition if the new firmware fails to boot or acknowledge gateway communication within 60 seconds.
3. **Physical Safety Switch**: A physical jumper on the garden node PCB must be closed to permit OTA flashing mode.
