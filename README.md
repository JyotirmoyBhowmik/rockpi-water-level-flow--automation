# Radxa ROCK Pi S Smart Water & Precision Irrigation Controller

[![Linux](https://img.shields.io/badge/Platform-Armbian%20Debian%2012%20(Bookworm)-blue.svg)](https://armbian.com)
[![Hardware](https://img.shields.io/badge/SoC-Rockchip%20RK3308B%20(512MB)-orange.svg)](https://radxa.com)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![ESP-NOW](https://img.shields.io/badge/Wireless-ESP--NOW%20Encrypted%20Unicast-brightgreen.svg)](https://espressif.com)
[![License](https://img.shields.io/badge/License-MIT-lightgrey.svg)](LICENSE)

An industrial-grade, dual-layer, hybrid IoT automation controller designed for the **Radxa ROCK Pi S** (Rockchip RK3308, 512MB RAM) running headless Armbian Debian 12 (Bookworm).

The system reliably orchestrates:
1. **Domestic Overhead Water Tank Management:** Automated replenishment via a 25A AC contactor switching a 230V submersible pump, protected by physical electromechanical interlocks, hardware-level dry-sump protection, anti-short-cycling timers, and auxiliary contact welded-relay detection.
2. **Precision Wireless 4-Zone Drip Irrigation:** Remote battery/solar garden node driven by an ESP32-C3 Super Mini communicating via encrypted unicast ESP-NOW with an onboard USB gateway. Features 4x capacitive soil moisture probes digitized by an ADS1115 16-bit ADC, 4x 12V submersible pumps with hardware cutoffs, a reservoir dry-run float switch, and a pulse flow meter on Zone 1.
3. **Dual-Layer Defense-in-Depth:** Hardware safety interlocks completely de-energize contactor coils independently of Linux software. Software failsafes enforce strict runtime caps, mandatory motor cooldowns, and per-sensor staleness isolation.
4. **Security & Zero-Trust Architecture:** Strictly private Headscale/Tailscale mesh access, constant-time SHA-256 token verification, single-use 60s SSE streaming tickets, SSRF-guarded webhooks, and zero default keys.
5. **Ultra-Low Resource Footprint:** Designed strictly for 512MB RAM boards using FastAPI + uvicorn (plain h11), SQLite WAL mode (~2MB cache), and Alpine.js frontend. Enforced by systemd cgroups (`MemoryHigh=128M`, `MemoryMax=200M`).

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Bill of Materials (BOM) & Sourcing](#2-bill-of-materials-bom--sourcing)
3. [Electrical Schematics & Hardware Wiring](#3-electrical-schematics--hardware-wiring)
   - [3.1 Rock Pi S 26-Pin Header Pinout](#31-rock-pi-s-26-pin-header-pinout)
   - [3.2 Dual-Layer Interlock Chain & Truth Table](#32-dual-layer-interlock-chain--truth-table)
   - [3.3 25A Contactor & Auxiliary Feedback Wiring](#33-25a-contactor--auxiliary-feedback-wiring)
   - [3.4 ESP32-C3 Gateway & Garden Node Wiring](#34-esp32-c3-gateway--garden-node-wiring)
4. [Step-by-Step Beginner Installation Guide](#4-step-by-step-beginner-installation-guide)
   - [Step 1: Flash Armbian Bookworm with balenaEtcher](#step-1-flash-armbian-bookworm-with-balenaetcher)
   - [Step 2: First Boot, Serial/SSH Access & Tailscale Setup](#step-2-first-boot-serialssh-access--tailscale-setup)
   - [Step 3: Enable Hardware Overlays (I2C7 & Watchdog)](#step-3-enable-hardware-overlays-i2c7--watchdog)
   - [Step 4: Run the One-Click Installer (`install.sh`)](#step-4-run-the-one-click-installer-installsh)
   - [Step 5: Generate Cryptographic ESP-NOW Secrets](#step-5-generate-cryptographic-esp-now-secrets)
   - [Step 6: Build & Flash ESP32-C3 Firmware via PlatformIO](#step-6-build--flash-esp32-c3-firmware-via-platformio)
   - [Step 7: First-Run Web Setup Wizard](#step-7-first-run-web-setup-wizard)
   - [Step 8: Calibration (Soil Moisture & Pulse Flow Meter)](#step-8-calibration-soil-moisture--pulse-flow-meter)
5. [Pre-Commissioning & Commissioning Checklist](#5-pre-commissioning--commissioning-checklist)
6. [Comprehensive Troubleshooting Runbook (35 Scenarios)](#6-comprehensive-troubleshooting-runbook-35-scenarios)
7. [Protocol v1.1 Wire Specification](#7-protocol-v11-wire-specification)
8. [REST & SSE Telemetry API Reference](#8-rest--sse-telemetry-api-reference)
9. [Maintenance, Backup & Token Rotation](#9-maintenance-backup--token-rotation)

---

## 1. System Architecture

```
+===================================================================================+
|                              HEAD-END (ROCK Pi S)                                 |
|                                                                                   |
|  [Armbian Debian 12 Minimal]                                                      |
|   ├── Linux Kernel Watchdog (/dev/watchdog pinged every 5s)                       |
|   ├── Dedicated Service User 'smartwater' (MemoryMax=200M, MemoryHigh=128M)       |
|   ├── DS3231 Precision I2C RTC (/dev/i2c-7 at 0x68)                              |
|   ├── CM108 USB Audio DAC Adapter (ALSA voice alerts via espeak-ng)               |
|   └── Single Async Python 3.11 Runtime (FastAPI + SQLite WAL + Alpine.js UI)      |
|                                                                                   |
|  [Hardware Protection Layer]                                                      |
|   ├── 4-Channel PC817 Optocoupler Isolation (3.3V Logic Safe)                     |
|   │     IN1: Tank Low Float (NO)       IN2: Interlock Output Tap                  |
|   │     IN3: Interlock Midpoint Tap    IN4: Conductive Leak Sensor                |
|   └── 25A AC Contactor Driver (12V/230V coil) with NO Auxiliary Contact Feedback  |
+===================================================================================+
                                       │
                         USB Serial (/dev/garden-gateway)
                           115200 Baud, JSON Lines v1.1
                                       │
+======================================▼============================================+
|                       USB GATEWAY (ESP32-C3 Super Mini)                           |
|  - Encrypted Unicast ESP-NOW (PMK + LMK, MAC allowlisted)                        |
|  - Protocol framing, HMAC-SHA256 signature pass-through                           |
|  - Hardware watchdog (5s timeout)                                                |
+===================================================================================+
                                       │
                      2.4 GHz Encrypted Unicast ESP-NOW
                                       │
+======================================▼============================================+
|                    GARDEN IRRIGATION NODE (ESP32-C3 Super Mini)                   |
|                                                                                   |
|  - 1x ADS1115 16-Bit I2C ADC (0x48): 4x Capacitive Soil Probes (8-sample median)  |
|  - 4x Logic-Level N-MOSFETs: 12V DC Micro Submersible Drip Pumps                  |
|  - 1x Reed Float Switch: Irrigation Reservoir low-level dry cutoff                |
|  - 1x YF-S201 Hall-Effect Pulse Flow Meter on Zone 1 (with 5V->3.3V divider)      |
|  - Hardwired Firmware Rules:                                                      |
|      * Max 60s pump pulse execution                                               |
|      * Max 2 concurrent pumps on DC bus                                           |
|      * Immediate pump cut on reservoir dry float open                             |
|      * Immediate pump cut on 60s radio link loss                                  |
+===================================================================================+
```

---

## 2. Bill of Materials (BOM) & Sourcing

| Category | Component | Quantity | Specification / Model | Est. Cost (USD) | Function |
| :--- | :--- | :---: | :--- | :---: | :--- |
| **SBC** | Radxa ROCK Pi S | 1 | 512MB RAM, RK3308B, Type-C Power | $18.00 | Central Automation Head-End |
| **Storage** | Micro-SD Card | 1 | 16GB or 32GB High Endurance Class 10 | $6.50 | OS and SQLite WAL storage |
| **Microcontroller** | ESP32-C3 Super Mini | 2 | RISC-V Single Core, Type-C, Onboard Ceramic Ant | $6.00 | Gateway & Garden Node |
| **Isolation** | PC817 Optocoupler Board | 1 | 4-Channel 12V/5V to 3.3V Galvanic Isolator | $2.50 | Float switches & leak sensor isolation |
| **Power Relay** | AC Contactor | 1 | 2-Pole 25A 230V AC Coil (with NO Aux Contact) | $12.00 | Main overhead AC pump switching |
| **Pilot Relay** | Relay Driver Module | 1 | 1-Channel 5V/12V Optocoupled Relay Module | $1.50 | Drives AC contactor coil |
| **ADC Module** | ADS1115 ADC | 1 | 16-Bit 4-Channel I2C ADC Module (Address 0x48) | $3.00 | High-resolution soil moisture sampling |
| **Sensors** | Capacitive Soil Probes | 4 | Corrosion-Resistant Capacitive Moisture v1.2/v2.0 | $6.00 | Zone 1-4 soil moisture readings |
| **Sensors** | Float Switches | 3 | Stainless Steel / PP Vertical Float Switches | $6.00 | Full Float (NC), Sump (NO), Reservoir (NO) |
| **Sensors** | Flow Meter | 1 | YF-S201 Hall-Effect Pulse Flow Meter | $4.50 | Zone 1 flow rate & leak monitoring |
| **Sensors** | Leak Sensor | 1 | Conductive Grid Pad / Gold-Plated Water Sensor | $1.50 | Drip manifold leak detection |
| **Drivers** | MOSFET Module | 1 | 4-Channel Logic-Level N-MOSFET (LR7843 / AO3400) | $3.50 | Low-side switching of 12V DC drip pumps |
| **Pumps** | 12V Submersible Pumps | 4 | 12V DC 2A Micro Submersible Drip Pumps | $14.00 | Precision irrigation delivery |
| **RTC** | DS3231 I2C RTC | 1 | Precision TCXO RTC Module + CR2032 Battery | $2.50 | Monotonic time during internet loss |
| **Audio** | USB Audio DAC | 1 | CM108 USB Audio Adapter + Powered Speaker | $4.50 | Synthesized voice alerts (espeak-ng) |
| **Total** | - | - | - | **~$90.00** | Complete Industrial Automation |

---

## 3. Electrical Schematics & Hardware Wiring

> [!CAUTION]
> **3.3V Logic Level Warning:** The Rockchip RK3308 GPIO inputs operate strictly at 3.3V. Applying 5V or 12V directly to RK3308 pins will permanently destroy the SoC. Always pass external sensor lines through the PC817 optoisolator board.

### 3.1 Rock Pi S 26-Pin Header Pinout

Connect the optocoupler board, pilot relay, and I2C peripherals to Header 1:

```
                      Radxa ROCK Pi S - Header 1 (26-Pin)
                   +---------------------------------------+
   3.3V Power (1)  | [01] [02] | (2)  5V Power (Fused)
   I2C7_SDA   (3)  | [03] [04] | (4)  5V Power
   I2C7_SCL   (5)  | [05] [06] | (6)  Ground
  GPIO1_B2/IN1 (7) | [07] [08] | (8)  UART0_TX (Debug)
  GPIO1_B3/IN2 (9) | [09] [10] | (10) UART0_RX (Debug)
 GPIO1_B4/IN3 (11) | [11] [12] | (12) GPIO1_B5/IN4
              (13) | [13] [14] | (14) Ground
GPIO1_C0/Relay(15) | [15] [16] | (16) GPIO1_C1 (Contactor Aux Feedback)
              (17) | [17] [18] | (18) Ground
              (19) | [19] [20] | (20) Ground
              (21) | [21] [22] | (22) Ground
              (23) | [23] [24] | (24) Ground
              (25) | [25] [26] | (26) Ground
                   +---------------------------------------+
```

| Signal | Header Pin | Pin Label | Connected Peripheral | Logic Level |
| :--- | :---: | :--- | :--- | :--- |
| **3.3V VCC** | Pin 1 | 3V3 | PC817 Collector Pull-up Rail | 3.3V DC |
| **I2C7 SDA** | Pin 3 | GPIO1_B0 | DS3231 RTC SDA | 3.3V DC (Pull-up) |
| **I2C7 SCL** | Pin 5 | GPIO1_B1 | DS3231 RTC SCL | 3.3V DC (Pull-up) |
| **IN1 (Tank Low)** | Pin 7 | GPIO1_B2 | PC817 Opto Output 1 | Active LOW (Pull-up) |
| **IN2 (Interlock Out)** | Pin 9 | GPIO1_B3 | PC817 Opto Output 2 | Active HIGH |
| **IN3 (Interlock Mid)** | Pin 11 | GPIO1_B4 | PC817 Opto Output 3 | Active HIGH |
| **IN4 (Leak Sensor)** | Pin 12 | GPIO1_B5 | PC817 Opto Output 4 | Active HIGH (Leak = HIGH) |
| **Pilot Relay Out** | Pin 15 | GPIO1_C0 | Pilot Relay IN (Triggers Contactor Coil) | Active HIGH |
| **Contactor Aux In** | Pin 16 | GPIO1_C1 | Contactor NO Auxiliary Contact (via Opto) | Active HIGH |
| **GND** | Pin 6, 14 | GND | Common Digital Ground | 0V |

---

### 3.2 Dual-Layer Interlock Chain & Truth Table

The overhead tank replenishment system enforces physical safety by wiring the float switches directly in series with the pilot relay's VCC line:

```
 +5V DC Fused 
      │
      ▼
 [ Full Float Switch (NC) ]  <--- Opens when overhead tank reaches maximum capacity
      │
      ├──────────────────────> Tap wire to Opto IN3 (interlock_mid)
      ▼
 [ Sump Float Switch (NO) ]  <--- Closes only when source sump contains sufficient water
      │
      ├──────────────────────> Tap wire to Opto IN2 (interlock_out)
      ▼
 [ Pilot Relay Module VCC ]  <--- Power to relay coil physically broken if either float trips
```

#### Hardware Interlock Truth Table

| interlock_out (IN2) | interlock_mid (IN3) | Physical Water State | Hardware Reaction | Controller State |
| :---: | :---: | :--- | :--- | :--- |
| **HIGH (1)** | **HIGH (1)** | Both floats closed. Water available in sump, space in tank. | Pilot relay powered; motor start allowed. | `healthy` |
| **LOW (0)** | **LOW (0)** | Overhead tank full (Full float opened circuit at top). | Pilot relay VCC cut; motor instantly de-energized. | `tank_full` |
| **LOW (0)** | **HIGH (1)** | Sump dry (Sump float opened circuit). | Pilot relay VCC cut; motor instantly de-energized. | `dry_sump` |
| **HIGH (1)** | **LOW (0)** | **IMPOSSIBLE STATE** (Midpoint low while output high). | **Hardware wiring fault (cut wire or short circuit).** | `wiring_fault` (Alarm & Lockout) |

---

### 3.3 25A Contactor & Auxiliary Feedback Wiring

1. **Main Line Switching:** Wire 230V AC Live (L) and Neutral (N) through the 25A contactor's main poles (L1/T1 and L2/T2) to the overhead tank pump motor.
2. **Pilot Relay:** The ROCK Pi S pin `GPIO1_C0` drives the optocoupled pilot relay module. When energized, the pilot relay closes the circuit supplying 230V (or 12V/24V) to the contactor's `A1/A2` coil terminals.
3. **Auxiliary Feedback Contact:** The contactor features a Normally Open (NO) auxiliary contact (`13/14`). Wire this contact through an optocoupler channel to `GPIO1_C1`. The software checks this feedback:
   - If the coil is commanded ON but `GPIO1_C1` does not go HIGH within 500ms, an alert `CONTACTOR_FAILED_TO_PULL` is raised.
   - If the coil is commanded OFF but `GPIO1_C1` remains HIGH, a critical alarm `CONTACTOR_WELDED_CONTACTS` is raised immediately.

---

### 3.4 ESP32-C3 Gateway & Garden Node Wiring

#### Gateway ESP32-C3
Connect the Gateway ESP32-C3 directly to the ROCK Pi S USB Type-A host port using a shielded USB Type-C data cable. The ROCK Pi S udev rules map this device to `/dev/garden-gateway`.

#### Garden Node ESP32-C3
```
ESP32-C3 Super Mini
 ├── 5V IN        <--- 12V-to-5V Step-Down Buck Converter (from 12V garden battery)
 ├── GND          <--- Common DC Ground
 ├── GPIO 0       <--- MOSFET Gate 1 (Zone 1 Drip Pump)
 ├── GPIO 1       <--- MOSFET Gate 2 (Zone 2 Drip Pump)
 ├── GPIO 3       <--- MOSFET Gate 3 (Zone 3 Drip Pump)
 ├── GPIO 10      <--- MOSFET Gate 4 (Zone 4 Drip Pump)
 ├── GPIO 4       <--- Zone 1 Flow Meter Signal (via 10k/20k Voltage Divider from 5V)
 ├── GPIO 5       <--- Reservoir Reed Float Switch (to GND with internal pull-up)
 ├── GPIO 6 (SDA) <--- ADS1115 I2C SDA (4.7k Pull-up to 3.3V)
 └── GPIO 7 (SCL) <--- ADS1115 I2C SCL (4.7k Pull-up to 3.3V)

ADS1115 16-Bit ADC (0x48)
 ├── VDD          <--- 3.3V DC
 ├── GND          <--- Common Ground
 ├── SCL/SDA      <--- Connected to ESP32-C3 GPIO 7/6
 ├── A0           <--- Capacitive Soil Probe 1 Analog Output
 ├── A1           <--- Capacitive Soil Probe 2 Analog Output
 ├── A2           <--- Capacitive Soil Probe 3 Analog Output
 └── A3           <--- Capacitive Soil Probe 4 Analog Output
```

> [!IMPORTANT]
> **Flow Meter Voltage Divider:** The YF-S201 flow meter runs on 5V and outputs 5V square wave pulses. Do NOT connect its pulse output directly to ESP32-C3 GPIO 4! Use a voltage divider: a 10kΩ resistor between the flow meter pulse wire and GPIO 4, and a 20kΩ resistor between GPIO 4 and GND. This steps the 5V pulse down to a safe 3.3V.

---

## 4. Step-by-Step Beginner Installation Guide

### Step 1: Flash Armbian Bookworm with balenaEtcher

1. Download the official **Armbian Debian 12 (Bookworm) Minimal Headless** image for Radxa ROCK Pi S from [Armbian Downloads](https://www.armbian.com/rockpi-s/).
2. Insert your Micro-SD card into your computer's card reader.
3. Download and open [balenaEtcher](https://etcher.balena.io/).
4. Select the downloaded `.img.xz` file, select your Micro-SD card target, and click **Flash!**.
5. Once flashing and verification complete, safely eject the Micro-SD card.

---

### Step 2: First Boot, Serial/SSH Access & Tailscale Setup

1. Insert the flashed Micro-SD card into the ROCK Pi S.
2. Connect the ROCK Pi S to your local network via an Ethernet cable.
3. Connect a quality 5V 2A USB Type-C power supply to the ROCK Pi S power port.
4. Allow 60 seconds for the initial boot and filesystem expansion.
5. Discover the board's IP address on your local router or connect a USB-to-UART adapter to pins 8/10 (UART0_TX/UART0_RX) at 1500000 baud.
6. SSH into the board:
   ```bash
   ssh root@<rockpi-s-ip>
   # Default password on fresh Armbian is: 1234
   ```
7. Follow the prompts to create your personal administrator user and set the root password.
8. Install and configure **Tailscale** for private mesh access:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up --accept-routes
   # Or for private Headscale:
   # sudo tailscale up --login-server=https://headscale.yourdomain.com --accept-routes
   ```
9. Verify your assigned Tailscale IP address:
   ```bash
   tailscale ip -4
   # Example: 100.64.0.15
   ```

---

### Step 3: Enable Hardware Overlays (I2C7 & Watchdog)

The DS3231 RTC module requires hardware I2C bus 7, and the hardware failsafe requires `/dev/watchdog`.

1. Open `/boot/armbianEnv.txt` in a text editor:
   ```bash
   sudo nano /boot/armbianEnv.txt
   ```
2. Ensure the `overlays` line includes `i2c7`:
   ```ini
   overlays=i2c7
   ```
3. Save the file (`Ctrl+O`, `Enter`, `Ctrl+X`) and reboot the board:
   ```bash
   sudo reboot
   ```
4. After reconnecting via SSH, verify the peripherals:
   ```bash
   # Check I2C bus 7 is active:
   sudo i2cdetect -y 7
   # The DS3231 RTC should show at address 0x68 (or 'UU')

   # Check kernel watchdog character device:
   ls -l /dev/watchdog
   ```

---

### Step 4: Run the One-Click Installer (`install.sh`)

1. Clone this repository into `/opt/smart-water`:
   ```bash
   sudo git clone https://github.com/JyotirmoyBhowmik/rockpi-water-level-flow--automation.git /opt/smart-water
   cd /opt/smart-water
   ```
2. Execute the production installer:
   ```bash
   sudo ./install.sh
   ```
3. The installer automatically performs:
   - Operating system validation (strictly validates Armbian Debian 12 Bookworm).
   - System dependency installation (`python3-libgpiod`, `gpiod`, `espeak-ng`, `alsa-utils`, `sqlite3`).
   - Creation of the unprivileged system user `smartwater` with necessary group memberships (`gpio`, `i2c`, `dialout`, `audio`).
   - Virtualenv setup with `--system-site-packages` (ensuring zero pip compilation errors for gpiod).
   - Python dependency installation from `requirements.txt` (pinned uvicorn + h11, FastAPI, Pydantic).
   - Deployment of `/etc/udev/rules.d/99-smart-water.rules` creating the stable symlink `/dev/garden-gateway`.
   - Setup of configuration and state directories:
     * `/etc/smart-water/config.yaml` (0640, root:smartwater, read-only bootstrap).
     * `/var/lib/smart-water/` (0750, smartwater:smartwater, settings and SQLite database).
   - Registration and startup of the hardened systemd service `smart-water.service`.

4. Check the service status:
   ```bash
   sudo systemctl status smart-water.service
   ```

---

### Step 5: Generate Cryptographic ESP-NOW Secrets

The ESP-NOW wireless link uses encrypted unicast communication (CCMP encryption with PMK/LMK keys) and an HMAC-SHA256 application signature. Zero default keys exist in the repository.

1. Generate fresh cryptographically random keys using the controller CLI:
   ```bash
   sudo /opt/smart-water/venv/bin/python -m app.cli gen-espnow-secrets
   ```
2. The command will output key definitions formatted for C++ and Python:
   ```
   ======================================================================
   GENERATED SECURE ESP-NOW & HMAC KEYS
   ======================================================================
   Wrote keys to /etc/smart-water/espnow.key (mode 0600)

   Copy these definitions into firmware/gateway/src/secrets.h
   and firmware/node/src/secrets.h:
   ----------------------------------------------------------------------
   #define ESPNOW_PMK_KEY {0x1a, 0x2b, 0x3c, ...}
   #define ESPNOW_LMK_KEY {0x4d, 0x5e, 0x6f, ...}
   #define HMAC_SHARED_KEY "9f8e7d6c5b4a..."
   ----------------------------------------------------------------------
   ```
3. Create `firmware/gateway/src/secrets.h` and `firmware/node/src/secrets.h` with the printed definitions. (Both files are ignored by git to prevent secret leakage).

---

### Step 6: Build & Flash ESP32-C3 Firmware via PlatformIO

Install PlatformIO on your workstation or directly on the SBC:
```bash
pip install platformio
```

1. **Find ESP32 MAC Addresses:**
   Connect each ESP32-C3 to your computer. Open a serial monitor or run an esptool read-mac command:
   ```bash
   python -m esptool --port /dev/ttyACM0 read_mac
   ```
   Set the gateway MAC address in `firmware/node/include/config.h` and the node MAC address in `firmware/gateway/include/config.h`.

2. **Build and Flash the Gateway:**
   Connect the Gateway ESP32-C3 to your computer via USB:
   ```bash
   python -m platformio run -d firmware/gateway --target upload
   ```

3. **Build and Flash the Garden Node:**
   Connect the Garden Node ESP32-C3 to your computer via USB:
   ```bash
   python -m platformio run -d firmware/node --target upload
   ```

4. Plug the Gateway ESP32-C3 into the ROCK Pi S USB port. Confirm the udev symlink is created:
   ```bash
   ls -l /dev/garden-gateway
   ```

---

### Step 7: First-Run Web Setup Wizard

On first boot with unconfigured GPIO lines, the controller boots safely into `SAFE-NO-HARDWARE` mode with all pumps and relays de-energized.

1. Open your browser and navigate to:
   ```
   http://<tailscale-ip>:8000
   ```
2. The **First-Run Commissioning Wizard** will guide you through:
   - **Step 1 - Pin Mapping:** Select detected GPIO line offsets for IN1, IN2, IN3, IN4, Pilot Relay, and Aux Feedback.
   - **Step 2 - Opto Input Live Test:** Test your float switches. Raise and lower the floats manually to confirm the UI reflects state changes in real time.
   - **Step 3 - Contactor Dry Click Test:** Click the "Test Relay (0.5s Pulse)" button. Listen for the contactor pulling in and releasing, and confirm auxiliary contact verification.
   - **Step 4 - Gateway & Node Verification:** Verify incoming `gw_hb` and `hb` packets from the garden node.
   - **Step 5 - Soil Probe Calibration:** Complete the dry and wet calibration steps.
   - **Step 6 - Finish:** Writes your verified settings to `/var/lib/smart-water/settings.yaml`, sets `setup_complete: true`, and prompts for service restart.

3. Restart the service to apply the verified hardware configuration:
   ```bash
   sudo systemctl restart smart-water.service
   ```

---

### Step 8: Calibration (Soil Moisture & Pulse Flow Meter)

#### 8.1 Capacitive Soil Moisture Probes
Capacitive probes output analog voltage inversely proportional to moisture: dry air = ~2.6V, saturated water = ~1.2V.
1. In the Web UI, go to **Settings > Calibration**.
2. **Dry Calibration:** Hold probe in dry ambient air. Click **Capture Dry (Air)**.
3. **Wet Calibration:** Submerge probe into a glass of tap water up to the marked line. Click **Capture Wet (Water)**.
4. Repeat for Zones 1 through 4. The system calculates a `0.4V` sanity margin around `[wet_v .. dry_v]`.

#### 8.2 Zone 1 Flow Meter Calibration Wizard
1. Ensure the irrigation reservoir is filled with water.
2. In the Web UI dashboard, click **Calibrate Zone 1 Flow**.
3. The system runs Zone 1 pump for exactly 10 seconds, counts the returned flow pulses, calculates the baseline flow rate in L/min, and sets `flow_min_lpm` to a safe 50% cutoff threshold.
4. If observed flow is below 0.3 L/min, the wizard flags a warning for inspection and prevents saving false calibration.

---

## 5. Pre-Commissioning & Commissioning Checklist

Before turning on water supply mains or energizing the 230V AC pump circuit, complete this 12-point checklist:

- [ ] **1. Ground & Polarity Isolation:** Verify common ground between 12V DC power supply, ESP32-C3, and ADS1115. Ensure AC mains are completely isolated from SBC DC ground.
- [ ] **2. Float Switch Directionality:** Verify Full Float switch contacts OPEN when lifted to top (NC), and Sump Float contacts CLOSE when floating in water (NO).
- [ ] **3. Interlock Truth Table Verification:** Test all 4 float permutations. Verify that disconnecting the Full float or Sump float instantly cuts VCC to the pilot relay coil.
- [ ] **4. Wiring Fault Trip Test:** Disconnect the tap wire to IN3 while keeping IN2 connected. Verify controller displays `WIRING FAULT` alarm and locks motor start.
- [ ] **5. Contactor Aux Feedback Check:** Perform a dry click test (`POST /api/test/relay`). Confirm auxiliary contact opens and closes in synchronization with coil.
- [ ] **6. 15-Minute Runtime Cap:** Confirm in settings that `motor.max_runtime_s` is set to 900 seconds (15 min) with a 1800 second (30 min) cooldown window.
- [ ] **7. Flow Meter Divider:** Confirm 10k/20k voltage divider is installed on ESP32-C3 GPIO 4; verify voltage on GPIO 4 does not exceed 3.3V with an oscilloscope or multimeter.
- [ ] **8. Reservoir Low Cutoff:** Manually lower the reservoir float switch. Confirm all active plant pumps shut off within 1 second.
- [ ] **9. ESP-NOW Security:** Verify that `shared_key`, `pmk`, and `lmk` are not empty strings and match between SBC and firmware.
- [ ] **10. Watchdog Keepalive:** Verify `/dev/watchdog` is active. Kill the service with `kill -STOP <pid>` in a test environment; verify board reboots after 15 seconds.
- [ ] **11. Monotonic RTC Clock:** Disconnect Ethernet, reboot the board, and verify `timedatectl` maintains accurate monotonically increasing time from the DS3231.
- [ ] **12. Audio Alert Audibility:** Trigger a test speech alert (`python -m app.cli test-audio`). Verify volume level is clearly audible at the equipment location.

---

## 6. Comprehensive Troubleshooting Runbook (35 Scenarios)

| # | Subsystem | Symptom | Probable Root Cause | Diagnostic CLI Command | Step-by-Step Remediation |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **1** | Interlock | System boots into `SAFE-NO-HARDWARE` mode | GPIO chip or line offsets set to `null` in bootstrap config | `cat /etc/smart-water/config.yaml` | Complete the Web Setup Wizard or populate `/etc/smart-water/config.yaml` with valid GPIO chip path (`/dev/gpiochip1`) and line numbers. |
| **2** | Interlock | Status reports `WIRING FAULT` alarm | Impossible interlock state: `interlock_out` is HIGH while `interlock_mid` is LOW | `gpioget gpiochip1 <mid_pin> <out_pin>` | Inspect float wiring. Verify tap wire to IN3 is connected between Full float and Sump float, not swapped. |
| **3** | Interlock | Motor start rejected: `Overhead tank is already full` | Full float switch open or float jammed at upper limit | `gpioget gpiochip1 <out_pin> <mid_pin>` | Check overhead tank water level. If tank is not full, inspect float mechanism for debris or mineral encrustation. |
| **4** | Interlock | Motor start rejected: `Source sump is empty` | Sump float switch open (dry sump or low water table) | `gpioget gpiochip1 <out_pin> <mid_pin>` | Verify water level in source well/sump. Check float buoyancy and continuity across switch contacts. |
| **5** | Interlock | Motor trips after exactly 15 minutes of pumping | Continuous motor max-runtime limit exceeded (`max_runtime_s: 900`) | `journalctl -u smart-water -n 50 \| grep MOTOR_MAX_RUNTIME` | Check delivery pipe for leaks, airlocks, or closed valves preventing tank from filling within 15 min. Wait 30m cooldown then reset. |
| **6** | Interlock | Motor start rejected: `Motor is cooling down` | Mandatory cooldown window active following a max runtime trip | `curl -H "Authorization: Bearer $T" http://localhost:8000/api/telemetry/live` | Cooldown timer protects submersible pump motor windings. Wait until cooldown expires or execute emergency cooldown override in config. |
| **7** | Interlock | Motor start rejected: `Manual reset required via UI/API` | Operator has not cleared the lockout flag after cooldown expiry | `curl -X POST -H "Authorization: Bearer $T" http://localhost:8000/api/motor/reset` | Trigger `/api/motor/reset` endpoint or click "Reset Lockout" on the Web UI dashboard. |
| **8** | Hardware | Motor start rejected: `Leak sensor is tripped` | IN4 conductive pad detected water bridging contacts | `gpioget gpiochip1 <leak_pin>` | Inspect manifold and pump housing for burst pipes or hose leaks. Dry sensor probe contacts thoroughly. |
| **9** | Gateway | Status reports `LINK LOST` to Garden Gateway | USB cable disconnected, loose hub port, or missing udev symlink | `ls -l /dev/garden-gateway` or `dmesg \| grep -i tty` | Reseat USB cable. Verify `/etc/udev/rules.d/99-smart-water.rules` matches ESP32 VID:PID using `lsusb`. Run `udevadm trigger`. |
| **10** | Gateway | Error in logs: `Permission denied: /dev/garden-gateway` | User `smartwater` is not in the `dialout` system group | `groups smartwater` or `id smartwater` | Run `sudo usermod -aG dialout smartwater` and restart service: `sudo systemctl restart smart-water`. |
| **11** | Gateway | ESP-NOW packets not arriving from Node | Node and Gateway configured on different Wi-Fi channels | Check PlatformIO `ESPNOW_WIFI_CHANNEL` in `config.h` | Ensure both Gateway and Node firmware have identical `ESPNOW_WIFI_CHANNEL` (default channel 1). Re-flash if mismatched. |
| **12** | Telemetry | Garden data marked `STALE (>90s)` | Node out of RF range, battery depleted, or solar brownout | `journalctl -u smart-water -f \| grep garden` | Check 12V battery voltage on garden node. Move node closer or fit an external dipole antenna to ESP32-C3 IPEX connector. |
| **13** | Telemetry | Head-end data marked `STALE (>15s)` | PC817 polling task hung or GPIO chip polled unsuccessfully | `systemctl status smart-water` | Check journal for libgpiod character device errors. Verify `/dev/gpiochip1` accessibility. |
| **14** | Plant | Irrigation rejected: `Probe voltage outside sanity band` | Capacitive soil probe unplugged, shorted, or disconnected | `curl -H "Authorization: Bearer $T" http://localhost:8000/api/telemetry/live` | Inspect probe wiring to ADS1115. A disconnected probe floats above 3.0V; a shorted probe falls below 0.5V. |
| **15** | Plant | Irrigation rejected: `Probe is uncalibrated` | Dry/wet reference voltages are null in `/var/lib/smart-water/settings.yaml` | `cat /var/lib/smart-water/settings.yaml \| grep calibration -A 5` | Run the two-step calibration wizard: immerse probe in air for dry calibration, then in cup of water for wet calibration. |
| **16** | Plant | Irrigation rejected: `Irrigation reservoir is dry` | Reservoir reed float switch is open (`reservoir_wet: false`) | `curl -H "Authorization: Bearer $T" http://localhost:8000/api/telemetry/live` | Refill drip reservoir tank with water. Check float switch continuity across ESP32 GPIO 5. |
| **17** | Plant | Irrigation rejected: `System clock is invalid (un-synced)` | System time earlier than firmware build date epoch; no RTC/NTP | `timedatectl status` or `hwclock -r` | Ensure DS3231 RTC coin cell (CR2032) is good. Check I2C RTC detected at `0x68` using `i2cdetect -y 7`. Set system clock. |
| **18** | Plant | Pump shut off with `NO FLOW DETECTED after 10s` | Zone 1 pulse flow meter registered fewer than `min_pulses_10s` (4 pulses) | `curl -H "Authorization: Bearer $T" http://localhost:8000/api/telemetry/live` | Check for clogged drip emitters, kinked tubing, closed ball valve, or airlock in Zone 1 pump impeller. |
| **19** | Plant | Pump run limited to exactly 60 seconds | Server and ESP32 firmware hard cap enforces max 60s per pulse | `grep MAX_PUMP_HARD_TIMEOUT_S firmware/node/include/config.h` | By design. Long runs are executed in distinct pulses separated by soak periods to avoid root drowning and battery drain. |
| **20** | Audio | Voice alerts silent or error: `aplay: test: No such device` | CM108 USB audio interface missing or ALSA card index shifted | `aplay -l` or `cat /proc/asound/cards` | Re-insert CM108 audio adapter. Run `amixer -c <card> sset 'Headphone' 80% unmute`. Check `audio.alsa_device` in `config.yaml`. |
| **21** | Audio | Voice alerts silent between 22:00 and 06:00 | Quiet hours active (`quiet_hours_enabled: true`) | `grep quiet_hours /etc/smart-water/config.yaml -A 5` | By design. Low-priority speech is silenced at night. Critical Priority 0 alerts (leak, fire, lockout) always sound. |
| **22** | Audio | Espeak fails with `espeak-ng: command not found` | Package `espeak-ng` was not installed during provisioning | `which espeak-ng` | Run `sudo apt-get install -y espeak-ng alsa-utils`. |
| **23** | System | Service crashes with `Memory limit exceeded (200M)` | Systemd cgroup killed process due to memory leak or buffering | `systemctl status smart-water.service \| grep Memory` | Verify SQLite PRAGMA cache limits (`cache_size = -2000`). Confirm SSE stream connections are disconnecting cleanly. |
| **24** | System | Kernel Watchdog reboots board after 15 seconds | Application event loop blocked or CPU starved for >15s | `dmesg \| grep -i watchdog` | Check for blocking sync I/O in custom rules. Ensure `/dev/watchdog` ping interval is 5s (`watchdog.py`). |
| **25** | Network | Refuses to start with `Refusing to bind to 0.0.0.0` | Security policy forbids binding public IP without explicit flag | `grep bind_address /etc/smart-water/config.yaml` | Set `bind_address` to your private Tailscale mesh IP (e.g., `100.x.y.z`) or set `allow_all_interfaces: true` if behind NAT. |
| **26** | Security | API requests fail with `401 Unauthorized` | Missing or invalid Bearer token in request header | `python -m app.cli show-token` | Copy your token from `/etc/smart-water/auth.json` setup step or run `python -m app.cli rotate-token` to generate fresh token. |
| **27** | Database | SQLite database locked (`database is locked`) | Concurrency contention or missing WAL mode | `sqlite3 /var/lib/smart-water/system.db "PRAGMA journal_mode;"` | Verify WAL mode is active (`PRAGMA journal_mode=WAL`). Increase busy timeout if disk I/O on micro-SD card is slow. |
| **28** | I2C | I2C devices missing from bus 7 (`i2cdetect -y 7` shows empty) | Radxa device tree overlay for I2C7 not enabled in `/boot/armbianEnv.txt` | `i2cdetect -y 7` | Add `overlays=i2c7` to `/boot/armbianEnv.txt` and reboot the ROCK Pi S. |
| **29** | Contactor | Motor trips with `CONTACTOR_MISMATCH` or `WELDED_CONTACT` | Auxiliary contact feedback mismatch; coil energized but aux switch open, or coil off but aux closed | `python -m app.cli test-relay` | Inspect AC contactor physical contacts. Check NO auxiliary switch continuity wiring to Rock Pi S feedback GPIO. Replace contactor if contacts are welded. |
| **30** | Hardware | ESP32-C3 GPIO 4 input damaged or erratic pulse count | Flow meter 5V signal wired directly to 3.3V ESP32 without resistive voltage divider | Inspect wiring with multimeter | Wire 5V pulse signal through voltage divider: 10k resistor in series, 20k resistor to GND, tapping mid-point to GPIO 4 (3.3V logic). |
| **31** | Watchdog | Daemon killed with exit code 2 and contactor drops | Critical loop stalled (`head_end_poll` > 3s or `rules_auto_loop` > 15s) | `journalctl -u smart-water -n 50 \| grep HealthMonitor` | Review CPU usage or long-running tasks blocking the asyncio thread. The health-gated watchdog intentionally forces relay de-energization and systemd restart. |
| **32** | GPIO | `ImportError: No module named gpiod` or API crash | Incorrect libgpiod package installed or mixed v1/v2 pip build | `apt list --installed \| grep gpiod` | Ensure `python3-libgpiod` and `gpiod` are installed via apt on Armbian Bookworm; never install gpiod via pip. |
| **33** | SSE | Telemetry event stream returns `403 Forbidden` | Attempting to connect to `/api/telemetry/stream` with raw Bearer token in URL | Inspect web console network tab | Obtain single-use 60s ticket first via `POST /api/telemetry/ticket`, then connect to `/api/telemetry/stream?ticket=<ticket_id>`. |
| **34** | Security | Webhook notification rejected: `Invalid or unsafe webhook URL` | Webhook points to private/loopback IP (127.0.0.1, 192.168.x.x, 10.x.x.x, 169.254.x.x) | Inspect configured alert webhook URL | Configure public HTTPS endpoint or legitimate Telegram Bot API (`https://api.telegram.org/...`). SSRF policy blocks private network targets. |
| **35** | Motor | Motor restart blocked with `MOTOR_MIN_OFF_TIME active` | Motor stopped less than 120s ago; anti-short-cycling guard enforced | `curl -H "Authorization: Bearer $T" http://localhost:8000/api/telemetry/live` | Mandatory 120-second delay protects pump motor windings against thermal distress. Wait until cooldown timer reaches zero. |

---

## 7. Protocol v1.1 Wire Specification

All serial and wireless communication between the ROCK Pi S and the ESP32-C3 node adheres to the canonical contract in `protocol/messages.json`. Packets are serialized as compact JSON lines with keys in a strictly deterministic order, terminated by `\n`. Every packet includes a 16-hex HMAC-SHA256 signature calculated over the packet string with the `,"sig":"..."` suffix removed.

### Telemetry Packets (Node -> Pi)

- **Heartbeat (`hb`):**
  ```json
  {"v":1.1,"t":"hb","seq":1,"boot":305419896,"uptime_s":120,"res_wet":true,"ads_ok":true,"pumps":[0,0,0,0],"rssi":-62,"sig":"7b26918d363bbaae"}
  ```
- **Soil Probe Moisture (`soil`):**
  ```json
  {"v":1.1,"t":"soil","seq":2,"boot":305419896,"z":1,"volts":1.842,"sig":"a18a933ffcfefcba"}
  ```
- **Flow Meter Pulses (`flow`):**
  ```json
  {"v":1.1,"t":"flow","seq":3,"boot":305419896,"z":1,"delta_pulses":15,"period_ms":1000,"sig":"9b61e06fa49d2149"}
  ```
- **Command Acknowledgment (`ack`):**
  ```json
  {"v":1.1,"t":"ack","seq":4,"boot":305419896,"acked_seq":1,"status":"OK","sig":"09d3bdf9dca68593"}
  ```

### Control Packets (Pi -> Node)

- **Pump Run Command (`pump`):**
  ```json
  {"v":1.1,"cmd":"pump","seq":1,"boot":305419896,"z":1,"duration_s":15,"sig":"18536f909192c730"}
  ```
- **Pump Stop Command (`stop`):**
  ```json
  {"v":1.1,"cmd":"stop","seq":2,"boot":305419896,"z":1,"sig":"be1529124adff045"}
  ```
- **Emergency Stop All Pumps (`stop_all`):**
  ```json
  {"v":1.1,"cmd":"stop_all","seq":3,"boot":305419896,"sig":"c953504bf1f7e8ba"}
  ```

---

## 8. REST & SSE Telemetry API Reference

All mutating endpoints require an `Authorization: Bearer <token>` header.

### Endpoints Summary

| Method | Path | Auth Required | Description |
| :--- | :--- | :---: | :--- |
| `GET` | `/healthz` | No | Liveness probe returning `{"status": "ok"}` |
| `GET` | `/api/health/detail` | Yes | Subsystem health matrix, failed signature counts, last gateway errors |
| `GET` | `/api/telemetry/live` | Yes | Instant snapshot of motor, tank, interlock, zones, and alarms |
| `POST` | `/api/telemetry/ticket` | Yes | Mints single-use 60-second ticket for SSE connection |
| `GET` | `/api/telemetry/stream?ticket=<t>` | No (Ticket) | 1Hz Server-Sent Events live telemetry stream |
| `POST` | `/api/motor/start` | Yes | Commands overhead AC motor start (`{"confirm": true}`) |
| `POST` | `/api/motor/stop` | Yes | Commands overhead AC motor stop |
| `POST` | `/api/motor/reset` | Yes | Clears manual safety lockout after cooldown expiry |
| `POST` | `/api/zones/{id}/water` | Yes | Triggers timed drip pulse on Zone 1-4 (`{"duration_s": 15}`) |
| `POST` | `/api/zones/{id}/stop` | Yes | Immediately cuts power to Zone 1-4 pump |
| `POST` | `/api/flow/calibrate` | Yes | Runs 10s calibration pulse on Zone 1 and sets cutoff thresholds |
| `POST` | `/api/control/emergency-stop` | Yes | Emergency stops AC motor and all 4 DC plant pumps |
| `POST` | `/api/test/relay` | Yes | Safe 500ms dry contactor test with aux contact verification |

---

## 9. Maintenance, Backup & Token Rotation

### 9.1 Atomic Database Backup & Restore
Create an atomic, non-locking backup of the SQLite database:
```bash
curl -X POST -H "Authorization: Bearer $AUTH_TOKEN" http://localhost:8000/api/backup
```
To restore from backup:
```bash
sudo -u smartwater /opt/smart-water/venv/bin/python -m app.cli restore /var/lib/smart-water/backups/backup_20261012.db
```

### 9.2 API Token Rotation
Rotate the cryptographic Bearer token at any time:
```bash
sudo -u smartwater /opt/smart-water/venv/bin/python -m app.cli rotate-token
```
The new raw token will be printed to stdout once and its SHA-256 hash written to `/etc/smart-water/auth.json`.

### 9.3 Systemd Service Management
```bash
# Restart the service:
sudo systemctl restart smart-water.service

# View live application logs:
journalctl -u smart-water.service -f

# Check cgroup resource consumption:
systemctl status smart-water.service | grep -E "Memory|CPU"
```

### 9.4 Clean Uninstallation
To completely remove the controller and restore default OS state:
```bash
sudo ./uninstall.sh
```
