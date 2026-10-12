# Industrial Hardware Wiring & Electrical Specification

## 1. Architectural Overview & Isolation Boundaries

The Smart Water & Precision Irrigation Controller separates the control plane into two galvanically isolated physical domains:
1. **Head-End (Hardwired, AC Power Domain):** Radxa ROCK Pi S SBC interfacing with 230V AC pump contactor, optical isolation board (PC817), hardware interlock chain, DS3231 RTC, and USB audio.
2. **Garden Node (Wireless ESP-NOW, Low-Voltage DC Domain):** ESP32-C3 Super Mini driving 4x 12V submersible drip irrigation pumps, 4x capacitive soil probes via ADS1115 ADC, reservoir float, and pulse flow meter.

```
       +-------------------------------------------------------------+
       |                  GALVANIC ISOLATION BARRIER                 |
       |                                                             |
+-------------------+      PC817 Optocouplers      +--------------------+
| High-Voltage /    | <--------------------------> | Radxa ROCK Pi S    |
| Float Chain (5V)  |     (5000Vrms Isolation)     | 3.3V Logic Domain  |
+-------------------+                              +---------+----------+
                                                             | USB Serial (CDC)
                                                   +---------v----------+
                                                   | ESP32-C3 Gateway   |
                                                   +---------+----------+
                                                             | ESP-NOW (2.4GHz)
                                                   +---------v----------+
                                                   | ESP32-C3 Node      |
                                                   | 12V DC Pumps + ADC |
                                                   +--------------------+
```

---

## 2. Head-End Wiring (Radxa ROCK Pi S)

