# Complete Industrial Bill of Materials (BOM) & Hardware Engineering Guide

> **Target Audience:** Practical Maker, Electrician, and Beginner ("Noob-Friendly").  
> **SoC / SBC:** Radxa ROCK Pi S (512MB RAM, Rockchip RK3308B)  
> **Operating System:** Armbian Debian 12 (Bookworm) Minimal Headless  
> **Application:** Dual-Domain Domestic Pump Automation & Precision Drip Irrigation Controller

---

## Table of Contents

1. [Understanding the System Architecture & Two Enclosures](#1-understanding-the-system-architecture--two-enclosures)
2. [What is the Hardware Commissioning & Calibration Wizard?](#2-what-is-the-hardware-commissioning--calibration-wizard)
3. [Time Window Selector & Operational Digest (1h, 6h, 24h, 48h)](#3-time-window-selector--operational-digest-1h-6h-24h-48h)
4. [Section 1: Controller Head-End (Logic Enclosure)](#4-section-1-controller-head-end-logic-enclosure)
5. [Section 2: Mains & Motor Control (Mains Enclosure)](#5-section-2-mains--motor-control-mains-enclosure)
6. [Section 3: Hardwired Tank & Sump Sensors](#6-section-3-hardwired-tank--sump-sensors)
7. [Section 4: Wireless Garden Station (Irrigation Node & Gateway)](#7-section-4-wireless-garden-station-irrigation-node--gateway)
8. [Section 5: Optional Extras & Redundancy Components](#8-section-5-optional-extras--redundancy-components)
9. [Scaling & Expansion Guide: "If I Increase the Items"](#9-scaling--expansion-guide-if-i-increase-the-items)
10. [End-to-End Master Pinout & Terminal Mapping Table](#10-end-to-end-master-pinout--terminal-mapping-table)

---

## 1. Understanding the System Architecture & Two Enclosures

Before stripping wires or turning on power, understand why the system is physically divided into **two separate enclosures**:

```
+------------------------------------+       +------------------------------------+
|  ENCLOSURE 1: HIGH VOLTAGE MAINS   |       |  ENCLOSURE 2: LOW VOLTAGE LOGIC    |
|  (IP65 250x200x120 mm)             |       |  (IP65 200x150x100 mm)             |
|                                    |       |                                    |
|  - 230V AC Mains Input             |       |  - Radxa ROCK Pi S (512MB)         |
|  - DP MCB (16A, C Curve)           |       |  - 4-Channel PC817 Optocoupler     |
|  - 2P 25A 30mA RCCB Shock Breaker  |       |  - DS3231 Precision I2C RTC        |
|  - Type 2 AC Surge Protector (SPD) |  5V   |  - CM108 USB Audio DAC + Speaker   |
|  - 25A AC Contactor (220V Coil)    | Pilot |  - USB Gateway (ESP32-C3)          |
|  - Thermal Overload Relay (Bimetal)| Relay |  - 12V Mini-UPS + Buck Converter   |
|  - Auto / Off / Manual 22mm Switch | <---> |  - Active Piezo Buzzer             |
|  - RC Snubber (0.1uF + 100 Ohm)    |       |  - Front-Panel 8-LED Diagnostic    |
+------------------------------------+       +------------------------------------+
```

### Why Two Enclosures? (The Critical Safety Rule)
1. **Electromagnetic Noise (EMI):** Switching a 230V submersible pump motor draws a large inductive inrush current that produces electrical sparks and magnetic fields. If the 3.3V Rockchip RK3308 computer sits right next to the 230V contactor without shielding, EMI will freeze the Linux kernel, corrupt the SD card, or cause false float sensor trips.
2. **Electrical Safety:** Separating 230V mains power from 5V/3.3V DC logic ensures that a water leak, condensation, or loose wire in the logic box can NEVER come into contact with 230V electricity.

---

## 2. What is the Hardware Commissioning & Calibration Wizard?

### Why Does the Wizard Exist?
When you first power on the controller on a real Rock Pi S, **the computer does not know**:
- Which physical pin is wired to which float switch.
- Whether your float switch is inverted (Active HIGH vs Active LOW).
- Whether your contactor coil pulls in within 500 milliseconds.
- Whether the garden node is within radio range.
- What raw voltage your soil probes report in dry air vs wet soil.

If the controller immediately started running automated pumping on first boot, it could accidentally turn on a 230V submersible pump with dry pipes, empty well, or closed valves!

Therefore, the controller boots into **`SAFE-NO-HARDWARE` mode** with all pump relays mechanically and electrically locked OFF. Navigating to `http://<tailscale-ip>:8000` opens the **7-Step Commissioning Wizard**:

| Wizard Step | What It Does | Why It Matters for Safety |
| :---: | :--- | :--- |
| **Step 1: Network & Security** | Binds to private Tailscale mesh (`100.x.y.z`) and sets Bearer token. | Blocks unauthorized access from public WiFi or LAN scanners. |
| **Step 2: Map Head-End Pins** | Live reads `/dev/gpiochip1` pin offsets and assigns IN1–IN4, Pilot Relay, and Aux Feedback. | Prevents sending 5V control signals to wrong GPIO pins. |
| **Step 3: Float Switches & Polarity** | Live test: lift and lower each physical float in a bucket of water. Confirms open/closed logic. | Ensures "Full" actually stops the pump and "Dry" blocks start. |
| **Step 4: Contactor Dry Click Test** | Sends a safe 0.5s test pulse to the contactor coil **with 230V mains disconnected**. | Verifies pilot relay switching and contactor auxiliary contact feedback. |
| **Step 5: Wireless Gateway Pairing** | Verifies serial link `/dev/garden-gateway` and ESP-NOW packets (`hb`, `soil`, `flow`). | Confirms PMK/LMK encryption and HMAC-SHA256 signatures match. |
| **Step 6: Probe & Flow Calibration** | Records soil probe voltage in dry air (~2.6V) and cup of water (~1.2V). Tests Zone 1 flow meter. | Establishes the 0.4V sanity margin and baseline L/min threshold. |
| **Step 7: Finalize Commissioning** | Saves verified config to `/var/lib/smart-water/settings.yaml`, seals wizard, unlocks auto-mode. | Prevents accidental configuration changes during production run. |

---

## 3. Time Window Selector & Operational Digest (1h, 6h, 24h, 48h)

The **Operational Digest** card and **Historical Charts** on the dashboard allow you to inspect water flow, pump starts, electrical energy, soil moisture trends, and safety trips across different operating windows:

- **1h (Past 1 Hour):** High-resolution diagnostic view. Used immediately after manual pumping or irrigation to verify water delivery rate and coil temperature.
- **6h (Past 6 Hours):** Half-day trend. Shows morning irrigation cycles and tank replenishment runs.
- **24h (Past 24 Hours - Default):** Full daily digest. Compares total household water consumed against total pumped.
- **48h (Past 48 Hours):** Two-day weekend overview. Helps identify slow pipe leaks (gradual tank level drop during zero-usage night hours).

### How the Time Window Works (Under the Hood):
1. **Dynamic Header & Badge:** When you click `1h`, `6h`, `24h`, or `48h`, the UI immediately updates:
   - Card Title: `1-Hour Operational Digest`, `6-Hour Operational Digest`, etc.
   - Badge: `Past 1 Hour`, `Past 6 Hours`, `Past 24 Hours`, `Past 48 Hours`.
   - Dispatch Button: `📩 Send 1h Summary Alert`, `📩 Send 6h Summary Alert`, etc.
2. **Dynamic Data Fetching:**
   - Queries `/api/summary/daily?hours=X` to aggregate runtime, starts, and kWh for that exact duration.
   - Queries `/api/history?limit=Y&from=Z` to load up to 3,000 downsampled data points across the exact time window.
   - Sets the chart horizontal X-axis start to `Date.now() - X * 3600 * 1000` and end to `Date.now()`, ensuring the visual timeline accurately spans the selected window.

---

## 4. Section 1: Controller Head-End (Logic Enclosure)

Components mounted inside the **Logic Enclosure (IP65 200x150x100 mm)**:

---

### 1.1 SanDisk Max Endurance 32GB microSD
- **What It Is:** High-endurance flash memory card designed specifically for continuous 24/7 write cycles (dashcam/surveillance rated).
- **Why We Use It:** Standard cheap SD cards corrupt within 3 to 6 months due to continuous Linux logging and database writes. Max Endurance uses high-grade pSLC/MLC NAND flash capable of 15,000+ hours of continuous writes.
- **How to Connect:** Insert directly into the Radxa ROCK Pi S Micro-SD slot before first boot.
- **How to Configure:** 
  - Formatted with ext4 filesystem.
  - SQLite is configured in WAL mode with `synchronous = NORMAL` and 90-day retention to minimize write wear:
    ```yaml
    database:
      retention_days: 90
      cache_size_kb: -2000
      busy_timeout_ms: 5000
    ```

---

### 1.2 CM108 USB Audio Dongle
- **What It Is:** C-Media CM108 USB-to-3.5mm audio adapter.
- **Why We Use It:** The ROCK Pi S has no onboard 3.5mm audio jack. The CM108 provides a Linux ALSA-compatible audio output for real-time synthesized voice alerts (e.g., *"Warning: Sump dry"*, *"Motor running: Tank low"*).
- **How to Connect:** Plug into the Powered USB Hub (or ROCK Pi S USB Type-A port). Connect 3.5mm stereo cable to the speaker.
- **How to Configure:** 
  - Linux package: `apt-get install -y espeak-ng alsa-utils`
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

### 1.3 3-5W USB Speaker
- **What It Is:** Small active loudspeaker with internal 5V amplifier.
- **Why We Use It:** Broadcasts audible voice alerts directly at the pump room so operators immediately know why a pump stopped without looking at their phone.
- **How to Connect:** 
  - 3.5mm audio jack $\rightarrow$ CM108 USB audio adapter.
  - USB Type-A power plug $\rightarrow$ Powered USB Hub 5V port.
- **Configuration:** Set volume using Linux mixer: `amixer sset 'Headphone' 80% unmute`.

---

### 1.4 Powered USB 2.0 Hub
- **What It Is:** 4-port USB 2.0 hub with external 5V 2A DC power input.
- **Why We Use It:** The ROCK Pi S onboard USB host port can only deliver ~500mA. Connecting the CM108 Audio DAC, USB Speaker, and ESP32-C3 Gateway simultaneously would cause a voltage brownout and reboot the SBC.
- **How to Connect:**
  - Upstream USB cable $\rightarrow$ ROCK Pi S USB Type-A port.
  - DC power socket $\rightarrow$ 5V output of the LM2596 buck converter.
  - Downstream ports $\rightarrow$ CM108 Audio, USB Speaker, Gateway ESP32-C3.

---

### 1.5 12V Mini-UPS (Uninterruptible Power Supply)
- **What It Is:** 12V DC battery backup unit (Li-ion 18650 based) with integrated charger and automatic switchover.
- **Why We Use It:** In developing areas, power fluctuations and brownouts cause micro-reboots. The 12V mini-UPS keeps the SBC, logic, and gateway running seamlessly for up to 4 hours during power cuts.
- **How to Connect:**
  - Input: 12V 2A AC-DC wall adapter.
  - Output: Feeds the 12V DC logic bus bar through a 2A inline fuse.

---

### 1.6 LM2596 / MP1584EN Buck Converter (Head-End)
- **What It Is:** High-efficiency step-down DC-DC switching regulator.
- **Why We Use It:** Steps down the 12V UPS battery bus to a clean, stable 5.1V DC rail to power the ROCK Pi S and the Powered USB Hub without heat buildup.
- **How to Connect:**
  - `IN+` $\rightarrow$ +12V UPS rail (fused).
  - `IN-` $\rightarrow$ DC Common Ground.
  - `OUT+` $\rightarrow$ +5.1V DC (Tune potentiometer with a multimeter before connecting SBC!).
  - `OUT-` $\rightarrow$ DC Common Ground.

---

### 1.7 DS3231 Precision I2C RTC Module
- **What It Is:** Real-Time Clock with temperature-compensated crystal oscillator (TCXO) and CR2032 coin cell battery.
- **Why We Use It:** The ROCK Pi S has no battery-backed clock. When internet/NTP is lost during a storm or network outage, the controller relies on the DS3231 to enforce quiet hours (22:00–06:00), irrigation schedules, and anti-short-cycling timers.
- **How to Connect (ROCK Pi S Header 1):**
  - `VCC` $\rightarrow$ Pin 1 (3.3V DC Rail)
  - `GND` $\rightarrow$ Pin 6 (Common Ground)
  - `SDA` $\rightarrow$ Pin 3 (I2C7 SDA / Line 50)
  - `SCL` $\rightarrow$ Pin 5 (I2C7 SCL / Line 51)
- **How to Configure:**
  - Add `overlays=i2c7` to `/boot/armbianEnv.txt`.
  - Verify detection: `sudo i2cdetect -y 7` (shows `0x68` or `UU`).

---

### 1.8 Inline Fuse Holders and Fuses
- **What It Is:** Waterproof automotive blade or glass cartridge fuse holders.
- **Why We Use It:** Prevents electrical fire or destroyed circuit boards if a wire chafes or shorts against the metal DIN rail or chassis.
- **Ratings & Placement:**
  - **Fuse 1 (2.0A Quick-Blow):** Between 12V UPS battery output and Buck Converter.
  - **Fuse 2 (0.5A Fast-Acting):** On the +5V supply line feeding the external float switch loop.

---

### 1.9 4-Channel PC817 Optocoupler Isolation Board
- **What It Is:** 4 independent optical isolators (infrared LED shining on a phototransistor).
- **Why We Use It:** Provides 5,000V RMS galvanic isolation between field sensor cables and the fragile 3.3V Rockchip RK3308 inputs. It stops induced lightning surges, mains hum, and high voltages from reaching the SBC.
- **How to Connect:**
  - **Field Input Side (Low-Voltage Float Domain):**
    - `IN1`: Tank Low Float return wire.
    - `IN2`: Interlock Output (end of float chain).
    - `IN3`: Interlock Midpoint tap (between Full float & Sump float).
    - `IN4`: Water Leak Sensor digital output.
  - **SBC Output Side (3.3V Domain):**
    - `VCC` $\rightarrow$ ROCK Pi S Pin 1 (3.3V Rail).
    - `GND` $\rightarrow$ ROCK Pi S Pin 6 (GND).
    - `OUT1` $\rightarrow$ Pin 7 (GPIO1_B2 / Line 42) — Tank Low.
    - `OUT2` $\rightarrow$ Pin 9 (GPIO1_B3 / Line 43) — Interlock Out.
    - `OUT3` $\rightarrow$ Pin 11 (GPIO1_B4 / Line 44) — Interlock Mid.
    - `OUT4` $\rightarrow$ Pin 12 (GPIO1_B5 / Line 45) — Leak Sensor.
- **Configuration (`/etc/smart-water/config.yaml`):**
  ```yaml
  head_end:
    chip_path: "/dev/gpiochip1"
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
- **What It Is:** Transient Voltage Suppression diodes (clamping voltage ~18V).
- **Why We Use It:** Outdoor sensor wires running up to rooftops act as antennas during thunderstorms. The TVS diode instantly shunts high-voltage electrostatic discharge (ESD) spikes to earth before they can damage the PC817 optocouplers.
- **How to Connect:** Connect in parallel across each field sensor terminal pair (one leg to sensor wire, other leg to enclosure star ground).

---

### 1.11 Water Leak Sensor (Digital, LM393 Comparator)
- **What It Is:** Gold-plated conductive track PCB pad with an LM393 sensitivity adjustment board.
- **Why We Use It:** Placed under the pump manifold and pipe unions. If a pipe burst, loose coupling, or seal failure occurs, water bridges the contacts, triggering an instant motor emergency stop within 50ms.
- **How to Connect:**
  - `VCC` $\rightarrow$ 5V DC Supply Rail.
  - `GND` $\rightarrow$ Common Ground.
  - `DO (Digital Out)` $\rightarrow$ PC817 Optocoupler Channel 4 (`IN4`).
- **Configuration:** Set sensitivity potentiometer until the onboard onboard LED is OFF when dry, and lights ON with one drop of water.

---

### 1.12 Logic Enclosure IP65 200x150x100 mm
- **What It Is:** Dust-tight, water-jet resistant polycarbonate industrial junction box with rubber gasket.
- **Why We Use It:** Houses the SBC, optocouplers, RTC, buck converters, and terminal blocks. Protects low-voltage electronics from dust, moisture, and insects.

---

### 1.13 PG7 / PG9 Cable Glands
- **What It Is:** Compression cable strain reliefs with rubber sealing rings.
  - **PG7:** Cable diameter 3.0–6.5 mm (ideal for 2-core and 4-core sensor wires).
  - **PG9:** Cable diameter 4.0–8.0 mm (ideal for DC power cables and USB leads).
- **Why We Use It:** Prevents water from dripping along cables into the enclosure; protects cables from being pulled loose from screw terminals.

---

### 1.14 WAGO 221 Lever Connectors (2-, 3-, 5-Way)
- **What It Is:** Maintenance-free spring-clamp electrical connectors.
- **Why We Use It:** Unlike screw terminal strips which loosen over time due to pump vibration, WAGO 221 cage clamps maintain constant spring pressure on stranded and solid wires. Perfect for splitting 5V, 12V, and Ground distribution rails.

---

### 1.15 10 kOhm Resistors, 1/4W
- **What It Is:** Standard 0.25W carbon film or metal film pull-up/pull-down resistors.
- **Why We Use It:** Used as pull-ups on open-collector sensor outputs and as part of voltage dividers (e.g. stepping down 5V pulses from flow meters to 3.3V).

---

### 1.16 Heat Shrink Kit (2–6 mm, Adhesive-Lined)
- **What It Is:** Dual-wall polyolefin tubing with internal hot-melt glue.
- **Why We Use It:** When heated with a heat gun, the internal glue melts to create a 100% submersible, waterproof seal over soldered wire splices in tank float lines.

---

### 1.17 UV-Resistant Cable Ties (150 mm, Black)
- **What It Is:** Carbon-black nylon zip ties rated for outdoor sunlight exposure.
- **Why We Use It:** White nylon ties crack and fail within 6 months outdoors under UV radiation. Black UV-stabilized ties secure rooftop cables neatly for 10+ years.

---

### 1.18 DC Barrel Pigtails 5.5 x 2.1 mm
- **What It Is:** Standard molded DC power jacks with pre-stripped red/black leads.
- **Why We Use It:** Quick, standardized connection between 12V mini-UPS, adapters, and the internal logic distribution terminals.

---

### 1.19 Terminal Blocks, Perfboard, Jumper Wires
- **What It Is:** Screw terminal strips, copper prototyping board, and DuPont jumper wires.
- **Why We Use It:** Organizes wiring inside the logic enclosure. Provides clean test points to measure voltages with a digital multimeter during commissioning.

---

## 5. Section 2: Mains & Motor Control (Mains Enclosure)

Components mounted inside the **Mains Enclosure (IP65 250x200x120 mm)** on a 35mm DIN rail:

---

### 2.1 Double-Pole MCB (16A, C Curve)
- **What It Is:** Miniature Circuit Breaker switching both Phase (Live) and Neutral simultaneously.
- **Why We Use It:**
  - **Double-Pole:** Ensures complete electrical isolation of both Live and Neutral when switched off for maintenance.
  - **C Curve Characteristic:** Allows inductive motor starting inrush current (5–10x full load current for ~100ms) without nuisance tripping.
- **Connection:** Incoming 230V AC mains connects directly to top terminals 1 and 3. Bottom terminals 2 and 4 feed the RCCB.

---

### 2.2 RCCB 2P 25A 30mA (Residual Current Circuit Breaker)
- **What It Is:** High-sensitivity earth leakage shock protector.
- **Why We Use It:** Submersible pumps operate immersed in water. If the motor seal fails or cable insulation frays, mains voltage leaks into the water tank. The RCCB detects leakage currents exceeding 30mA and trips within 40 milliseconds, preventing fatal electric shocks to building occupants.
- **Connection:** Placed immediately downstream of the DP MCB. Output feeds the AC Contactor and Surge Protector.

---

### 2.3 Type 2 AC Surge Protector (DIN Rail SPD)
- **What It Is:** High-energy Metal Oxide Varistor (MOV) surge arrester rated for 275V AC (In: 20kA, Imax: 40kA).
- **Why We Use It:** Clamps severe overvoltage spikes caused by nearby lightning strikes or grid switching, protecting the contactor coil and controller power supplies.
- **Connection:** Connect Line (L) and Neutral (N) in parallel across the mains bus, and connect the Earth terminal to the main building earth electrode with 2.5 mm² green wire.

---

### 2.4 AC Contactor 25A (220V/230V Coil, 2-Pole + NO Aux Contact)
- **What It Is:** Heavy-duty electromechanical power switch rated for inductive motor duty (AC-3 duty).
- **Why We Use It:** Small relays cannot safely switch the heavy inductive starting load of a 1 HP to 2 HP submersible pump motor. A 25A contactor features silver-alloy contacts designed to extinguish arcs and handle 100,000+ operations.
- **Key Terminals & Connections:**
  - `L1 / L2` (Top Mains Terminals): 230V Live and Neutral input from MCB/RCCB.
  - `T1 / T2` (Bottom Load Terminals): Switched 230V output to the pump motor.
  - `A1 / A2` (Coil Terminals): 230V AC coil power, switched by the 5V Pilot Relay.
  - `13 / 14` (NO Auxiliary Contact): Mechanically linked feedback switch. Connects to PC817 Aux Feedback channel to let software detect welded/stuck contacts.
- **Configuration:**
  ```yaml
  head_end:
    contactor_feedback_pin: 49  # GPIO1_C1 (Line 49 / Pin 16)
    feedback_active_low: true
  motor:
    pull_in_timeout_ms: 1000
    drop_out_timeout_ms: 1000
  ```

---

### 2.5 Thermal Overload Relay (Range Matched to Motor FLC)
- **What It Is:** Bimetallic electromechanical thermal protector mounted directly below the contactor.
- **Why We Use It:** If the pump impeller is jammed with silt or mud, the motor draws excessive current without short-circuiting. The thermal overload detects prolonged high current and mechanically trips before motor windings overheat and burn out.
- **Critical Rule:** Software-based current monitoring (e.g. PZEM-004T) is a convenience feature; it **NEVER replaces the physical thermal overload relay**!
- **Connection:**
  - Main pins plug directly into contactor `T1 / T2`.
  - Auxiliary NC contact (`95 / 96`) is wired in series with the contactor coil `A1` line so a thermal trip mechanically cuts coil power independently of software.
- **Adjustment:** Set the dial to 1.1x the motor nameplate Full Load Current (FLC).

---

### 2.6 5V Relay Module (Pilot Relay)
- **What It Is:** Optocoupled 1-channel relay module.
- **Why We Use It:** The ROCK Pi S outputs 3.3V logic signals that cannot directly switch a 230V contactor coil. The pilot relay acts as an amplifier: 3.3V GPIO energizes the pilot relay, and the pilot relay switches 230V to contactor `A1`.
- **How to Connect:**
  - `VCC` $\rightarrow$ Fed from the end of the float interlock chain (`interlock_out`). If either float switch opens, VCC is physically broken!
  - `GND` $\rightarrow$ Common Ground.
  - `IN` $\rightarrow$ ROCK Pi S Pin 15 (GPIO1_C0 / Line 48).
  - `COM / NO Contacts` $\rightarrow$ Switches 230V Live to contactor terminal `A1`.
- **Configuration:**
  ```yaml
  head_end:
    contactor_relay_pin: 48
    contactor_relay_active_high: true
  ```

---

### 2.7 RC Snubber (0.1 µF 400V/630V + 100 Ohm 2W)
- **What It Is:** Series combination of a metallized polypropylene film capacitor (X2 rated) and a flameproof resistor.
- **Why We Use It:** When the contactor coil is de-energized, its collapsing magnetic field generates a back-EMF inductive spike exceeding 800V. This arc welds pilot relay contacts and resets microcontrollers. The RC snubber absorbs the inductive kickback safely.
- **How to Connect:** Wire directly in parallel across contactor coil terminals `A1` and `A2`.

---

### 2.8 22mm Selector Switch, 3-Position (Auto / Off / Manual)
- **What It Is:** Industrial rotary cam switch with NO and NC contact blocks.
- **Why We Use It:** Allows manual pump operation during software maintenance, firmware flashing, or SBC hardware replacement:
  - **Position 1 (AUTO):** Contactor coil is controlled by the ROCK Pi S pilot relay and float interlock chain.
  - **Position 2 (OFF):** Physical circuit to coil is completely broken. Motor cannot start under any condition.
  - **Position 3 (MANUAL):** Bypasses the SBC and energizes contactor coil directly through the Sump float (preventing dry-running even in manual mode!).

---

### 2.9 DIN Rail 35 mm (Top-Hat Profile)
- **What It Is:** Standard 35mm slotted steel mounting rail.
- **Why We Use It:** Securely mounts the MCB, RCCB, Surge Protector, AC Contactor, Overload Relay, and DIN terminal blocks inside the mains enclosure without loose screws.

---

### 2.10 Wire Ferrules and Lugs Kit
- **What It Is:** Tin-plated copper sleeve tubes with plastic color-coded collars.
- **Why We Use It:** Stranded copper wire frays when tightened under screw terminals, causing loose strands, high contact resistance, and electrical fires. Crimping ferrules onto wire ends creates a solid, gas-tight cylindrical connection.

---

### 2.11 2.5 mm² FRLS Copper Wire (Red, Black, Green)
- **What It Is:** Flame Retardant Low Smoke (FRLS) multi-strand copper electrical cable.
  - **Red:** 230V AC Live (Phase)
  - **Black:** 230V AC Neutral
  - **Green:** Earth Ground
- **Why We Use It:** 2.5 mm² is safely rated for up to 20A continuous load, ensuring negligible voltage drop during motor starting. FRLS insulation prevents toxic smoke in case of fire.

---

### 2.12 DIN-Rail Terminal Blocks 2.5 mm² + End Stops
- **What It Is:** Modular feed-through screw clamp terminals with snap-on mechanical end brackets.
- **Why We Use It:** Neatly separates incoming building mains, contactor power circuits, and outgoing pump motor leads on the DIN rail. End stops prevent components from sliding along the rail.

---

### 2.13 PG11 / PG13.5 Glands (Mains)
- **What It Is:** Heavy-duty compression cable glands.
  - **PG11:** Cables 5.0–10.0 mm diameter.
  - **PG13.5:** Cables 6.0–12.0 mm diameter (ideal for 3-core 2.5 mm² round mains cable).
- **Why We Use It:** Seals 230V high-voltage cable entries against rain and bugs, maintaining IP65 enclosure integrity.

---

## 6. Section 3: Hardwired Tank & Sump Sensors

---

### 3.1 Tank Float Switches (Full = NC, Low = Reversible)
- **What It Is:** Stainless steel or polypropylene magnetic reed float switches.
  - **Full Float (NC - Normally Closed):** Mounted at the 100% top level mark. Contacts stay CLOSED when hanging in air; when water rises and lifts the float, contacts OPEN.
  - **Low Float (Reversible):** Mounted at the 20% replenishment threshold mark. Inverted so contacts CLOSE when water drops below it, signalling the controller that replenishment is needed.
- **Why We Use It:** Magnetic reed switches operate mechanically without exposed electrical probes, preventing corrosion and mineral scale buildup.
- **How to Wire the 3-Wire Interlock Chain:**
  ```
  +5V DC Fused 
       │
       ▼
  [ Full Float (NC) ]  <--- Opens when overhead tank reaches 100% capacity
       │
       ├──────────────────────> Wire 1: Tap to PC817 IN3 (interlock_mid)
       ▼
  [ Sump Float (NO) ]  <--- Held CLOSED by water in sump; opens if sump runs dry
       │
       ├──────────────────────> Wire 2: Tap to PC817 IN2 (interlock_out)
       ▼
  [ Pilot Relay VCC ]  <--- Power to pilot relay physically CUT if either float trips
  ```

---

### 3.2 Sump Float (NO Type, Closes When Wet)
- **What It Is:** Weighted polypropylene cable float switch suspended inside the source well or underground sump.
- **Why We Use It:** Protects submersible pumps from dry-running (loss of prime), which destroys pump water seals and overheats motor bearings within 60 seconds.
- **Operation:** When sufficient water exists, the float tilts upward, holding its internal microswitch CLOSED. If water drops below safe suction depth, the float hangs downward, OPENING the circuit.

---

### 3.3 4-Core Shielded Cable 0.5 mm²
- **What It Is:** 4 insulated copper conductors (Red, Black, White, Green) wrapped in aluminium foil with an uninsulated tinned copper drain wire.
- **Why We Use It:** Runs between the Logic Enclosure and the Overhead Tank (carrying 5V supply, Full float return, Low float return, and Ground). The foil shield blocks 50Hz electromagnetic noise induced by parallel mains conduit.
- **Shield Grounding Rule:** Connect the bare shield drain wire to the logic box star ground **at the controller end ONLY**. Leave the tank end floating!

---

### 3.4 2-Core Shielded Cable 0.5 mm²
- **What It Is:** 2-conductor shielded cable.
- **Why We Use It:** Used for the dedicated run down into the source sump for the dry-run float switch.

---

## 7. Section 4: Wireless Garden Station (Irrigation Node & Gateway)

Components for the remote garden drip irrigation node:

---

### 4.1 ESP32-C3 Super Mini (Gateway + Garden Node)
- **What It Is:** Ultra-compact 32-bit RISC-V microcontroller with onboard 2.4 GHz Wi-Fi/Bluetooth and ceramic antenna.
- **Quantity:** 2 units:
  - **Unit 1 (USB Gateway):** Plugged into the ROCK Pi S USB hub. Translates USB Serial JSON lines to 2.4 GHz ESP-NOW packets.
  - **Unit 2 (Garden Node):** Mounted in the garden. Drives the 4 irrigation pumps, reads soil moisture via ADS1115, and counts flow pulses.
- **Why We Use It:** Encrypted unicast ESP-NOW operates peer-to-peer without requiring a home Wi-Fi router, cloud connection, or IP stack, providing 150+ meter outdoor range with sub-10ms latency.
- **Firmware Compilation (PlatformIO):**
  ```bash
  # Flash Gateway:
  python -m platformio run -d firmware/gateway --target upload
  # Flash Garden Node:
  python -m platformio run -d firmware/node --target upload
  ```

---

### 4.2 ADS1115 16-Bit I2C ADC Module (Address 0x48)
- **What It Is:** High-precision 4-channel analog-to-digital converter with internal programmable gain amplifier (PGA).
- **Why We Use It:** The ESP32-C3 onboard ADC is notoriously non-linear and noisy. The ADS1115 provides genuine 16-bit resolution, allowing stable, jitter-free measurement of 4 capacitive soil moisture probes.
- **Connections (to Garden Node ESP32-C3):**
  - `VDD` $\rightarrow$ 3.3V DC
  - `GND` $\rightarrow$ Common Ground
  - `SDA` $\rightarrow$ ESP32-C3 GPIO 6 (with 4.7k pull-up)
  - `SCL` $\rightarrow$ ESP32-C3 GPIO 7 (with 4.7k pull-up)
  - `ADDR` $\rightarrow$ GND (sets I2C address to `0x48`)
  - `A0 .. A3` $\rightarrow$ Analog signal wires from Soil Probes 1 through 4.

---

### 4.3 Capacitive Soil Moisture Sensors (v1.2 / v2.0)
- **What It Is:** Corrosion-resistant soil moisture probes operating on capacitive frequency shifting.
- **Why We Use It:** Cheap resistive probes with exposed metal tracks corrode and dissolve into the soil within 2 weeks due to DC electrolysis. Capacitive probes have completely insulated solder-masked copper traces that never touch soil directly, lasting years.
- **Output:** Analog voltage inversely proportional to moisture:
  - Dry ambient air: ~2.6V
  - Saturated wet soil: ~1.2V
- **Calibration:** Run in Web UI **Settings > Calibration** (Step 6 of wizard).

---

### 4.4 4-Channel LR7843 Logic-Level N-MOSFET Board
- **What It Is:** 4-channel solid-state DC switching module with optocoupled inputs.
- **Why We Use It:** The ESP32-C3 outputs 3.3V at max 20mA, which cannot directly drive 12V DC water pumps (each drawing 1.5A–2.0A). The LR7843 logic-level MOSFET switches high-current 12V DC loads directly from 3.3V logic with zero mechanical wear.
- **Connections:**
  - `Control Inputs (CH1–CH4)` $\rightarrow$ ESP32-C3 GPIO 0, 1, 3, 10.
  - `DC+ / DC- Input` $\rightarrow$ 12V DC power supply.
  - `Load Outputs (OUT1–OUT4)` $\rightarrow$ 12V DC mini submersible pumps 1 through 4.

---

### 4.5 12V Mini Submersible Pumps (Brushless DC)
- **What It Is:** 12V DC centrifugal mini water pumps (rated ~240 L/h, 3m head, 1.5A–2.0A operating).
- **Why We Use It:** Powers precision drip irrigation to individual garden plant beds.
- **Safety Rule in Firmware:** The ESP32-C3 firmware strictly caps pump runs to **60 seconds maximum** and allows a **maximum of 2 concurrent pumps** on the DC bus (capping DC current to 4.0A).

---

### 4.6 1N4007 Flyback Diodes
- **What It Is:** 1000V 1A general-purpose rectifier diodes.
- **Why We Use It:** DC pump motors are inductive coils. When a MOSFET switches OFF, the collapsing magnetic field produces an inductive voltage spike that can puncture the MOSFET junction.
- **How to Connect:** Solder directly across each 12V pump's power terminals in **reverse-bias** (Cathode [striped band] to +12V; Anode to MOSFET drain / pump negative).

---

### 4.7 Polypropylene (PP) Float Switch (Reservoir Dry-Run)
- **What It Is:** Vertical reed float switch mounted inside the garden drip reservoir bucket.
- **Why We Use It:** If the drip reservoir runs out of water, running the micro pumps dry will burn out their impellers.
- **Connection:** Connect between ESP32-C3 GPIO 5 and GND (with internal pull-up enabled in firmware).
- **Failsafe:** If this float opens, the ESP32-C3 hardware firmware immediately cuts power to all 4 MOSFETs within 1 millisecond without waiting for SBC instructions.

---

### 4.8 12V 3A DC Power Adapter
- **What It Is:** Regulated switch-mode AC-DC wall adapter.
- **Why We Use It:** Supplies clean 12V DC power to the garden node MOSFET board and the 4 mini pumps.

---

### 4.9 MP1584EN Buck Converter (Garden Node, to 5V)
- **What It Is:** Miniature step-down converter module.
- **Why We Use It:** Steps down the incoming 12V garden supply to 5.0V to power the ESP32-C3 Super Mini through its `5V` pin.

---

### 4.10 YF-S401 / YF-S201 Pulse Flow Meter
- **What It Is:** In-line Hall-effect turbine flow sensor.
  - **YF-S401:** 0.3–6.0 L/min range (ideal for 4mm micro-drip manifolds; K-factor = 98 Hz per L/min = ~5880 pulses/L).
  - **YF-S201:** 1.0–30.0 L/min range (ideal for 1/2" supply lines; K-factor = 7.5 Hz per L/min = 450 pulses/L).
- **Why We Use It:** Validates that water is physically flowing through Zone 1. If a pump is powered but the flow meter registers fewer than 4 pulses in 10 seconds, the controller trips with a `NO FLOW / AIRLOCK` alarm.
- **Wiring (Voltage Divider Rule):**
  The flow meter runs on 5V and outputs 5V square-wave pulses. **Do NOT connect directly to ESP32-C3 GPIO 4!**
  Use a voltage divider:
  - 10 kΩ resistor between flow pulse wire and ESP32-C3 GPIO 4.
  - 20 kΩ resistor between GPIO 4 and GND.
  *(This steps down 5V pulses to a safe 3.3V logic level).*

---

### 4.11 4 mm Check Valves (Non-Return Valves)
- **What It Is:** One-way silicone duckbill or spring-loaded ball valves.
- **Why We Use It:** Installed in the drip delivery line immediately after each pump. It keeps the tubing primed with water between cycles, eliminating startup airlocks.

---

### 4.12 4 mm Barbed Anti-Siphon Tees
- **What It Is:** 3-way barbed plastic tee with a high-point vacuum breaker pinhole.
- **Why We Use It:** If the drip reservoir is positioned higher than the garden plant beds, turning on the pump can create an accidental gravity siphon that drains the entire reservoir onto the plants even after the pump is turned OFF. The anti-siphon tee breaks the siphon effect instantly.

---

### 4.13 4/7 mm Silicone / PVC Tubing
- **What It Is:** 4 mm internal diameter, 7 mm external diameter flexible drip distribution hose.
- **Why We Use It:** Connects the submersible pumps to barbed tees and emitters. Food-grade silicone remains flexible in winter and does not kink.

---

### 4.14 Drip Stakes / Adjustable Emitters
- **What It Is:** Pressure-compensating or adjustable 0–70 L/h drip stakes pushed into the plant soil.
- **Why We Use It:** Delivers water directly to the plant root zone without splashing foliage or eroding soil.

---

### 4.15 Pump Inlet Strainer / Pre-Filter
- **What It Is:** Fine stainless-steel or nylon mesh pre-filter cap.
- **Why We Use It:** Prevents algae, organic matter, or sand particles in the reservoir from entering and jamming the miniature pump impellers.

---

### 4.16 Garden Enclosure IP65 150x110x70 mm & PG7 Glands
- **What It Is:** Weatherproof plastic project box with silicone sealing gasket and PG7 cable strain reliefs.
- **Why We Use It:** Protects the garden ESP32-C3, ADS1115 ADC, and MOSFET board from rain, sprinkler spray, and morning dew.

---

### 4.17 Pump Outlet Reducers / Adapters
- **What It Is:** Barbed fittings adapting pump discharge nozzles (typically 8mm or 10mm OD) down to standard 4/7mm drip tubing.

---

## 8. Section 5: Optional Extras & Redundancy Components

---

### 5.1 Rooftop Redundancy Node
- **What It Is:** An optional standalone ESP32-C3 node with an SS316 stainless-steel float switch mounted on the rooftop tank rim.
- **Why We Use It:** Provides defense-in-depth against primary float cable cuts or mechanical jamming on high-value properties. If the primary float fails, this node broadcasts an emergency stop packet over ESP-NOW directly to the head-end.

---

### 5.2 BME280 Environmental Sensor (I2C)
- **What It Is:** Precision digital sensor measuring ambient temperature, relative humidity, and barometric pressure.
- **Why We Use It:** Provides local micro-climate data. When combined with the free Open-Meteo weather service, the controller calculates vapor pressure deficit (VPD) and automatically skips irrigation cycles if rain probability > 60% or relative humidity > 90%.
- **Connection (Garden Node ESP32-C3):** Connects to I2C bus (SDA: GPIO 6, SCL: GPIO 7, address `0x76` or `0x77`).

---

### 5.3 0.96-Inch OLED Display (SSD1306, 128x64 I2C)
- **What It Is:** High-contrast monochrome OLED display.
- **Why We Use It:** Mounted on the front of the logic enclosure. Displays tank level %, motor state, IP address, and active alarms without requiring an SSH terminal or phone screen.
- **Connection (ROCK Pi S Header 1):** Connects to I2C bus 7 (SDA: Pin 3, SCL: Pin 5, 3.3V, GND, address `0x3C`).
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
- **What It Is:** Audible beeper generating an 85 dB tone when energized.
- **Why We Use It:** Acoustic backup when the USB audio speaker is muted, disconnected, or during quiet hours for Critical Priority 0 alarms (e.g. pipe burst leak, wiring fault, welded contactor).
- **Connection (ROCK Pi S):** Pin 13 (GPIO1_B6 / Line 46) drives buzzer positive leg through a 330 Ω resistor; negative leg to GND.
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
- **What It Is:** Second ADS1115 ADC module configured for I2C address `0x49`.
- **Why We Use It:** Allows expanding the garden node from 4 soil moisture zones to 8 soil moisture zones.
- **Connection:** Tie the `ADDR` pin of the second ADS1115 to `VDD` (3.3V). Connect SDA and SCL in parallel with the first ADS1115.

---

## 9. Scaling & Expansion Guide: "If I Increase the Items"

### 1. Adding More Irrigation Zones (e.g. from 4 to 8 Zones)
1. **Hardware:**
   - Add a second 4-channel LR7843 MOSFET module.
   - Add a second ADS1115 ADC with its `ADDR` pin tied to `3.3V` (I2C address `0x49`).
   - Connect MOSFET gate inputs to remaining ESP32-C3 GPIOs.
2. **Firmware:**
   - In `firmware/node/include/config.h`, set `#define NUM_ZONES 8`.
3. **Software Configuration:**
   - In `/etc/smart-water/config.yaml`, declare zones 5 through 8:
     ```yaml
     zones:
       zone_5:
         name: "Fruit Trees"
         enabled: true
         soil_dry_threshold_pct: 30.0
         pulse_seconds: 20
     ```

### 2. Sizing Up the Pump Motor (e.g. Upgrading from 1 HP to 3 HP or 5 HP)
1. **Mains Contactor Sizing:** Upgrade contactor from 25A to 40A or 50A (rated for AC-3 inductive duty matching motor nameplate Full Load Current).
2. **Thermal Overload Relay:** Replace the bimetallic overload relay with a model matching the higher motor FLC (e.g. 12A–18A for a 3 HP single-phase motor).
3. **MCB & Cable Gauge:**
   - Up to 2 HP: 16A MCB, 2.5 mm² copper cable.
   - 3 HP: 25A MCB, 4.0 mm² copper cable.
   - 5 HP: 32A MCB, 6.0 mm² copper cable.
4. **Software Timing:** If the tank is larger (e.g. 5,000 Litres), increase `max_runtime_s`:
   ```yaml
   motor:
     max_runtime_s: 1800  # 30 minutes
     cooldown_s: 2400     # 40 minutes
   ```

### 3. Adding Continuous Tank Level Sensing (Ultrasonic or Pressure Transducer)
To view exact litres and tank fill percentage on the live dashboard:
1. **Option A (Ultrasonic JSN-SR04T):** Mount waterproof transducer at top center of tank. Wire Echo to GPIO via voltage divider, Trigger to GPIO.
2. **Option B (4–20 mA Submersible Pressure Transducer):** Drop transducer to bottom of tank. Wire through ADS1115 with a precision 250 Ω shunt resistor (converting 4–20 mA to 1.0V–5.0V).
3. **Configuration:**
   ```yaml
   level_sensor:
     enabled: true
     sensor_type: "pressure_transducer"
     tank_shape: "cylinder"
     tank_height_cm: 200.0
     tank_diameter_cm: 150.0
     predictive_stop_enabled: true
     predictive_stop_pct: 95.0
   ```

---

## 10. End-to-End Master Pinout & Terminal Mapping Table

| Component & Signal | Device / Board | Board Pin / Line | Wire Color | Destination Terminal | Normal Logic State |
| :--- | :--- | :---: | :---: | :--- | :---: |
| **DC System Rail (3.3V)** | ROCK Pi S H1 | Pin 1 | Red | PC817 VCC, DS3231 VCC, OLED VCC | 3.3V DC Continuous |
| **DC Supply Rail (5.0V)** | ROCK Pi S H1 | Pin 2 | Orange | Fused Float Chain Supply, Buzzer | 5.0V DC Fused |
| **I2C7 SDA** | ROCK Pi S H1 | Pin 3 (Line 50) | Blue | DS3231 SDA, SSD1306 OLED SDA | 3.3V Pull-Up |
| **I2C7 SCL** | ROCK Pi S H1 | Pin 5 (Line 51) | Yellow | DS3231 SCL, SSD1306 OLED SCL | 3.3V Pull-Up |
| **Common DC Ground** | ROCK Pi S H1 | Pin 6, 14 | Black | Star Ground Terminal Block | 0V Reference |
| **IN1 (Tank Low Float)** | ROCK Pi S H1 | Pin 7 (Line 42) | White | PC817 Channel 1 Collector | Active LOW (0) |
| **IN2 (Interlock Out)** | ROCK Pi S H1 | Pin 9 (Line 43) | Brown | PC817 Channel 2 Collector | Active LOW (0) |
| **IN3 (Interlock Mid)** | ROCK Pi S H1 | Pin 11 (Line 44) | Gray | PC817 Channel 3 Collector | Active LOW (0) |
| **IN4 (Manifold Leak)** | ROCK Pi S H1 | Pin 12 (Line 45) | Violet | PC817 Channel 4 Collector | Active LOW (0) |
| **Piezo Buzzer Drive** | ROCK Pi S H1 | Pin 13 (Line 46) | Pink | Piezo Buzzer (+) via 330 Ω | Active HIGH (1) |
| **Pilot Relay Drive** | ROCK Pi S H1 | Pin 15 (Line 48) | Green | 5V Pilot Relay IN Terminal | Active HIGH (1) |
| **Contactor Aux FB** | ROCK Pi S H1 | Pin 16 (Line 49) | Blue/Wht | PC817 Aux Feedback Collector | Active LOW (0) |
| **230V Mains Live (In)** | Building Board | Phase Terminal | Red (2.5mm)| DP MCB Terminal 1 | 230V AC Sinusoidal |
| **230V Mains Neutral** | Building Board | Neutral Terminal | Black (2.5mm)| DP MCB Terminal 3 | 0V AC Reference |
| **Mains Earth Ground** | Earth Electrode| Earth Terminal | Green (2.5mm)| Enclosure Earth Stud & SPD Ground | Protective Earth |
| **Contactor Coil L** | Pilot Relay NO | Output Terminal | Red (1.0mm)| Contactor Terminal A1 (with Snubber)| 230V AC when active |
| **Contactor Coil N** | Mains Neutral | Bus Terminal | Black (1.0mm)| Contactor Terminal A2 (with Snubber)| 230V AC Neutral |
| **Garden Pump 1 Gate** | ESP32-C3 Node | GPIO 0 | Yellow | LR7843 MOSFET Gate 1 | 3.3V Active HIGH |
| **Garden Pump 2 Gate** | ESP32-C3 Node | GPIO 1 | Blue | LR7843 MOSFET Gate 2 | 3.3V Active HIGH |
| **Garden Pump 3 Gate** | ESP32-C3 Node | GPIO 3 | Green | LR7843 MOSFET Gate 3 | 3.3V Active HIGH |
| **Garden Pump 4 Gate** | ESP32-C3 Node | GPIO 10 | White | LR7843 MOSFET Gate 4 | 3.3V Active HIGH |
| **Zone 1 Flow Pulse** | ESP32-C3 Node | GPIO 4 | Orange | Voltage Divider Tap (10k/20k) | 0–3.3V Square Wave |
| **Reservoir Dry Float** | ESP32-C3 Node | GPIO 5 | Gray | PP Float Switch (to GND) | Closed to GND = Wet |
| **Garden I2C SDA** | ESP32-C3 Node | GPIO 6 | Blue | ADS1115 SDA, BME280 SDA | 3.3V Pull-Up |
| **Garden I2C SCL** | ESP32-C3 Node | GPIO 7 | Yellow | ADS1115 SCL, BME280 SCL | 3.3V Pull-Up |
