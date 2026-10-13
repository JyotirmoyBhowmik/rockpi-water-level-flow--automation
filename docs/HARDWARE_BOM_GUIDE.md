# Complete Industrial Hardware & BOM Engineering Manual (Master Field Guide)

> **Document Type:** Production Hardware & Installation Manual  
> **Target Audience:** Absolute Beginner ("Noob-Friendly") to Industrial Automation Electrician  
> **Hardware Platform:** Radxa ROCK Pi S (512MB RAM, Rockchip RK3308B) & ESP32-C3 Super Mini  
> **Operating System:** Armbian Debian 12 (Bookworm) Minimal Headless (Linux Kernel 6.1+ / 6.6+)  
> **Application:** Dual-Domain Domestic Pump Automation & Precision 4-Zone Wireless Drip Irrigation

---

## Table of Contents

1. [Understanding the System Architecture & Two Enclosures](#1-understanding-the-system-architecture--two-enclosures)
2. [What is the Hardware Commissioning & Calibration Wizard?](#2-what-is-the-hardware-commissioning--calibration-wizard)
3. [Operational Digest & Historical Time Window (1h, 6h, 24h, 48h)](#3-operational-digest--historical-time-window-1h-6h-24h-48h)
4. [Section 1: Controller Head-End (Logic Enclosure IP65 200x150x100)](#4-section-1-controller-head-end-logic-enclosure-ip65-200x150x100)
   - 1.1 [SanDisk Max Endurance 32GB microSD](#11-sandisk-max-endurance-32gb-microsd)
   - 1.2 [CM108 USB Audio Dongle](#12-cm108-usb-audio-dongle)
   - 1.3 [3–5W USB Speaker](#13-35w-usb-speaker)
   - 1.4 [Powered USB 2.0 Hub](#14-powered-usb-20-hub)
   - 1.5 [12V Mini-UPS (Battery Backup)](#15-12v-mini-ups-battery-backup)
   - 1.6 [LM2596 / MP1584EN Buck Converter (Head-End)](#16-lm2596--mp1584en-buck-converter-head-end)
   - 1.7 [DS3231 Precision I2C RTC Module](#17-ds3231-precision-i2c-rtc-module)
   - 1.8 [Inline Fuse Holders and Fuses](#18-inline-fuse-holders-and-fuses)
   - 1.9 [4-Channel PC817 Optocoupler Isolation Board](#19-4-channel-pc817-optocoupler-isolation-board)
   - 1.10 [TVS Diodes P6KE18CA (Bidirectional)](#110-tvs-diodes-p6ke18ca-bidirectional)
   - 1.11 [Water Leak Sensor (Digital, LM393 Comparator)](#111-water-leak-sensor-digital-lm393-comparator)
   - 1.12 [Logic Enclosure IP65 200x150x100 mm](#112-logic-enclosure-ip65-200x150x100-mm)
   - 1.13 [PG7 / PG9 Cable Glands](#113-pg7--pg9-cable-glands)
   - 1.14 [WAGO 221 Lever Connectors (2-, 3-, 5-Way)](#114-wago-221-lever-connectors-2--3--5-way)
   - 1.15 [10 kOhm Resistors, 1/4W](#115-10-kohm-resistors-14w)
   - 1.16 [Heat Shrink Kit (2–6 mm, Adhesive-Lined)](#116-heat-shrink-kit-26-mm-adhesive-lined)
   - 1.17 [UV-Resistant Cable Ties (150 mm, Black)](#117-uv-resistant-cable-ties-150-mm-black)
   - 1.18 [DC Barrel Pigtails 5.5 x 2.1 mm](#118-dc-barrel-pigtails-55-x-21-mm)
   - 1.19 [Terminal Blocks, Perfboard, Jumper Wires](#119-terminal-blocks-perfboard-jumper-wires)
5. [Section 2: Mains & Motor Control (Mains Enclosure IP65 250x200x120)](#5-section-2-mains--motor-control-mains-enclosure-ip65-250x200x120)
   - 2.1 [Double-Pole MCB (16A, C Curve)](#21-double-pole-mcb-16a-c-curve)
   - 2.2 [RCCB 2P 25A 30mA (Earth Leakage)](#22-rccb-2p-25a-30ma-earth-leakage)
   - 2.3 [Type 2 AC Surge Protector (DIN Rail SPD)](#23-type-2-ac-surge-protector-din-rail-spd)
   - 2.4 [AC Contactor 25A (220V/230V Coil, 2-Pole + NO Aux Contact)](#24-ac-contactor-25a-220v230v-coil-2-pole--no-aux-contact)
   - 2.5 [Thermal Overload Relay (Matched to Motor FLC)](#25-thermal-overload-relay-matched-to-motor-flc)
   - 2.6 [5V Relay Module (Pilot Relay)](#26-5v-relay-module-pilot-relay)
   - 2.7 [RC Snubber (0.1 µF 400V/630V + 100 Ohm 2W)](#27-rc-snubber-01-%C2%B5f-400v630v--100-ohm-2w)
   - 2.8 [22mm Selector Switch, 3-Position (Auto / Off / Manual)](#28-22mm-selector-switch-3-position-auto--off--manual)
   - 2.9 [Mains Enclosure IP65 250x200x120 mm](#29-mains-enclosure-ip65-250x200x120-mm)
   - 2.10 [DIN Rail 35 mm (Top-Hat Profile)](#210-din-rail-35-mm-top-hat-profile)
   - 2.11 [Wire Ferrules and Lugs Kit](#211-wire-ferrules-and-lugs-kit)
   - 2.12 [2.5 mm² FRLS Copper Wire (Red, Black, Green)](#212-25-mm%C2%B2-frls-copper-wire-red-black-green)
   - 2.13 [DIN-Rail Terminal Blocks 2.5 mm² + End Stops](#213-din-rail-terminal-blocks-25-mm%C2%B2--end-stops)
   - 2.14 [PG11 / PG13.5 Glands (Mains)](#214-pg11--pg135-glands-mains)
6. [Section 3: Hardwired Tank & Sump Sensors](#6-section-3-hardwired-tank--sump-sensors)
   - 3.1 [Tank Float Switches (Full = NC, Low = Reversible)](#31-tank-float-switches-full--nc-low--reversible)
   - 3.2 [Sump Float (NO Type, Closes When Wet)](#32-sump-float-no-type-closes-when-wet)
   - 3.3 [4-Core Shielded Cable 0.5 mm²](#33-4-core-shielded-cable-05-mm%C2%B2)
   - 3.4 [2-Core Shielded Cable 0.5 mm²](#34-2-core-shielded-cable-05-mm%C2%B2)
7. [Section 4: Wireless Garden Station (Irrigation Node & Gateway)](#7-section-4-wireless-garden-station-irrigation-node--gateway)
   - 4.1 [ESP32-C3 Super Mini (Gateway + Garden Node)](#41-esp32-c3-super-mini-gateway--garden-node)
   - 4.2 [ADS1115 16-Bit I2C ADC Module (Address 0x48)](#42-ads1115-16-bit-i2c-adc-module-address-0x48)
   - 4.3 [Capacitive Soil Moisture Sensors (v1.2 / v2.0)](#43-capacitive-soil-moisture-sensors-v12--v20)
   - 4.4 [4-Channel LR7843 Logic-Level N-MOSFET Board](#44-4-channel-lr7843-logic-level-n-mosfet-board)
   - 4.5 [12V Mini Submersible Pumps (Brushless DC)](#45-12v-mini-submersible-pumps-brushless-dc)
   - 4.6 [1N4007 Flyback Diodes](#46-1n4007-flyback-diodes)
   - 4.7 [Polypropylene (PP) Float Switch (Reservoir Dry-Run)](#47-polypropylene-pp-float-switch-reservoir-dry-run)
   - 4.8 [12V 3A DC Power Adapter](#48-12v-3a-dc-power-adapter)
   - 4.9 [MP1584EN Buck Converter (Garden Node, to 5V)](#49-mp1584en-buck-converter-garden-node-to-5v)
   - 4.10 [YF-S401 / YF-S201 Pulse Flow Meter](#410-yf-s401--yf-s201-pulse-flow-meter)
   - 4.11 [4 mm Check Valves (Non-Return Valves)](#411-4-mm-check-valves-non-return-valves)
   - 4.12 [4 mm Barbed Anti-Siphon Tees](#412-4-mm-barbed-anti-siphon-tees)
   - 4.13 [4/7 mm Silicone / PVC Tubing](#413-47-mm-silicone--pvc-tubing)
   - 4.14 [Drip Stakes / Adjustable Emitters](#414-drip-stakes--adjustable-emitters)
   - 4.15 [Pump Inlet Strainer / Pre-Filter](#415-pump-inlet-strainer--pre-filter)
   - 4.16 [Garden Enclosure IP65 150x110x70 mm & PG7 Glands](#416-garden-enclosure-ip65-150x110x70-mm--pg7-glands)
   - 4.17 [Pump Outlet Reducers / Adapters](#417-pump-outlet-reducers--adapters)
8. [Section 5: Optional Extras & Redundancy Components](#8-section-5-optional-extras--redundancy-components)
   - 5.1 [Rooftop Redundancy Node (ESP32-C3, Enclosure, 5V Adapter, SS316 Float)](#51-rooftop-redundancy-node)
   - 5.2 [BME280 Environmental Sensor (I2C)](#52-bme280-environmental-sensor-i2c)
   - 5.3 [0.96-Inch OLED Display (SSD1306, 128x64 I2C)](#53-096-inch-oled-display-ssd1306-128x64-i2c)
   - 5.4 [Active Piezo Buzzer (5V DC)](#54-active-piezo-buzzer-5v-dc)
   - 5.5 [Extra ADS1115 (Address 0x49)](#55-extra-ads1115-address-0x49)
9. [Scaling & Expansion Handbook: "If I Increase The Items"](#9-scaling--expansion-handbook-if-i-increase-the-items)
   - 9.1 [Scaling Irrigation Zones (from 4 up to 8 or 16 zones)](#91-scaling-irrigation-zones)
   - 9.2 [Sizing Up the Pump Motor (0.5 HP, 1 HP, 2 HP, 3 HP, 5 HP Electrical Sizing Table)](#92-sizing-up-the-pump-motor)
   - 9.3 [Adding Continuous Level Sensing (Ultrasonic vs. Submersible Pressure)](#93-adding-continuous-level-sensing)
   - 9.4 [Multi-Tank Management (Dual Overhead Tanks & Sump Cascades)](#94-multi-tank-management)
   - 9.5 [Solar & Battery Off-Grid Power Sizing for Garden Node](#95-solar--battery-off-grid-power-sizing)
10. [End-to-End Master Pinout, Wiring & Terminal Matrix](#10-end-to-end-master-pinout-wiring--terminal-matrix)
11. [Pre-Power-On Multimeter Checklist ("Don't Blow Up Your SBC")](#11-pre-power-on-multimeter-checklist-dont-blow-up-your-sbc)

---

## 1. Understanding the System Architecture & Two Enclosures

### The Core Problem: Why Can't We Put Everything in One Box?
Submersible water pumps run on **230V AC mains electricity** and draw between 4 Amperes and 25 Amperes of inductive current. When a 230V motor starts or stops:
1. An electrical plasma arc leaps across the relay contacts.
2. A severe electromagnetic pulse (EMP / EMI) radiates outward.
3. Voltage fluctuations (dips and spikes) ripple across the power line.

On the other hand, the **Radxa ROCK Pi S** is a sensitive micro-computer running a 64-bit quad-core ARM processor at 3.3V logic levels. If 230V AC or electromagnetic noise reaches the ROCK Pi S pins:
- The Linux kernel crashes immediately (`kernel panic` or watchdog reset).
- The MicroSD card file system gets corrupted, bricking your installation.
- The 3.3V silicon I/O pads on the processor fry instantly.

### The Solution: Two Galvanically Isolated Enclosures

```
+===================================================================================+
|                                PHYSICAL LAYOUT                                    |
+===================================================================================+

+-------------------------------------+      +-------------------------------------+
|   ENCLOSURE 1: MAINS HIGH-VOLTAGE   |      |   ENCLOSURE 2: LOGIC LOW-VOLTAGE    |
|   (IP65 250x200x120 mm)             |      |   (IP65 200x150x100 mm)             |
|                                     |      |                                     |
|   230V AC Live (L) & Neutral (N) In |      |   12V DC Battery / UPS Supply In    |
|                 │                   |      |                 │                   |
|                 ▼                   |      |                 ▼                   |
|   [ 16A DP MCB (C-Curve) ]          |      |   [ 12V Mini-UPS (Li-ion) ]         |
|                 │                   |      |                 │                   |
|                 ▼                   |      |                 ▼                   |
|   [ 25A 30mA RCCB Shock Breaker ]   |      |   [ LM2596 Buck Converter (5.1V) ]  |
|                 │                   |      |                 │                   |
|                 ▼                   |      |                 ▼                   |
|   [ Type 2 Surge Protector (SPD) ]  |      |   [ Radxa ROCK Pi S (512MB RAM) ]   |
|                 │                   |      |    ├── DS3231 I2C Precision RTC     |
|                 ▼                   |      |    ├── CM108 USB Audio DAC Adapter  |
|   [ 25A AC Contactor (230V Coil) ]  |      |    ├── 3-5W USB Audio Speaker       |
|    - RC Snubber across A1/A2        |      |    ├── USB Gateway (ESP32-C3)       |
|    - NO Aux Contact 13/14 Feedback ──┼──────┼──> ├── 4-Ch PC817 Optocouplers      |
|                 │                   | Aux  |    └── Active Piezo Buzzer 85dB     |
|                 ▼                   | Loop |                                     |
|   [ Thermal Overload Relay ]        |      |   [ 8-LED Status Diagnostic Board ] |
|                 │                   |      +-------------------------------------+
|                 ▼                   |                         ▲
|   [ Auto / Off / Manual Switch ]    |                         │ Isolated
|                 │                   |      +------------------┴------------------+
|                 ▼                   |      |   FIELD SENSORS (OUTDOOR RUNS)      |
|   230V Submersible Pump Motor       |      |   - Full Float Switch (NC, Top)     |
|   (Underground Sump / Borewell)     |      |   - Low Float Switch (NO, Bottom)   |
|                                     |      |   - Sump Float Switch (NO, Dry-Run) |
|   5V Pilot Relay Coil Command ◄─────┼──────┼── - Manifold Leak Sensor (LM393)    |
+-------------------------------------+ Pilot+-------------------------------------+
                                       Loop
```

---

## 2. What is the Hardware Commissioning & Calibration Wizard?

### Purpose & Architecture
When you flash Armbian onto the SD card and boot the ROCK Pi S for the very first time, the controller intentionally boots into **`SAFE-NO-HARDWARE` mode**:
- All pump outputs are forced **LOW (0V / OFF)**.
- Automated timer loops are suspended.
- Audio and Telegram alerts will not sound false alarms.
- The web browser interface automatically routes you to the **7-Step Hardware Commissioning Wizard** at `http://<tailscale-ip>:8000`.

### Deep Dive: Each of the 7 Wizard Steps

```
Step 1: Security Token ──► Step 2: Pin Mapping ──► Step 3: Float Verification
                                                            │
Step 6: Probe Calibration ◄── Step 5: RF Pairing ◄── Step 4: Dry Click Contactor
         │
         ▼
Step 7: Commissioning Seal ──► System Unlocked (Production Ready)
```

#### Step 1: Network & Security Token Authentication
- **What happens:** The system binds strictly to `127.0.0.1` and your private Tailscale mesh address (`100.x.y.z`). It generates a cryptographically secure 256-bit Bearer token written to `/var/lib/smart-water/auth.json` (permissions `0600`, owned by `root:smartwater`).
- **Why it matters:** Prevents unauthorized people on your home Wi-Fi or local network from triggering pump cycles or tampering with safety thresholds.

#### Step 2: Map Head-End GPIO Pins
- **What happens:** The wizard executes `gpioinfo /dev/gpiochip1` in the background and renders a real-time pin mapping grid. You select physical pins for:
  - IN1 (Tank Low Float)
  - IN2 (Interlock Output)
  - IN3 (Interlock Midpoint Tap)
  - IN4 (Manifold Leak Sensor)
  - Pilot Relay Output
  - Contactor Aux Feedback Input
- **Why it matters:** Pin offsets can change across different Linux device-tree overlays and Armbian releases. This step guarantees that software lines match physical copper traces.

#### Step 3: Physical Float Switches & Polarity Test
- **What happens:** The wizard displays live raw logic meters (HIGH/LOW) for all 3 float switches. You are instructed to physically lift the Full float in the tank, lower the Sump float in the well, and wet the leak sensor.
- **Why it matters:** Many reed switches can be flipped physically to be Normally Closed (NC) or Normally Open (NO). This test verifies whether your float pulls the GPIO pin LOW or HIGH when water reaches it, auto-configuring `active_low: true/false`.

#### Step 4: Contactor Dry Click Test
- **What happens:** With **230V mains power completely switched off at the DP MCB**, you click "Test Relay (0.5s Pulse)" on the screen. The controller energizes the pilot relay for exactly 500 milliseconds.
- **Verification:**
  1. You hear an audible mechanical "CLACK" as the contactor armature pulls in.
  2. The software measures how many milliseconds elapsed before the auxiliary contact (`13/14`) closed (pull-in time, typically 20–60 ms).
  3. If auxiliary contact does not close within 1,000 ms, the wizard alerts you to a wiring error or defective coil.

#### Step 5: Pair Wireless Garden Gateway
- **What happens:** You plug the ESP32-C3 USB gateway into the USB hub. The udev rule `/etc/udev/rules.d/99-smart-water.rules` maps it to `/dev/garden-gateway`. The wizard validates that packets arrive at 115,200 baud with valid HMAC-SHA256 signatures matching `/etc/smart-water/espnow.key`.
- **Why it matters:** Ensures that wireless packets cannot be spoofed by rogue 2.4 GHz transmitters.

#### Step 6: Soil Probe & Flow Meter Calibration
- **What happens:**
  1. **Soil Probes:** You hold each capacitive probe in dry ambient air (captures dry baseline: ~2.6V), then dip it into a glass of tap water (captures wet baseline: ~1.2V). The system establishes a 0.4V sanity margin.
  2. **Zone 1 Flow Meter:** With water in the garden reservoir, the wizard pulses Zone 1 for 10 seconds, counts pulses, and calculates baseline L/min (setting a 50% cutoff threshold for airlock/leak trips).

#### Step 7: Finalize Commissioning Seal
- **What happens:** Writes the verified configuration to `/var/lib/smart-water/settings.yaml`, sets `setup_complete: true`, seals the wizard against further edits, and prompts for a service restart (`sudo systemctl restart smart-water.service`). Full automated replenishment is now operational.

---

## 3. Operational Digest & Historical Time Window (1h, 6h, 24h, 48h)

The dashboard includes a dedicated **Operational Digest** card and **Historical Trends** chart that summarize water delivery, power consumption, pump starts, and safety trips:

```
+-----------------------------------------------------------------------------------+
|  [Time Window: (1h) (6h) [24h] (48h)]              [🔄 Refresh] [📩 Send Alert]   |
+-----------------------------------------------------------------------------------+
|  📊 24-Hour Operational Digest                          [Past 24 Hours]           |
|  +--------------------+--------------------+--------------------+-----------------+
|  | Motor Runtime      | Est. Water Pumped  | Irrigation Total   | Safety Trips    |
|  | 14m 20s (3 starts) | ~2,150 Litres      | 48.5 Litres        | 0 (All Healthy) |
|  | 0.38 kWh           | Overhead Tank      | 4 Zones Monitored  | Interlocks OK   |
|  +--------------------+--------------------+--------------------+-----------------+
+-----------------------------------------------------------------------------------+
```

### Time Window Modes Explained:
- **1h (1 Hour):** Diagnostic zoom. Inspects flow rate and voltage immediately after a run.
- **6h (6 Hours):** Half-day recap. Verifies morning drip irrigation and tank replenishment cycles.
- **24h (24 Hours - Standard):** Daily summary. Directly compares household water pumped vs. plant irrigation delivered.
- **48h (48 Hours):** Weekend multi-day overview. Vital for detecting slow tank leaks (gradual level loss during zero-usage night hours between 01:00 and 04:00).

### Technical Operation (Under the Hood):
1. **Dynamic Headers & Badges:** Selecting `1h`, `6h`, `24h`, or `48h` updates the title to `${hours}-Hour Operational Digest`, the badge to `Past ${hours} Hours`, and the button to `📩 Send ${hours}h Summary Alert`.
2. **Downsampled Data Logging:** The background scheduler logs a downsampled snapshot to SQLite every 60 seconds (1 row per minute).
3. **Paging Limit Scaling:** `/api/history` scales rows dynamically:
   - `1h`: 120 points (2 hours buffer)
   - `6h`: 400 points
   - `24h`: 1,500 points
   - `48h`: 3,000 points
4. **SVG Chart Viewport:** Passes exact bounding timestamps `{ xMin: now - hours * 3600 * 1000, xMax: now }` to ensure lines stretch across the full selected period.

---

## 4. Section 1: Controller Head-End (Logic Enclosure IP65 200x150x100)

---

### 1.1 SanDisk Max Endurance 32GB microSD
- **Specifications:** MicroSDHC / MicroSDXC, Class 10, U3, V30, up to 100 MB/s read, 40 MB/s write. pSLC/MLC high-endurance flash.
- **What It Is:** Heavy-duty flash memory card engineered specifically for continuous video recorders and database servers.
- **Why We Use It:** Standard consumer SD cards use cheap TLC/QLC flash designed for taking photos. Under continuous Linux SQLite WAL database logging, cheap SD cards develop bad blocks within 3 to 6 months. Max Endurance lasts 15,000+ hours of continuous write cycles.
- **Connection:** Insert into the MicroSD slot on the bottom of the Radxa ROCK Pi S board.
- **Software Configuration:**
  - Formatted with ext4 filesystem.
  - SQLite is configured in `/etc/smart-water/config.yaml`:
    ```yaml
    database:
      retention_days: 90
      cache_size_kb: -2000    # ~2MB cache
      busy_timeout_ms: 5000   # 5s lock timeout
    ```

---

### 1.2 CM108 USB Audio Dongle
- **Specifications:** C-Media CM108 chipset, USB 2.0 Full Speed, 3.5mm stereo output jack, 16-bit 48kHz DAC, 93dB SNR.
- **What It Is:** USB sound card adapter providing an audio output jack for the ROCK Pi S.
- **Why We Use It:** The ROCK Pi S has no onboard audio jack. The CM108 provides a Linux ALSA audio device for real-time synthesized voice alerts.
- **Connection:** Plugs into downstream port of Powered USB Hub (or ROCK Pi S USB Type-A port). Connect 3.5mm cable to speaker.
- **Software Configuration:**
  - Install dependencies: `sudo apt-get install -y espeak-ng alsa-utils`
  - `/etc/smart-water/config.yaml`:
    ```yaml
    audio:
      enabled: true
      alsa_device: "default"
      voice: "en-us"
      speed: 145
      quiet_hours_start: 22
      quiet_hours_end: 6
    ```
  - Test command: `python -m app.cli test-audio`

---

### 1.3 3–5W USB Speaker
- **Specifications:** 5V DC powered, 3W to 5W RMS, 4 Ohm impedance, 3.5mm analog input, 80 Hz – 20 kHz.
- **What It Is:** Compact powered loudspeaker with internal audio amplifier.
- **Why We Use It:** Broadcasts audible spoken warnings in the pump room so operators are immediately alerted to issues like a dry sump or burst pipe without having to check their phone.
- **Connection:**
  - 3.5mm audio jack $\rightarrow$ CM108 audio output.
  - USB Type-A power cable $\rightarrow$ Powered USB Hub 5V port.
- **Volume Adjustment:** `amixer sset 'Headphone' 80% unmute`

---

### 1.4 Powered USB 2.0 Hub
- **Specifications:** 4-port USB 2.0 hub, external 5V 2A DC input jack (5.5 x 2.1 mm), per-port overcurrent protection.
- **What It Is:** Multiport USB splitter with its own dedicated power supply.
- **Why We Use It:** The ROCK Pi S onboard USB Type-A port can only deliver 500mA. Running the CM108 DAC (~100mA), USB Speaker (~800mA peak), and ESP32-C3 Gateway (~150mA) simultaneously draws over 1,000mA, which would cause SBC brownout reboots.
- **Connection:**
  - Upstream USB cable $\rightarrow$ ROCK Pi S USB Type-A port.
  - DC power socket $\rightarrow$ 5.1V rail from the LM2596 buck converter.
  - Downstream ports $\rightarrow$ CM108 Audio, USB Speaker, ESP32-C3 Gateway.

---

### 1.5 12V Mini-UPS (Battery Backup)
- **Specifications:** Input: 12V DC, Output: 12V DC 2A, Capacity: 3x 18650 Li-ion cells (7,800 mAh / 28.8 Wh), zero transfer time (<5ms).
- **What It Is:** Uninterruptible Power Supply providing battery backup for the DC electronics.
- **Why We Use It:** Prevents sudden SBC shutdowns and database corruption during power outages and voltage dips. Keeps the controller running for up to 4 hours without mains power.
- **Connection:**
  - Input: 12V 2A AC-DC wall adapter.
  - Output: Feeds the 12V DC logic bus bar through a 2A inline fuse.

---

### 1.6 LM2596 / MP1584EN Buck Converter (Head-End)
- **Specifications:** Input: 7V–28V DC, Output: 5.1V DC adjustable, Continuous current: 2A (3A peak with heatsink), Efficiency: 92%, Switching frequency: 150 kHz.
- **What It Is:** Step-down DC-DC switching power supply.
- **Why We Use It:** Steps down the 12V UPS battery bus to a clean 5.1V DC rail to power the ROCK Pi S and USB Hub without generating the excessive heat of linear regulators.
- **Connection:**
  - `IN+` $\rightarrow$ +12V UPS rail (fused 2A).
  - `IN-` $\rightarrow$ DC Common Ground.
  - `OUT+` $\rightarrow$ 5.10V DC rail (Measure and adjust with a multimeter before connecting the SBC!).
  - `OUT-` $\rightarrow$ DC Common Ground.

---

### 1.7 DS3231 Precision I2C RTC Module
- **Specifications:** I2C interface, address `0x68`, integrated 32.768 kHz TCXO, accuracy ±2 ppm (less than 1 minute drift per year), CR2032 coin cell holder.
- **What It Is:** Battery-backed Real-Time Clock maintaining accurate date and time during power or internet outages.
- **Why We Use It:** The ROCK Pi S has no onboard RTC. When internet/NTP is lost, the DS3231 ensures that quiet hours (22:00–06:00), daily summaries, and irrigation schedules execute on time.
- **Connection (ROCK Pi S Header 1):**
  - `VCC` $\rightarrow$ Pin 1 (3.3V System Rail)
  - `GND` $\rightarrow$ Pin 6 (Common Ground)
  - `SDA` $\rightarrow$ Pin 3 (GPIO1_C2 / Line 50)
  - `SCL` $\rightarrow$ Pin 5 (GPIO1_C3 / Line 51)
- **Configuration:**
  - Add `overlays=i2c7` to `/boot/armbianEnv.txt`.
  - Verify with: `sudo i2cdetect -y 7` (shows `0x68` or `UU`).

---

### 1.8 Inline Fuse Holders and Fuses
- **Specifications:** 5x20 mm screw-type or automotive mini-blade fuse holders, waterproof rubber cap, 18 AWG leads.
- **What It Is:** Overcurrent protection devices that melt to disconnect a circuit during a short.
- **Why We Use It:** Protects wiring from overheating and catching fire if a component fails or a wire chafes against metal.
- **Ratings & Placement:**
  - **Fuse 1 (2.0A Quick-Blow):** Between 12V UPS output and the LM2596 buck converter.
  - **Fuse 2 (0.5A Fast-Acting):** On the +5V supply line feeding external float switch loops.

---

### 1.9 4-Channel PC817 Optocoupler Isolation Board
- **Specifications:** 4 channels, PC817 phototransistor, Isolation voltage: 5,000V RMS, Input current: 5–10mA, Output: Open-collector NPN, max $V_{ce} = 35\text{V}$, max $I_c = 50\text{mA}$.
- **What It Is:** Optical isolator board that transmits electrical signals using light beams.
- **Why We Use It:** Keeps external 5V/12V sensor wires physically separated from the fragile 3.3V Rockchip RK3308 processor, protecting it from lightning surges, static, and noise.
- **Wiring (Input vs Output):**
  ```
  Field 5V Domain                         ROCK Pi S 3.3V Domain
  ---------------+                         ---------------------
  [ Float/Sensor]                         Pin 1 (3.3V Rail)
         │                                       │
         ▼                                       ▼
  PC817 Input LED ── (Light Beam) ──► PC817 NPN Phototransistor
         │                                       │
         ▼                                       ▼
    Common Ground                         ROCK Pi S GPIO Pin
  ```
  - `IN1`: Tank Low Float return wire.
  - `IN2`: Interlock Output (end of float chain).
  - `IN3`: Interlock Midpoint tap.
  - `IN4`: Water Leak Sensor digital output.
  - `OUT1 .. OUT4`: Wired to ROCK Pi S Pins 7, 9, 11, 12.
- **Configuration (`config.yaml`):**
  ```yaml
  head_end:
    tank_low_float_pin: 42
    interlock_out_pin: 43
    interlock_mid_pin: 44
    leak_sensor_pin: 45
    tank_low_active_low: true
    interlock_out_active_low: true
    interlock_mid_active_low: true
    leak_active_low: true
  ```

---

### 1.10 TVS Diodes P6KE18CA (Bidirectional)
- **Specifications:** P6KE18CA, Bidirectional, Stand-off voltage: 15.3V, Breakdown voltage: 17.1V–18.9V, Peak pulse power: 600 Watts (10/1000 µs), DO-15 axial package.
- **What It Is:** Fast-acting voltage clamping diode.
- **Why We Use It:** Clamps high-voltage electrostatic discharge (ESD) spikes induced on outdoor rooftop sensor cables during thunderstorms to a safe 18V, protecting the PC817 optocouplers.
- **Connection:** Placed in parallel across each field sensor input pair (one lead to sensor line, other lead to enclosure star ground).

---

### 1.11 Water Leak Sensor (Digital, LM393 Comparator)
- **Specifications:** Dual-sided gold-plated conductive PCB grid (50x40 mm), LM393 comparator board, Digital (DO) and Analog (AO) outputs, 3.3V–5V DC.
- **What It Is:** Surface water sensor with adjustable sensitivity.
- **Why We Use It:** Mounted directly under pipe unions and the pump housing. Immediately cuts motor power if a pipe bursts, preventing room flooding.
- **Connection:**
  - `VCC` $\rightarrow$ 5V DC Supply Rail.
  - `GND` $\rightarrow$ Common Ground.
  - `DO` $\rightarrow$ PC817 Optocoupler Channel 4 (`IN4`).
- **Calibration:** Turn potentiometer with a small screwdriver until onboard LED is OFF when dry, and turns ON when a drop of water touches the plate.

---

### 1.12 Logic Enclosure IP65 200x150x100 mm
- **Specifications:** ABS/Polycarbonate thermoplastic, IP65 ingress protection, polyurethane foam gasket, transparent or solid screw-on lid.
- **What It Is:** Weatherproof electrical junction box housing low-voltage logic components.
- **Why We Use It:** Protects sensitive SBC, optocouplers, and power supplies from dust, humidity, insects, and accidental contact.

---

### 1.13 PG7 / PG9 Cable Glands
- **Specifications:** Polyamide 6.6 (Nylon), EPDM rubber seal ring, IP68 rated, thread standard: PG.
  - PG7: Clamping range 3.0–6.5 mm (ideal for 2-core and 4-core sensor wires).
  - PG9: Clamping range 4.0–8.0 mm (ideal for DC power cables and USB leads).
- **What It Is:** Threaded compression fittings securing cables through enclosure walls.
- **Why We Use It:** Prevents water ingress and provides strain relief so cables cannot be accidentally pulled loose from screw terminals.

---

### 1.14 WAGO 221 Lever Connectors (2-, 3-, 5-Way)
- **Specifications:** WAGO 221-412 (2-wire), 221-413 (3-wire), 221-415 (5-wire). Rated: 450V, 32A, handles 0.14–4.0 mm² solid or stranded wire.
- **What It Is:** Lever-operated spring clamp electrical connectors.
- **Why We Use It:** Pump vibration can loosen traditional screw terminal blocks over time. WAGO spring clamps provide constant clamping pressure, eliminating maintenance and loose connections.
- **Usage:** Used for splitting 5.1V, 12V, and DC ground distribution rails.

---

### 1.15 10 kOhm Resistors, 1/4W
- **Specifications:** 10 kΩ ±1% tolerance, 0.25 Watt metal film, through-hole.
- **What It Is:** Electrical resistance component.
- **Why We Use It:** Pull-up resistors for open-collector sensor outputs and voltage divider circuits.

---

### 1.16 Heat Shrink Kit (2–6 mm, Adhesive-Lined)
- **Specifications:** Dual-wall polyolefin tubing with internal hot-melt thermoplastic adhesive, 3:1 shrink ratio, waterproof IP68.
- **What It Is:** Protective insulating tubing that shrinks when heated.
- **Why We Use It:** Waterproofs soldered wire splices inside water tanks and outdoor junction boxes. The internal glue melts to seal out moisture completely.

---

### 1.17 UV-Resistant Cable Ties (150 mm, Black)
- **Specifications:** Nylon 6.6 with 2% carbon black additive, tensile strength 18 kg, UV-resistant rated for outdoor exposure.
- **What It Is:** Fastening straps for securing outdoor cables.
- **Why We Use It:** Standard white zip ties degrade and snap under sunlight within 6 months. Black UV-stabilized ties last 10+ years outdoors.

---

### 1.18 DC Barrel Pigtails 5.5 x 2.1 mm
- **Specifications:** 5.5 mm outer diameter, 2.1 mm inner pin, 18 AWG copper leads, rated 12V DC 5A.
- **What It Is:** Pre-wired DC power plugs.
- **Why We Use It:** Provides standardized connections between the 12V UPS, power supplies, and distribution terminals.

---

### 1.19 Terminal Blocks, Perfboard, Jumper Wires
- **Specifications:** 2.54 mm pitch FR4 perfboard, 5.08 mm pitch screw terminal blocks, 22 AWG solid core jumper wire.
- **What It Is:** Prototyping board and screw terminals for neat wiring inside the logic enclosure.

---

## 5. Section 2: Mains & Motor Control (Mains Enclosure IP65 250x200x120)

---

### 2.1 Double-Pole MCB (16A, C Curve)
- **Specifications:** 2-Pole (2P), Rated current: 16A, Tripping curve: C (trips at 5–10x In), Breaking capacity: 6kA (IEC 60898-1), 230V/400V AC.
- **What It Is:** Miniature Circuit Breaker that disconnects both Live and Neutral conductors simultaneously.
- **Why We Use It:**
  - Disconnects both Live and Neutral for safety during servicing.
  - The C-curve allows the inductive starting inrush of the motor (30A–50A for ~100ms) without nuisance tripping, while tripping reliably under actual short circuits.
- **Connections:**
  - Terminals 1 and 3 (Top): Incoming 230V AC Live and Neutral from building supply.
  - Terminals 2 and 4 (Bottom): Output feeding the RCCB.

---

### 2.2 RCCB 2P 25A 30mA (Earth Leakage)
- **Specifications:** 2-Pole, Rated current: 25A, Sensitivity: 30mA ($\Delta I_n$), Type AC or Type A, Operating time: <40 ms at $I_{\Delta n}$, DIN rail mount.
- **What It Is:** Residual Current Circuit Breaker (shock protection device).
- **Why We Use It:** Submersible pumps operate submerged in water. If pump insulation or cable seals fail, mains voltage can leak into the water. The RCCB detects leakage currents exceeding 30mA and trips within 40 ms, protecting occupants from electric shock.
- **Connections:** Input from DP MCB; output feeds the Contactor and Surge Protector.

---

### 2.3 Type 2 AC Surge Protector (DIN Rail SPD)
- **Specifications:** 1-Phase + Neutral (2-pole), Max continuous operating voltage ($U_c$): 275V AC, Nominal discharge current ($I_n$): 20kA, Max discharge current ($I_{max}$): 40kA, Voltage protection level ($U_p$): <1.5 kV.
- **What It Is:** High-energy surge arrester using Metal Oxide Varistors (MOVs).
- **Why We Use It:** Shunts high-voltage grid switching transients and lightning surges directly to earth, protecting the contactor coil and controller power supplies.
- **Connections:** Wired in parallel across Live and Neutral downstream of the RCCB; Earth terminal connected to building earth electrode using 2.5 mm² green wire.

---

### 2.4 AC Contactor 25A (220V/230V Coil, 2-Pole + NO Aux Contact)
- **Specifications:** 2-Pole main poles, AC-3 rating: 25A (up to 4 kW / 5.5 HP motor at 230V), Coil voltage: 220V–240V AC 50/60Hz, Auxiliary contact: 1 Normally Open (NO, 13/14), Mechanical life: 10,000,000 cycles, Electrical life: 1,000,000 cycles.
- **What It Is:** Heavy-duty electromechanical power switch.
- **Why We Use It:** Standard relays cannot handle the heavy inductive inrush and arc breaking of a 1 HP to 2 HP submersible pump motor. A 25A contactor has arc chutes and silver-alloy contacts designed for inductive motor switching.
- **Terminal Connections:**
  - `L1` (1): 230V Live from RCCB.
  - `L2` (3): 230V Neutral from RCCB.
  - `T1` (2): Switched Live to Thermal Overload input.
  - `T2` (4): Switched Neutral to Thermal Overload input.
  - `A1`: 230V Live switched through the 5V Pilot Relay (fitted with RC Snubber).
  - `A2`: 230V Neutral bus (fitted with RC Snubber).
  - `13 / 14` (NO Aux Contact): Wired to PC817 Aux Feedback channel to let software detect contact welding.
- **Configuration:** `contactor_feedback_pin: 49`, `feedback_active_low: true`.

---

### 2.5 Thermal Overload Relay (Matched to Motor FLC)
- **Specifications:** Bimetallic thermal overload relay, adjustable current setting dial (e.g. 4.0A–6.0A for 1 HP, 7.0A–10.0A for 1.5 HP), Phase failure sensitive, 1 NC contact (95/96) + 1 NO contact (97/98), Manual/Auto reset selector.
- **What It Is:** Electromechanical thermal protection device.
- **Why We Use It:** If the pump impeller gets jammed with silt or mud, current rises to 2–3x normal levels. The thermal overload detects the prolonged heat and trips mechanically before motor windings burn out.
- **Critical Rule:** Software current monitoring (e.g. PZEM-004T) **NEVER replaces the physical thermal overload relay**!
- **Connection:**
  - Pins plug directly into contactor load terminals `T1 / T2`.
  - Output terminals connect to the submersible pump cable.
  - Terminal `95 / 96` (NC auxiliary contact): Wired in series with contactor coil `A1`. If an overload trips, `95/96` mechanically cuts power to the coil.
- **Adjustment:** Set the current dial to 1.1x the motor nameplate Full Load Current (FLC).

---

### 2.6 5V Relay Module (Pilot Relay)
- **Specifications:** 1-Channel optocoupled relay, Coil: 5V DC (70mA), Contacts: 250V AC 10A, Transistor driver onboard, Active-High trigger.
- **What It Is:** Small pilot relay acting as a signal amplifier.
- **Why We Use It:** The ROCK Pi S outputs 3.3V logic signals that cannot directly switch a 230V AC contactor coil. The pilot relay bridges the 3.3V logic to 230V AC coil power.
- **Connection:**
  - `VCC` $\rightarrow$ Powered from the end of the float interlock chain (`interlock_out`). If either float switch opens, VCC to the pilot relay is physically broken!
  - `GND` $\rightarrow$ Common Ground.
  - `IN` $\rightarrow$ ROCK Pi S Pin 15 (GPIO1_C0 / Line 48).
  - `COM` $\rightarrow$ 230V AC Live (from selector switch AUTO contact).
  - `NO` $\rightarrow$ Contactor terminal `A1`.
- **Configuration:** `contactor_relay_pin: 48`, `contactor_relay_active_high: true`.

---

### 2.7 RC Snubber (0.1 µF 400V/630V + 100 Ohm 2W)
- **Specifications:** 0.1 µF (100nF) 630V Metallized Polypropylene Film Capacitor (X2 rated) in series with a 100 Ω 2W flameproof metal oxide resistor.
- **What It Is:** Electrical filter absorbing voltage spikes across inductive loads.
- **Why We Use It:** When the contactor coil de-energizes, the collapsing magnetic field produces an inductive kickback spike exceeding 800V. This arc damages pilot relay contacts and radiates EMI that crashes microcontrollers. The snubber absorbs this energy safely.
- **Connection:** Wired directly in parallel across contactor coil terminals `A1` and `A2`.

---

### 2.8 22mm Selector Switch, 3-Position (Auto / Off / Manual)
- **Specifications:** 22 mm panel cutout, 3-position maintained rotary cam switch (45° angle), 2 contact blocks (1 NO + 1 NC or 2 NO), 240V AC 10A rated.
- **What It Is:** Industrial rotary mode selector switch.
- **Why We Use It:** Provides manual override capability for pump testing, maintenance, or SBC replacement:
  - **AUTO:** Powers contactor coil through the ROCK Pi S pilot relay and interlock chain.
  - **OFF:** Coil circuit is completely open. Motor cannot start under any condition.
  - **MANUAL:** Bypasses the SBC and energizes contactor coil directly through the Sump float (preventing dry-running even in manual mode).

---

### 2.9 Mains Enclosure IP65 250x200x120 mm
- **Specifications:** Heavy-duty polycarbonate industrial cabinet with hinged door and lock, galvanized steel backplate, IP65 rated.
- **What It Is:** Enclosure housing high-voltage 230V mains components.
- **Why We Use It:** Keeps 230V mains components physically separate from low-voltage logic, preventing electrical accidents and containing arcing.

---

### 2.10 DIN Rail 35 mm (Top-Hat Profile)
- **Specifications:** Standard EN 50022 / IEC 60715, 35 mm width, 7.5 mm depth, slotted zinc-plated steel.
- **What It Is:** Mounting rail for snap-on electrical equipment.
- **Why We Use It:** Rigidly mounts the MCB, RCCB, Surge Arrester, Contactor, and Overload inside the enclosure.

---

### 2.11 Wire Ferrules and Lugs Kit
- **Specifications:** Insulated cord-end terminals (0.5 mm², 0.75 mm², 1.5 mm², 2.5 mm²), copper sleeve, tin-plated.
- **What It Is:** Crimp sleeves fitted over stranded wire ends.
- **Why We Use It:** Stranded copper wires fray when tightened under screw terminals, creating loose strands and high contact resistance that can cause fires. Ferrules bundle strands into a solid, gas-tight cylinder.

---

### 2.12 2.5 mm² FRLS Copper Wire (Red, Black, Green)
- **Specifications:** Multi-strand flexible copper wire, Flame Retardant Low Smoke (FRLS) insulation, 1100V grade, IS 694 / IEC 60227.
- **What It Is:** High-current electrical cable.
- **Why We Use It:** Safely carries up to 20A continuous motor current with negligible voltage drop.
  - Red: 230V Live (Phase)
  - Black: 230V Neutral
  - Green: Protective Earth Ground

---

### 2.13 DIN-Rail Terminal Blocks 2.5 mm² + End Stops
- **Specifications:** Modular screw-clamp feed-through terminals, 800V / 24A rating, snap-on end stops.
- **What It Is:** Modular wiring connectors mounted on the DIN rail.
- **Why We Use It:** Neatly organizes incoming mains, contactor outputs, and pump motor cable connections. End stops prevent components from sliding along the rail under pump vibration.

---

### 2.14 PG11 / PG13.5 Glands (Mains)
- **Specifications:** Heavy-duty compression cable glands.
  - PG11: For cables 5–10 mm diameter.
  - PG13.5: For cables 6–12 mm diameter (ideal for 3-core 2.5 mm² mains cables).
- **What It Is:** Cable seals for high-voltage wire entries.
- **Why We Use It:** Preserves IP65 enclosure integrity against moisture and dust ingress.

---

## 6. Section 3: Hardwired Tank & Sump Sensors

---

### 3.1 Tank Float Switches (Full = NC, Low = Reversible)
- **Specifications:** Vertical magnetic reed float switches, Stainless steel 304/316 or Polypropylene, Switching voltage: up to 220V DC/AC, Current: 0.5A, Temperature: -20°C to +80°C.
- **What It Is:** Electromechanical water level switches using an internal reed switch and a floating magnet.
  - **Full Float (NC - Top of Tank):** Mounted at the 100% capacity mark. Contacts stay CLOSED when hanging in air; opens when water lifts the float.
  - **Low Float (Reversible - 20% Height):** Mounted at the replenishment threshold mark. Inverted so contacts CLOSE when water drops below it, signalling the controller to start the pump.
- **3-Wire Interlock Midpoint Tap Schematic:**
  ```
  +5V DC Fused (0.5A)
         │
         ▼
  [ Full Float (NC, Top) ]  <--- Opens when overhead tank reaches 100%
         │
         ├──────────────────────> Wire 1: Tap to PC817 IN3 (interlock_mid)
         ▼
  [ Sump Float (NO, Bottom)] <--- Opens if underground sump runs dry
         │
         ├──────────────────────> Wire 2: Tap to PC817 IN2 (interlock_out)
         ▼
  [ 5V Pilot Relay VCC ]    <--- Power physically CUT if either float trips
  ```
- **Truth Table & Anomaly Detection:**
  | IN2 (interlock_out) | IN3 (interlock_mid) | Physical Tank Status | Software Action |
  | :---: | :---: | :--- | :--- |
  | **HIGH** | **HIGH** | Healthy: Sump has water, tank has space. | Motor start permitted. |
  | **LOW** | **LOW** | Overhead Tank Full (Full float open). | Motor stopped normally. |
  | **LOW** | **HIGH** | Sump Dry (Sump float open). | Motor stopped; dry-run lockout. |
  | **HIGH** | **LOW** | **IMPOSSIBLE STATE** (Wiring broken/shorted). | **WIRING_FAULT alarm; hard lockout.** |

---

### 3.2 Sump Float (NO Type, Closes When Wet)
- **Specifications:** Weighted drop-in cable float switch, double chamber polypropylene, 5-meter neoprene cable, microswitch rating: 250V AC 10A.
- **What It Is:** Weighted hanging float switch suspended in the source well or sump.
- **Why We Use It:** Protects submersible pumps from dry-running (loss of prime), which can destroy water seals and overheat bearings within 60 seconds.
- **Operation:** When submerged in water, buoyancy tilts the float upward, closing internal contacts. If water drops below safe suction depth, the float hangs downward, opening the circuit.

---

### 3.3 4-Core Shielded Cable 0.5 mm²
- **Specifications:** 4 copper conductors (Red, Black, White, Green), tinned copper drain wire, aluminium foil shield, PVC outer sheath.
- **What It Is:** Multi-core shielded instrumentation cable.
- **Why We Use It:** Runs between the Logic Enclosure and the Overhead Tank (carrying 5V supply, Full float tap, Low float tap, and Ground). The foil shield blocks 50Hz electromagnetic noise induced by parallel mains conduit.
- **Crucial Shield Grounding Rule:** Connect the bare shield drain wire to the controller enclosure star ground **at one end only**. Leave the tank end floating to prevent circulating ground loops.

---

### 3.4 2-Core Shielded Cable 0.5 mm²
- **Specifications:** 2 conductors (Red, Black), aluminium foil shield, drain wire.
- **What It Is:** Dedicated shielded pair for the sump float switch run.

---

## 7. Section 4: Wireless Garden Station (Irrigation Node & Gateway)

---

### 4.1 ESP32-C3 Super Mini (Gateway + Garden Node)
- **Specifications:** 32-bit RISC-V single-core processor @ 160 MHz, 400KB SRAM, 4MB Flash, 2.4 GHz Wi-Fi (802.11 b/g/n) + Bluetooth 5 (LE), onboard ceramic antenna, USB Type-C.
- **Quantity:** 2 units:
  - **Gateway:** Connected to ROCK Pi S via USB (`/dev/garden-gateway`).
  - **Garden Node:** Mounted in the garden project box.
- **Why We Use It:** Encrypted unicast ESP-NOW operates peer-to-peer without requiring a home Wi-Fi router, cloud connection, or IP stack, providing 150+ meter outdoor range with sub-10ms latency.
- **PlatformIO Compilation:**
  ```bash
  # Flash Gateway:
  python -m platformio run -d firmware/gateway --target upload
  # Flash Garden Node:
  python -m platformio run -d firmware/node --target upload
  ```

---

### 4.2 ADS1115 16-Bit I2C ADC Module (Address 0x48)
- **Specifications:** 16-bit resolution, 4 single-ended or 2 differential channels, I2C interface, internal PGA, sample rate: 8–860 SPS.
- **Why We Use It:** The ESP32-C3 onboard ADC is noisy and non-linear. The ADS1115 delivers clean, stable 16-bit measurements across all 4 soil probes.
- **Connections (to Garden Node ESP32-C3):**
  - `VDD` $\rightarrow$ 3.3V DC
  - `GND` $\rightarrow$ Common Ground
  - `SDA` $\rightarrow$ ESP32-C3 GPIO 6 (with 4.7k pull-up)
  - `SCL` $\rightarrow$ ESP32-C3 GPIO 7 (with 4.7k pull-up)
  - `ADDR` $\rightarrow$ GND (sets I2C address to `0x48`)
  - `A0 .. A3` $\rightarrow$ Analog outputs from Soil Probes 1 through 4.

---

### 4.3 Capacitive Soil Moisture Sensors (v1.2 / v2.0)
- **Specifications:** Operating voltage: 3.3V DC, Output: 1.2V–2.6V analog voltage, capacitive frequency measurement, corrosion-resistant solder-mask coating.
- **Why We Use It:** Cheap resistive probes with exposed metal tracks corrode and dissolve within 2 weeks due to DC electrolysis. Capacitive probes have completely insulated copper traces that never touch soil directly, lasting years.
- **Calibration:**
  - Dry air: ~2.6V (0% moisture)
  - Water cup: ~1.2V (100% moisture)
  - Captured during Wizard Step 6.

---

### 4.4 4-Channel LR7843 Logic-Level N-MOSFET Board
- **Specifications:** LR7843 N-Channel MOSFET, $V_{ds} = 30\text{V}$, $I_d = 161\text{A}$ (module rated 15A continuous with heatsink), logic-level gate threshold ($V_{gs(th)} \approx 1.5\text{V}$–$2.3\text{V}$), optocoupled inputs.
- **Why We Use It:** Allows 3.3V ESP32 GPIOs to switch 12V 2A DC pumps directly with zero mechanical wear or contact bounce.
- **Connections:**
  - `CH1 .. CH4 Inputs` $\rightarrow$ ESP32-C3 GPIOs 0, 1, 3, 10.
  - `DC+ / DC- In` $\rightarrow$ 12V 3A DC power adapter.
  - `Load 1 .. 4 Out` $\rightarrow$ 12V mini submersible pumps 1 through 4.

---

### 4.5 12V Mini Submersible Pumps (Brushless DC)
- **Specifications:** 12V DC, 1.5A–2.0A operating current, flow rate: ~240 L/h, static head: 3 meters, brushless motor.
- **Firmware Protection Rules:**
  - Max 60 seconds continuous pulse per run.
  - Max 2 concurrent pumps active on the DC bus (capping DC current to 4.0A).
  - Immediate shutoff if reservoir float opens or radio link drops for >60s.

---

### 4.6 1N4007 Flyback Diodes
- **Specifications:** 1000V peak reverse voltage, 1A average forward current, DO-41 axial package.
- **Why We Use It:** When a MOSFET switches off a DC pump motor, the collapsing magnetic field produces an inductive voltage spike. The flyback diode shunts this spike safely, protecting the MOSFET.
- **Connection:** Solder directly across each pump's power terminals in **reverse bias** (Cathode [silver band] to +12V; Anode to MOSFET drain / pump negative lead).

---

### 4.7 Polypropylene (PP) Float Switch (Reservoir Dry-Run)
- **Specifications:** Miniature vertical reed float switch, PP plastic, 10W contact rating.
- **Why We Use It:** Mounted inside the drip reservoir bucket. If the bucket runs out of water, running micro-pumps dry will melt their plastic impellers.
- **Connection:** Wired between ESP32-C3 GPIO 5 and GND (internal pull-up enabled).
- **Firmware Reaction:** Hardware interrupts immediately kill all active pump pulses within 1 ms if this float opens.

---

### 4.8 12V 3A DC Power Adapter
- **Specifications:** Switch-mode desktop AC-DC adapter, Input: 100–240V AC, Output: 12V DC 3.0A (36 Watts), ripple: <100 mV.
- **Why We Use It:** Powers the 12V garden pumps and the buck converter reliably.

---

### 4.9 MP1584EN Buck Converter (Garden Node, to 5V)
- **Specifications:** Ultra-small DC-DC step-down module (22x17 mm), Input: 7V–28V, Output: 5.0V DC, Current: up to 3A, Efficiency: 90%.
- **Why We Use It:** Drops incoming 12V garden power to a stable 5.0V to power the ESP32-C3 through its 5V pin.

---

### 4.10 YF-S401 / YF-S201 Pulse Flow Meter
- **Specifications:** Hall-effect turbine flow sensor.
  - YF-S401: Range: 0.3–6.0 L/min (4mm drip lines; K-factor = 98 Hz per L/min = ~5,880 pulses/L).
  - YF-S201: Range: 1.0–30.0 L/min (1/2" supply lines; K-factor = 7.5 Hz per L/min = 450 pulses/L).
- **Why We Use It:** Confirms that water is physically flowing through Zone 1. If the pump is running but no pulses are counted, an airlock or pipe disconnect is flagged.
- **Voltage Divider Wiring:**
  ```
  Flow Meter 5V Signal Wire ──[ 10 kΩ Resistor ]──┬──► ESP32-C3 GPIO 4 (3.3V Logic Safe)
                                                  │
                                          [ 20 kΩ Resistor ]
                                                  │
                                                  ▼
                                            Common Ground
  ```

---

### 4.11 4 mm Check Valves (Non-Return Valves)
- **Specifications:** Plastic inline one-way valve with silicone diaphragm, cracking pressure: <0.05 bar.
- **Why We Use It:** Installed in the drip delivery line immediately after each pump. Keeps tubing primed with water between cycles, eliminating startup airlocks.

---

### 4.12 4 mm Barbed Anti-Siphon Tees
- **Specifications:** 3-way barbed tee with an elevated vacuum breaker pinhole.
- **Why We Use It:** Prevents accidental gravity siphoning from draining the entire reservoir onto plant beds after the pump stops.

---

### 4.13 4/7 mm Silicone / PVC Tubing
- **Specifications:** 4 mm internal diameter, 7 mm outer diameter, food-grade silicone or UV-treated PVC.
- **Why We Use It:** Flexible micro-irrigation distribution hose delivering water to plant pots.

---

### 4.14 Drip Stakes / Adjustable Emitters
- **Specifications:** 0–70 L/h adjustable rotary drippers on ground stakes.
- **Why We Use It:** Delivers water directly to the plant root zone without splashing foliage.

---

### 4.15 Pump Inlet Strainer / Pre-Filter
- **Specifications:** 80-mesh fine stainless-steel screen filter cap.
- **Why We Use It:** Prevents algae and organic sediment from entering and jamming pump impellers.

---

### 4.16 Garden Enclosure IP65 150x110x70 mm & PG7 Glands
- **Specifications:** Weatherproof ABS project box with silicone gasket and PG7 cord grips.
- **Why We Use It:** Protects garden node electronics from rain, sprinkler spray, and morning dew.

---

### 4.17 Pump Outlet Reducers / Adapters
- **Specifications:** Barbed fittings adapting pump outlets (8mm/10mm) down to 4/7mm drip tubing.

---

## 8. Section 5: Optional Extras & Redundancy Components

---

### 5.1 Rooftop Redundancy Node
- **Specifications:** Standalone ESP32-C3 node in an IP65 box with an SS316 stainless-steel float switch mounted on the rooftop tank rim.
- **Why We Use It:** Provides defense-in-depth against primary float cable cuts or mechanical jamming on high-value properties. If the primary float fails, this node broadcasts an emergency stop packet over ESP-NOW directly to the head-end.

---

### 5.2 BME280 Environmental Sensor (I2C)
- **Specifications:** I2C address `0x76` / `0x77`, Temp: -40°C to +85°C (±0.5°C), Humidity: 0–100% RH (±3%), Pressure: 300–1100 hPa.
- **Why We Use It:** Local micro-climate sensing. The controller automatically skips irrigation if rain probability > 60% or humidity > 90%.
- **Connection:** Wired to Garden Node I2C bus (SDA: GPIO 6, SCL: GPIO 7, 3.3V, GND).

---

### 5.3 0.96-Inch OLED Display (SSD1306, 128x64 I2C)
- **Specifications:** 128x64 pixels, monochrome white/blue, I2C address `0x3C`, 3.3V supply.
- **Why We Use It:** Enclosure-mounted status screen displaying tank level %, motor state, IP address, and active alarms without needing a phone or PC.
- **Connection:** ROCK Pi S Header 1 I2C7 (Pin 3: SDA, Pin 5: SCL, Pin 1: 3.3V, Pin 6: GND).
- **Configuration:**
  ```yaml
  display:
    enabled: true
    i2c_bus: 7
    i2c_address: 0x3C
    refresh_interval_s: 2.0
  ```

---

### 5.4 Active Piezo Buzzer (5V DC)
- **Specifications:** 5V DC active buzzer, resonant frequency: 2.3 kHz, sound pressure: 85 dB @ 10cm.
- **Why We Use It:** Audible alarm for Critical Priority 0 events (pipe leak, wiring fault, welded contactor) when voice audio is muted or disconnected.
- **Connection:** ROCK Pi S Pin 13 (GPIO1_B6 / Line 46) drives buzzer (+) through a 330 Ω resistor; (-) to GND.
- **Configuration:**
  ```yaml
  buzzer:
    enabled: true
    gpio_pin: 46
    active_high: true
    beep_on_alarm: true
  ```

---

### 5.5 Extra ADS1115 (Address 0x49)
- **Specifications:** ADS1115 with `ADDR` pin tied to `VDD` (3.3V), setting I2C address to `0x49`.
- **Why We Use It:** Expands the garden node from 4 soil moisture zones to 8 soil moisture zones.
- **Connection:** Connects in parallel to the first ADS1115 on I2C pins GPIO 6 and GPIO 7.

---

## 9. Scaling & Expansion Handbook: "If I Increase The Items"

---

### 9.1 Scaling Irrigation Zones (from 4 up to 8 or 16 Zones)
1. **Hardware Additions:**
   - Add a second 4-channel LR7843 MOSFET module.
   - Add a second ADS1115 with `ADDR` tied to `3.3V` (I2C address `0x49`).
   - Wire gate inputs to remaining ESP32 GPIOs.
2. **Firmware Configuration:**
   - In `firmware/node/include/config.h`:
     ```c
     #define NUM_ZONES 8
     ```
3. **Software Configuration:**
   - Add `zone_5` through `zone_8` in `/etc/smart-water/config.yaml`:
     ```yaml
     zones:
       zone_5:
         name: "Citrus Trees"
         enabled: true
         soil_dry_threshold_pct: 35.0
         pulse_seconds: 20
         calibration:
           dry_v: 2.65
           wet_v: 1.20
     ```

---

### 9.2 Sizing Up the Pump Motor (0.5 HP to 5 HP Electrical Sizing Table)

| Motor Rating | Supply Voltage | Full Load Current (FLC) | Contactor Rating (AC-3) | Thermal Overload Range | DP MCB Rating | Minimum Cable Size | Max Runtime Setting |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **0.5 HP (0.37 kW)** | 230V 1-Phase | ~3.2 A | 16 A | 2.5 A – 4.0 A | 10 A (C-Curve) | 1.5 mm² Copper | 600 s (10 min) |
| **1.0 HP (0.75 kW)** | 230V 1-Phase | ~6.0 A | 25 A | 5.5 A – 8.0 A | 16 A (C-Curve) | 2.5 mm² Copper | 900 s (15 min) |
| **1.5 HP (1.1 kW)** | 230V 1-Phase | ~8.5 A | 25 A | 7.0 A – 10.0 A | 20 A (C-Curve) | 2.5 mm² Copper | 1200 s (20 min) |
| **2.0 HP (1.5 kW)** | 230V 1-Phase | ~11.5 A | 32 A | 10.0 A – 14.0 A | 25 A (C-Curve) | 4.0 mm² Copper | 1500 s (25 min) |
| **3.0 HP (2.2 kW)** | 230V 1-Phase | ~16.0 A | 40 A | 14.0 A – 20.0 A | 32 A (C-Curve) | 6.0 mm² Copper | 1800 s (30 min) |
| **5.0 HP (3.7 kW)** | 415V 3-Phase | ~7.8 A / phase | 25 A (3-Pole) | 7.0 A – 10.0 A | 20 A (3P C-Curve)| 4.0 mm² Copper | 2400 s (40 min) |

---

### 9.3 Adding Continuous Level Sensing (Ultrasonic vs. Submersible Pressure)

To monitor exact litres and water level percentage on the dashboard:

```
Option A: JSN-SR04T Ultrasonic                 Option B: 4-20mA Submersible Pressure
+---------------------------------------+      +---------------------------------------+
| Mounted at top center of tank lid     |      | Lowered to very bottom of tank floor  |
| Measures distance to water surface    |      | Measures hydrostatic water head weight|
| Requires blind-zone offset (~20 cm)   |      | Zero blind zone, immune to foam/steam |
| Connected via GPIO Echo/Trigger       |      | Connected via ADS1115 + 250 Ohm Shunt |
+---------------------------------------+      +---------------------------------------+
```

- **Configuration (`/etc/smart-water/config.yaml`):**
  ```yaml
  level_sensor:
    enabled: true
    sensor_type: "pressure_transducer"  # or "jsn_sr04t"
    i2c_bus: 7
    i2c_address: 0x48
    adc_channel: 1
    shunt_resistance_ohms: 250.0        # 250 Ohm precision shunt
    sensor_range_cm: 200.0              # 2-meter depth probe
    tank_shape: "cylinder"
    tank_height_cm: 180.0
    tank_diameter_cm: 120.0
    predictive_stop_enabled: true
    predictive_stop_pct: 95.0
    night_leak_detection_enabled: true
  ```

---

### 9.4 Multi-Tank Management (Dual Overhead Tanks & Sump Cascades)
To manage two overhead tanks with independent valves:
1. Add an extra PC817 channel for the second tank's float switch.
2. Add a motorized ball valve driver on the manifold.
3. Configure dual rules in the Rules Builder: Tank A fills first; upon reaching full, Valve A closes and Valve B opens until Tank B fills.

---

### 9.5 Solar & Battery Off-Grid Power Sizing for Garden Node
For gardens without mains electricity:
- **Daily Energy Consumption:**
  - ESP32-C3 sleeping between packets: ~15 mA average @ 5V = 1.8 Wh/day.
  - 4 Mini pumps running 2 min/day each: $4 \times (2\text{A} \times 12\text{V} \times 0.033\text{h}) = 3.2\text{ Wh/day}$.
  - Total Daily Consumption: **~5.0 Watt-hours / day**.
- **Recommended Off-Grid Sizing:**
  - **Solar Panel:** 20 Watt 18V Monocrystalline Panel.
  - **Solar Charge Controller:** 12V 10A PWM Controller.
  - **Battery:** 12V 7Ah Sealed Lead-Acid (SLA) or 12V 6Ah LiFePO4 battery (provides 7+ days of autonomy during monsoon clouds).

---

## 10. End-to-End Master Pinout, Wiring & Terminal Matrix

| Function / Component | Board / Device | Pin / Terminal | Wire Gauge | Wire Color | Destination / Terminal | Logic / Voltage |
| :--- | :--- | :---: | :---: | :---: | :--- | :---: |
| **DC System Rail (3.3V)** | ROCK Pi S H1 | Pin 1 | 22 AWG | Red | PC817 VCC, DS3231 VCC, OLED VCC | 3.3V DC Stable |
| **DC Supply Rail (5.0V)** | ROCK Pi S H1 | Pin 2 | 22 AWG | Orange | Fused Float Chain Supply, Buzzer | 5.0V DC Fused |
| **I2C7 SDA** | ROCK Pi S H1 | Pin 3 (Line 50) | 24 AWG | Blue | DS3231 SDA, SSD1306 OLED SDA | 3.3V Pull-Up |
| **I2C7 SCL** | ROCK Pi S H1 | Pin 5 (Line 51) | 24 AWG | Yellow | DS3231 SCL, SSD1306 OLED SCL | 3.3V Pull-Up |
| **Common DC Ground** | ROCK Pi S H1 | Pin 6, 14 | 20 AWG | Black | Logic Enclosure Star Ground | 0V Reference |
| **IN1 (Tank Low Float)** | ROCK Pi S H1 | Pin 7 (Line 42) | 24 AWG | White | PC817 Channel 1 Collector | Active LOW (0) |
| **IN2 (Interlock Out)** | ROCK Pi S H1 | Pin 9 (Line 43) | 24 AWG | Brown | PC817 Channel 2 Collector | Active LOW (0) |
| **IN3 (Interlock Mid)** | ROCK Pi S H1 | Pin 11 (Line 44) | 24 AWG | Gray | PC817 Channel 3 Collector | Active LOW (0) |
| **IN4 (Manifold Leak)** | ROCK Pi S H1 | Pin 12 (Line 45) | 24 AWG | Violet | PC817 Channel 4 Collector | Active LOW (0) |
| **Piezo Buzzer Output** | ROCK Pi S H1 | Pin 13 (Line 46) | 24 AWG | Pink | Active Piezo Buzzer (+) via 330 Ω | Active HIGH (1) |
| **Pilot Relay Drive** | ROCK Pi S H1 | Pin 15 (Line 48) | 22 AWG | Green | 5V Pilot Relay IN Terminal | Active HIGH (1) |
| **Contactor Aux FB** | ROCK Pi S H1 | Pin 16 (Line 49) | 24 AWG | Blue/Wht | PC817 Aux Feedback Collector | Active LOW (0) |
| **230V Mains Live (In)** | Building DB | Phase Terminal | 2.5 mm² | Red | DP MCB Terminal 1 | 230V AC Phase |
| **230V Mains Neutral** | Building DB | Neutral Bus | 2.5 mm² | Black | DP MCB Terminal 3 | 0V AC Neutral |
| **Protective Earth** | Earth Electrode| Earth Bar | 2.5 mm² | Green | Enclosure Earth Stud & SPD Ground | Safety Earth |
| **Contactor Coil L** | Pilot Relay NO | Output Terminal | 1.0 mm² | Red | Contactor Terminal A1 (with Snubber)| 230V AC Switched |
| **Contactor Coil N** | Mains Neutral | Bus Terminal | 1.0 mm² | Black | Contactor Terminal A2 (with Snubber)| 230V AC Neutral |
| **Garden Pump 1 Gate** | ESP32-C3 Node | GPIO 0 | 24 AWG | Yellow | LR7843 MOSFET Gate 1 | 3.3V Active HIGH |
| **Garden Pump 2 Gate** | ESP32-C3 Node | GPIO 1 | 24 AWG | Blue | LR7843 MOSFET Gate 2 | 3.3V Active HIGH |
| **Garden Pump 3 Gate** | ESP32-C3 Node | GPIO 3 | 24 AWG | Green | LR7843 MOSFET Gate 3 | 3.3V Active HIGH |
| **Garden Pump 4 Gate** | ESP32-C3 Node | GPIO 10 | 24 AWG | White | LR7843 MOSFET Gate 4 | 3.3V Active HIGH |
| **Zone 1 Flow Pulse** | ESP32-C3 Node | GPIO 4 | 24 AWG | Orange | Voltage Divider Tap (10k/20k) | 0–3.3V Square Wave |
| **Reservoir Dry Float** | ESP32-C3 Node | GPIO 5 | 24 AWG | Gray | PP Float Switch (to GND) | Closed to GND = Wet |
| **Garden I2C SDA** | ESP32-C3 Node | GPIO 6 | 24 AWG | Blue | ADS1115 SDA, BME280 SDA | 3.3V Pull-Up |
| **Garden I2C SCL** | ESP32-C3 Node | GPIO 7 | 24 AWG | Yellow | ADS1115 SCL, BME280 SCL | 3.3V Pull-Up |

---

## 11. Pre-Power-On Multimeter Checklist ("Don't Blow Up Your SBC")

Before plugging in 230V mains or connecting power to the ROCK Pi S, complete this 10-step cold-check with a Digital Multimeter (DMM):

- [ ] **Test 1 (Galvanic Isolation):** Set DMM to Mega-Ohms ($\text{M}\Omega$). Measure resistance between 230V AC mains terminals and the ROCK Pi S DC ground terminal. **Result MUST read Open Circuit (OL / $\infty$).**
- [ ] **Test 2 (Buck Output Voltage):** Power the 12V UPS. Measure voltage between LM2596 `OUT+` and `OUT-` **with the ROCK Pi S unplugged**. Adjust potentiometer until voltage reads exactly **5.10V $\pm$ 0.05V**.
- [ ] **Test 3 (Float Loop Voltage):** Measure voltage on the external float switch loop. Confirm it does NOT exceed 5.0V DC.
- [ ] **Test 4 (Interlock Continuity):** With both floats hanging in dry air, confirm continuity between float chain input and output. Lift Full float; confirm circuit opens instantly.
- [ ] **Test 5 (Auxiliary Feedback Isolation):** Set DMM to continuity buzzer. Test contactor aux contacts `13 / 14`. Confirm open when contactor is de-energized, and closes when contactor is pushed in manually with an insulated screwdriver.
- [ ] **Test 6 (Flow Meter Voltage Divider):** Feed 5.0V into flow meter supply. Manually spin turbine; measure voltage on ESP32 GPIO 4 lead. **Voltage MUST peak at 3.3V $\pm$ 0.1V, never 5.0V!**
- [ ] **Test 7 (RC Snubber Placement):** Verify RC snubber is wired directly across contactor coil terminals `A1` and `A2`.
- [ ] **Test 8 (Flyback Diodes):** Verify 1N4007 diodes across all 4 garden pumps have their cathode (striped band) wired to +12V.
- [ ] **Test 9 (Grounding):** Confirm cable shield drain wire is connected to ground at the controller box only.
- [ ] **Test 10 (Dry Contactor Click):** Keep 230V mains MCB turned OFF. Run `python -m app.cli test-relay`. Verify contactor clicks cleanly without smoke or sparks.