### 2.1 Radxa ROCK Pi S Pin Header Reference (Verify with `gpioinfo /dev/gpiochip1`)
The Radxa ROCK Pi S exposes 26-pin Header 1 and 26-pin Header 2 (0.1" pitch). All GPIO pins on the RK3308 operate strictly at **3.3V logic levels**. Inputs must NEVER be exposed to 5V or 12V directly. Pin numbers and line offsets can shift between kernel device-tree overlays and Armbian releases; always verify active line numbers with `gpioinfo /dev/gpiochip1` and set them in `/etc/smart-water/config.yaml` or during the Web Commissioning Wizard Step 2.

| Function / Signal | Physical Pin (H1) | Kernel Pin Name | Default Logic Level | Connected Peripheral / Circuit |
| :--- | :---: | :--- | :--- | :--- |
| **3.3V System Rail** | Pin 1 | VCC_3V3 | 3.3V DC Rail | PC817 Collector Pull-up Rail (Max 150mA external) |
| **5V Supply Rail** | Pin 2 | VCC_5V | 5.0V DC Rail | 5V DC Supply Rail (Fused 0.5A) |
| **I2C7 SDA** | Pin 3 | GPIO1_C2 (Line 50) | 3.3V DC (Pull-up) | DS3231 RTC SDA & Optional SSD1306 OLED |
| **I2C7 SCL** | Pin 5 | GPIO1_C3 (Line 51) | 3.3V DC (Pull-up) | DS3231 RTC SCL & Optional SSD1306 OLED |
| **Common Ground** | Pin 6, 14 | GND | 0V | Common DC System Ground |
| **IN1 (Tank Low)** | Pin 7 | GPIO1_B2 (Line 42) | Configurable (Active LOW default) | PC817 Opto Channel 1 (Overhead Tank Low Float) |
| **IN2 (Interlock Out)**| Pin 9 | GPIO1_B3 (Line 43) | Configurable (Active LOW default) | PC817 Opto Channel 2 (End of Float Safety Chain) |
| **IN3 (Interlock Mid)**| Pin 11 | GPIO1_B4 (Line 44) | Configurable (Active LOW default) | PC817 Opto Channel 3 (Mid-Point Tap between Floats) |
| **IN4 (Leak Sensor)** | Pin 12 | GPIO1_B5 (Line 45) | Configurable (Active LOW default) | PC817 Opto Channel 4 (Manifold Conductive Leak Sensor) |
| **Pilot Relay Out** | Pin 15 | GPIO1_C0 (Line 48) | Active HIGH (unless pull-up confirmed) | Pilot Relay IN (Switches 230V/12V Contactor Coil) |
| **Contactor Aux In** | Pin 16 | GPIO1_C1 (Line 49) | Configurable (Active LOW default) | Contactor NO Auxiliary Contact (via PC817) |

---

### 2.2 Optocoupled Inputs (PC817 4-Channel Board)
To protect the RK3308 against inductive spikes, voltage surges, and ground loops from long outdoor float wiring, a 4-channel PC817 optoisolator module is placed between the floats and the SBC.

```
External 5V Float Domain                   Rock Pi S 3.3V Logic Domain
------------------------                   ---------------------------
+5V Fused ---+
             |
          [ Float / Sensor ]
             |
             +----[ 1k Resistor ]----+
                                     |
                                 +---+---+
                                 | PC817 |
                                 | Opto  |---- Collector ----> Rock Pi S GPIO Pin
                                 +---+---+                     (Internal Pull-up 50k)
                                     |
Ground (Float Domain) ---------------+-------- Emitter   ----> Rock Pi S GND
```

#### PC817 Channels & Polarity Configuration:
With PC817 open-collector outputs and pull-ups to 3.3V, energizing the optocoupler LED pulls the GPIO pin LOW (0). By default, all inputs use `active_low: true`, but each input has configurable polarity:
1. **IN1 (Tank Low Float):** Normally Open (NO) reed float placed near bottom (20% height). Closed when water drops below float level (`tank_low_active_low: true` by default).
2. **IN2 (Interlock Out):** Sourced from the end of the series hardware safety chain (`interlock_out_active_low: true` by default, energized chain pulls GPIO LOW).
3. **IN3 (Interlock Mid):** Tap between Full Float and Sump Float (`interlock_mid_active_low: true` by default). Used by cause logic to isolate whether the Full float opened or the Sump float opened.
4. **IN4 (Leak Sensor):** Conductive grid / optical rain sensor at the pump manifold (`leak_active_low: true` by default).
5. **Contactor Aux Feedback:** Auxiliary contact on contactor (`feedback_active_low: true` by default).

---

### 2.3 Hardware Interlock Safety Chain (Physical Layer 1)
The primary safety barrier does NOT rely on software or microcontrollers. Fused 5V power feeds directly through passive electromechanical float switches before energizing the contactor coil driver.

```
       +5V Fused (0.5A)
              |
       +------v------+
       |  Full Float | (NC - Normally Closed; Opens when tank reaches 100% full
       |   (Top Tank)|  or if sensor wire is severed)
       +------+------+
              |
              +-----------------------------> Tap to PC817 IN3 (interlock_mid)
              |
       +------v------+
       |  Sump Float | (NO - Normally Open; Held CLOSED by water buoyancy in sump;
       | (Ground/Well)| Opens when sump runs dry)
       +------+------+
              |
              +-----------------------------> Tap to PC817 IN2 (interlock_out)
              |
              v
       Relay Module VCC / Enable Pin
```

#### Electrical Truth Table & Cause Analysis:
| `interlock_out` | `interlock_mid` | Hardware Condition | Motor Action | Software State |
| :---: | :---: | :--- | :--- | :--- |
| **HIGH (1)** | **HIGH (1)** | Chain intact, sump wet, tank not full | Allowed to Run | `healthy` |
| **LOW (0)** | **LOW (0)** | Full Float OPEN (Tank full or wire cut) | Hardware Cutoff | `tank_full` |
| **LOW (0)** | **HIGH (1)** | Sump Float OPEN (Sump dry / dry-run) | Hardware Cutoff | `dry_sump` |
| **HIGH (1)** | **LOW (0)** | **IMPOSSIBLE STATE** (Short circuit / miswire) | **FORCED LOCK** | `wiring_fault` |

---

### 2.4 AC Contactor (25A) Driver Circuit
- **Motor Sizing & Overload Protection:** Electrician sizes MCB, cable, and thermal overload relay from the motor nameplate Full Load Current (FLC). Single-phase submersible pumps (1.5 HP to 3 HP) typically draw up to 18A continuous and 45A inrush.
- **Contactor Specification:** A 25A industrial AC contactor (e.g., Schneider TeSys LC1D / ABB AF series) rated for AC-3 inductive motor duty with a 230V AC or 12V DC coil is used. **NEVER** drive AC pumps directly with miniature PCB relays.
- **Relay Driver:** The Rock Pi S drives an optocoupled pilot relay board, which in turn switches the contactor coil.
- A **10k pull-down resistor** is hardwired at the gate of the relay driver to guarantee the relay stays OFF during Rockchip bootloader initialization.
- An **RC Snubber (100 ohm + 0.1 uF 400V)** is wired across the contactor coil terminals to quench inductive flyback.

---

### 2.5 I2C Bus & Peripheral Devices
- **I2C Bus:** `/dev/i2c-7` on Rock Pi S Header 1 (Pins 3 & 5).
- **DS3231 RTC:** I2C address `0x68`. Powered from 3.3V, includes CR2032 lithium coin cell. Keeps accurate monotonic time during internet outages.
- **SSD1306 OLED (Optional):** I2C address `0x3C`. 128x64 monochrome display for local status.
- **Audio Output:** C-Media CM108 USB audio DAC dongle plugged into Rock Pi S USB Type-A port. Outputs line-out to powered PA speaker for synthesized voice alerts.

---

### 2.6 Contactor Auxiliary Feedback Contact Wiring (Part 2.8)
Industrial contactors (such as Schneider TeSys LC1D or ABB AF series) feature a mechanical Normally Open (NO) Auxiliary Contact block (terminals 13 & 14) mechanically tied to the main power contacts.
- **Pull-In Verification:** When the Rock Pi S energizes the relay, the auxiliary contacts must close within 1.0 second. Failure to close indicates coil supply failure or mechanical contact jam (`CONTACTOR_MISMATCH`).
- **Welded Contact Detection:** Before energizing the relay, the software checks that the aux contact is currently OPEN. If closed while the relay output is de-energized, contacts are welded shut (`WELDED_CONTACT`), instantly locking out the motor and tripping an alarm.

```
Rock Pi S 3.3V Rail ---+
                       |
               [ 10k Pull-Up ]
                       |
Rock Pi S Feedback GPIO +----[ 1k Resistor ]----+
                                                |
                                      [ Contactor Aux NO ]
                                      (Terminals 13 & 14)
                                                |
Rock Pi S GND ----------------------------------+
```

---

### 2.7 Motor Power & AC Current Monitoring (PZEM-004T v3 & SCT-013 CT)
> [!IMPORTANT]
> **SAFETY NOTICE:** Software AC current monitoring provides supplementary automated dry-run (loss-of-prime) and locked-rotor protection. It **NEVER replaces the physical bimetallic/electronic thermal overload relay or MPCB** required by electrical wiring codes.

#### Option A: PZEM-004T v3 (Modbus RTU over USB-TTL)
- **Interface:** USB-TTL UART adapter connected to ROCK Pi S USB port (udev symlink `/dev/pzem`, 9600 baud, 8N1).
- **Wiring:**
  - `5V` -> USB-TTL 5V
  - `GND` -> USB-TTL GND
  - `RX` -> USB-TTL TX
  - `TX` -> USB-TTL RX
  - **High-Voltage Side:** Live & Neutral terminals connected to AC mains input (230V AC); motor supply line wire passes through the onboard Current Transformer (CT) coil.
- **Capabilities:** Measures RMS Voltage (0.1V), Current (0.001A), Active Power (0.1W), Cumulative Energy (1Wh -> kWh), Frequency (0.1Hz), and Power Factor (0.01).

#### Option B: SCT-013 Current Transformer + ADS1115 ADC
- **Interface:** SCT-013-030 (30A/1V) split-core CT clamped around the motor Live wire.
- **Circuit:** Burden output connected to ADS1115 ADC channel (A0) on I2C bus 7 (`/dev/i2c-7`, address `0x48`) with a DC bias divider (3.3V / 2) to center the AC waveform within ADC input bounds.

---

### 2.8 Continuous Overhead Tank Level Sensing (JSN-SR04T & 4-20mA Transducer)
> [!NOTE]
> **AUTHORITATIVE SAFETY FLOATS:** Continuous level sensing provides percentage/litres telemetry, filling rate estimation, predictive motor stops, and night leak alarms. The series electromechanical float switches (Full float NC, Sump float NO) remain the authoritative, non-bypassable hardware interlock layer.

#### Option A: JSN-SR04T Waterproof Ultrasonic Distance Sensor
- **Mounting:** Mounted vertically pointing down from the top inspection port of the overhead water tank.
- **Sensor Offset:** A minimum offset distance (default 20cm) accommodates the ultrasonic transducer blind zone (20-25cm).
- **Interface Modes:**
  - **Serial Mode (Recommended):** Transmits distance at 9600 baud (`0xFF, H_DATA, L_DATA, CHECKSUM`) to SBC UART.
  - **GPIO Pulse Mode:** Trigger pin connected to SBC GPIO; Echo pin connected through a 5V-to-3.3V voltage divider (1k / 2k resistor) or PC817 optoisolator channel.

#### Option B: 4-20 mA Submersible Hydrostatic Pressure Transducer
- **Mounting:** Submersible stainless steel probe lowered to the bottom of the overhead tank.
- **Circuit:** 2-wire 12V/24V loop connected through a precision 250 ohm ($\pm 0.1\%$) shunt resistor to ADS1115 analog input (Channel A1) on I2C bus 7 (`/dev/i2c-7`, address `0x48`).
- **Transfer Function:**
  - $4\text{ mA} \to 1.0\text{V}$ (zero water head / empty)
  - $20\text{ mA} \to 5.0\text{V}$ (full depth range, e.g. 200cm)

---

## 3. Wireless Garden Node Wiring (ESP32-C3 Super Mini)

### 3.1 ESP32-C3 Super Mini Pinout Reference
The ESP32-C3 Super Mini is an ultra-compact RISC-V board operating strictly at **3.3V logic levels**.

```
                       +-------------------+
             5V (IN) --| 1              16 |-- 3V3 (OUT)
                 GND --| 2              15 |-- GND
 (Pump 1 Gate) GPIO 0 --| 3              14 |-- GPIO 10 (Pump 4 Gate)
 (Pump 2 Gate) GPIO 1 --| 4              13 |-- GPIO 9  (BOOT)
 (Pump 3 Gate) GPIO 3 --| 5              12 |-- GPIO 8  (Status LED)
 (Flow Divider)GPIO 4 --| 6              11 |-- GPIO 7  (I2C SCL)
(Res. Float)  GPIO 5 --| 7              10 |-- GPIO 6  (I2C SDA)
             NC      --| 8               9 |-- NC
                       +-------------------+
```

---

### 3.2 ADS1115 16-Bit ADC Wiring (0x48)
To eliminate ESP32 internal SAR ADC nonlinearities and temperature drift, all capacitive soil moisture sensors are digitized via an external ADS1115 16-bit I2C ADC.

```
ESP32-C3 Super Mini                    ADS1115 (Address 0x48)
-------------------                    ----------------------
3V3 ---------------------------------> VDD
GND ---------------------------------> GND
GPIO 6 (SDA) ------------------------> SDA (4.7k pull-up to 3.3V)
GPIO 7 (SCL) ------------------------> SCL (4.7k pull-up to 3.3V)
GND ---------------------------------> ADDR (Configures I2C Address 0x48)

Capacitive Soil Probes:
Probe 1 Analog Out ------------------> A0 (Zone 1: Vegetables)
Probe 2 Analog Out ------------------> A1 (Zone 2: Herbs)
Probe 3 Analog Out ------------------> A2 (Zone 3: Perennials)
Probe 4 Analog Out ------------------> A3 (Zone 4: Shrubs)
Probe VCC (All 4 Probes) ------------> 3V3
Probe GND (All 4 Probes) ------------> GND
```

---

### 3.3 12V Drip Pump MOSFET Drivers (Zones 1-4)
Four logic-level N-Channel MOSFETs (e.g., IRLZ44N, AO3400, or LR7843) switch the low side of 12V DC micro submersible drip pumps (1.5A - 2.0A continuous operating current, 2.5A peak inrush per pump).
The DC power rail / battery bank must be sized for at least 5.0A DC: the ESP32-C3 firmware strictly caps concurrent pump actuation to a maximum of 2 active pumps at any moment.

```
       +12V DC Battery / Solar Bus
              |
              +--------------------------+
              |                          |
          [ Pump + ]                  [ Flyback Diode 1N5819 / SS34 ]
          [ 12V DC ]                  (Cathode to +12V, Anode to Drain)
              |                          |
              +-------------+------------+
                            |
                         Drain
ESP32-C3 GPIO (0,1,3,10) --- Gate [ 100 Ohm Gate Resistor ]
                            |
                         Source
                            |
                   Common 12V/GND Rail

* Note: 10k Pull-down resistor hardwired from Gate to GND on every MOSFET module.
```

---

### 3.4 Zone 1 Hall-Effect Flow Meter & 5V-to-3.3V Voltage Divider
Zone 1 flow monitoring supports configurable flow meter models (default `flow_meter_model: "YF-S201"` with calibration factor `flow_k_factor: 7.5`, equivalent to 450 pulses/L).
The YF-S201 Hall-effect flow meter requires a 5V supply to operate reliably. However, its pulse output emits 5V square waves, which **will destroy** the ESP32-C3 GPIO pin if connected directly. A precision resistive voltage divider scales the 5V pulse to 3.33V:

```
YF-S201 Flow Meter (5V)                   ESP32-C3 Garden Node (3.3V Logic)
-----------------------                   ---------------------------------
+5V Power (Red Wire) --------------------> +5V DC Supply Rail
GND (Black Wire)     --------------------> Common DC Ground Rail
                                                    
Pulse Signal (Yellow) ----[ 10k Resistor ]----+-----> GPIO 4 (Pulse Input)
(0V - 5V pulses)                              |
                                      [ 20k Resistor ]
                                              |
Common Ground --------------------------------+-----> Common GND Rail

Theoretical Output Voltage:
V_out = 5.0V * (20k / (10k + 20k)) = 5.0V * (2/3) = 3.33V (Within ESP32-C3 3.6V max limit)
```

---

### 3.5 Reservoir Reed Float Switch
- Float Lead 1 -> **GPIO 5** (Internal pull-up enabled).
- Float Lead 2 -> **GND**.
- Switch is closed when water level is above low threshold (`reservoir_wet = true`). When reservoir runs dry, contacts open and all zone pumps lock out immediately.

---

## 4. Beginner Step-by-Step Assembly & Wiring Guide

Follow these stages in order to guarantee safety and prevent hardware damage:

### Stage 1: Power Supplies & Common Ground
1. Mount 5V 3A DC power supply (for Rock Pi S & USB Hub) and 12V DC power supply (for drip pumps).
2. Establish a single-point Star Ground between 5V DC ground and 12V DC ground.
3. Verify with a digital multimeter (DMM) that 5V and 3.3V rails are within +/-2% before connecting any microcontrollers.

### Stage 2: Head-End Float & Interlock Chain Wiring
1. Mount the 4-channel PC817 optocoupler board near the Rock Pi S.
2. Wire the Full Float (NC, top of overhead tank) in series with the Sump Float (NO, bottom of ground reservoir).
3. Connect the 5V interlock feed through the series chain to the relay module VCC/Enable input.
4. Tap the mid-point (between Full and Sump floats) into PC817 IN3 (`interlock_mid`).
5. Tap the end of the chain into PC817 IN2 (`interlock_out`).
6. Connect the Tank Low float (NO) to PC817 IN1 (`tank_low`).
7. Connect the manifold leak sensor to PC817 IN4 (`leak_detected`).
8. Connect the isolated collectors of PC817 IN1-IN4 to Rock Pi S GPIO pins with internal pull-ups enabled.

### Stage 3: AC Contactor & Auxiliary Feedback
1. Wire the 1-channel optocoupled relay output to switch the AC contactor coil (230V AC or 12V DC).
2. Install an RC snubber (100 ohm + 0.1 uF) across the contactor coil terminals.
3. Wire the contactor NO auxiliary contacts (terminals 13 & 14) to the Rock Pi S Aux Feedback GPIO with a 10k pull-up resistor to 3.3V.
4. **DO NOT connect mains power to the contactor main terminals yet.** Perform the dry relay test wizard first.

### Stage 4: Wireless Garden Node (ESP32-C3)
1. Mount the ESP32-C3 Super Mini and ADS1115 ADC on a protoboard or DIN-rail carrier.
2. Wire I2C bus: ESP32 GPIO 6 (SDA) -> ADS1115 SDA, GPIO 7 (SCL) -> ADS1115 SCL. Add 4.7k pull-up resistors to 3.3V.
3. Wire the 4 capacitive soil probes to ADS1115 channels A0, A1, A2, and A3. Power probes strictly from 3.3V.
4. Wire reservoir reed float switch to ESP32 GPIO 5 and GND.

### Stage 5: MOSFET Pump Drivers & Flow Divider
1. Connect the 4 MOSFET driver gates to ESP32 GPIO 0 (Zone 1), GPIO 1 (Zone 2), GPIO 3 (Zone 3), and GPIO 10 (Zone 4).
2. Ensure each gate has a 10k pull-down resistor to GND so pumps stay OFF during ESP32 boot.
3. Wire flyback diodes (1N5819 / SS34) across every 12V pump terminal.
4. Install the 10k / 20k resistor divider on the Zone 1 flow meter signal wire before connecting to ESP32 GPIO 4.

---

## 5. Wire Gauge & Installation Guidelines

| Circuit | Operating Voltage | Peak Current | Recommended Wire Gauge | Max Cable Run |
| :--- | :--- | :--- | :--- | :--- |
| **Main AC Pump Feed** | 230V AC | Sized per motor FLC (typ. 18A-25A) | 4.0 mm² (12 AWG Copper) — Electrician sizes MCB, cable, and overload from nameplate FLC | 30 meters |
| **Contactor Coil** | 230V AC / 12V DC | 0.2A | 0.75 mm² (18 AWG) | 5 meters |
| **Contactor Aux Feedback** | 3.3V DC | <1 mA | 0.5 mm² (20 AWG) | 2 meters |
| **Opto Float Switches** | 5V DC | 20 mA | 0.5 mm² Shielded Twisted Pair | 50 meters |
| **12V DC Drip Pumps** | 12V DC | 2.5A per zone | 1.5 mm² (16 AWG) | 10 meters |
| **Flow Meter Signal** | 5V DC / 3.3V DC | <10 mA | 0.5 mm² Shielded 3-Core | 5 meters |
| **I2C Bus (RTC/ADC)** | 3.3V DC | <5 mA | Ribbon Cable / 0.2 mm² | 30 cm |

