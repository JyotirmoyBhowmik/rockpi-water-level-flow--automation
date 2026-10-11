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

### 2.1 Rock Pi S Pin Header Reference (Header 1 & Header 2)
The Radxa ROCK Pi S exposes 26-pin Header 1 and 26-pin Header 2 (0.1" pitch). All GPIO pins on the RK3308 operate strictly at **3.3V logic levels**. Inputs must NEVER be exposed to 5V or 12V directly.

| Function | Pin Name | Physical Pin | Rockchip GPIO Offset | Signal Description |
| :--- | :--- | :--- | :--- | :--- |
| **3.3V Power** | VCC_3V3 | Pin 1 (H1) | - | 3.3V System Rail (Max 150mA external) |
| **5V Power** | VCC_5V | Pin 2 (H1) | - | 5V DC Supply Rail (Fused) |
| **Ground** | GND | Pin 6 (H1) | - | Common DC Ground |
| **I2C7 SDA** | I2C7_SDA | Pin 3 (H1) | GPIO1_C2 (Line 50) | DS3231 RTC & Optional SSD1306 OLED |
| **I2C7 SCL** | I2C7_SCL | Pin 5 (H1) | GPIO1_C3 (Line 51) | DS3231 RTC & Optional SSD1306 OLED |
| **PC817 IN1** | IN1_GPIO | Header 1 | Configurable in YAML | Tank Low Float (Active LOW) |
| **PC817 IN2** | IN2_GPIO | Header 1 | Configurable in YAML | Interlock Chain Closed (HIGH = Motor OK) |
| **PC817 IN3** | IN3_GPIO | Header 1 | Configurable in YAML | Interlock Mid-Point (Between Full & Sump) |
| **PC817 IN4** | IN4_GPIO | Header 1 | Configurable in YAML | Leak Detection Sensor (Active LOW) |
| **Contactor Out** | RELAY_GPIO | Header 1 | Configurable in YAML | 25A Contactor Driver Output |

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

#### PC817 Channels Configuration:
1. **IN1 (Tank Low Float):** Normally Open (NO) reed float placed near bottom (20% height). Closed when water drops below float level. Reads ACTIVE LOW on Pi GPIO.
2. **IN2 (Interlock Out):** Sourced from the end of the series hardware safety chain. Reads HIGH when entire chain is complete (Fused 5V -> Full Float NC -> Sump Float NO -> Interlock Out).
3. **IN3 (Interlock Mid):** Tap between Full Float and Sump Float. Used by cause logic to isolate whether the Full float opened or the Sump float opened.
4. **IN4 (Leak Sensor):** Conductive grid / optical rain sensor at the pump manifold. Reads ACTIVE LOW when moisture bridges the contacts.

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
- The main AC pump (1.5 HP to 3 HP single-phase) draws up to 18A continuous and 45A inrush.
- **NEVER** drive AC pumps directly with miniature PCB relays.
- A 25A industrial AC contactor (e.g., Schneider TeSys / ABB) with a 230V AC or 12V DC coil is used.
- The Rock Pi S drives an optocoupled relay board, which in turn switches the contactor coil.
- A **10k pull-down resistor** is hardwired at the gate of the relay driver to guarantee the relay stays OFF during Rockchip bootloader initialization.
- An **RC Snubber (100 ohm + 0.1 uF 400V)** is wired across the contactor coil terminals to quench inductive flyback.

---

### 2.5 I2C Bus & Peripheral Devices
- **I2C Bus:** `/dev/i2c-7` on Rock Pi S Header 1 (Pins 3 & 5).
- **DS3231 RTC:** I2C address `0x68`. Powered from 3.3V, includes CR2032 lithium coin cell. Keeps accurate monotonic time during internet outages.
- **SSD1306 OLED (Optional):** I2C address `0x3C`. 128x64 monochrome display for local status.
- **Audio Output:** C-Media CM108 USB audio DAC dongle plugged into Rock Pi S USB Type-A port. Outputs line-out to powered PA speaker for synthesized voice alerts.

---

## 3. Wireless Garden Node Wiring (ESP32-C3 Super Mini)

### 3.1 ESP32-C3 Super Mini Pinout Reference
The ESP32-C3 Super Mini is an ultra-compact RISC-V board operating at 3.3V.

```
                       +-------------------+
             5V (IN) --| 1              16 |-- 3V3 (OUT)
                 GND --| 2              15 |-- GND
 (Flow Meter) GPIO 2 --| 3              14 |-- GPIO 20 (Pump 4 Gate)
(Res. Float) GPIO 3 --| 4              13 |-- GPIO 10 (Pump 3 Gate)
   (I2C SDA) GPIO 4 --| 5              12 |-- GPIO 7  (Pump 2 Gate)
   (I2C SCL) GPIO 5 --| 6              11 |-- GPIO 6  (Pump 1 Gate)
   (ADC A0)  GPIO 0 --| 7              10 |-- GPIO 9  (BOOT)
   (ADC A1)  GPIO 1 --| 8               9 |-- GPIO 8  (Onboard LED)
                       +-------------------+
```

---

### 3.2 ADS1115 16-Bit ADC Wiring (0x48)
To avoid noisy ESP32 SAR ADC nonlinearities, all capacitive soil moisture sensors are measured via an ADS1115 16-bit I2C ADC.

```
ESP32-C3 Super Mini                    ADS1115 (0x48)
-------------------                    --------------
3V3 ---------------------------------> VDD
GND ---------------------------------> GND
GPIO 4 (SDA) ------------------------> SDA
GPIO 5 (SCL) ------------------------> SCL
GND ---------------------------------> ADDR (Configures I2C Address 0x48)

Capacitive Soil Probes:
Probe 1 Analog Out ------------------> A0 (Zone 1: Vegetables)
Probe 2 Analog Out ------------------> A1 (Zone 2: Herbs)
Probe 3 Analog Out ------------------> A2 (Zone 3: Perennials)
Probe 4 Analog Out ------------------> A3 (Zone 4: Shrubs)
Probe VCC (All) ---------------------> 3V3
Probe GND (All) ---------------------> GND
```

---

### 3.3 12V Drip Pump MOSFET Drivers (Zones 1-4)
Four logic-level N-Channel MOSFETs (e.g., IRLZ44N, AO3400) switch the low side of 12V 2A submersible DC pumps.

```
       +12V DC Battery / Solar Bus
              |
              +--------------------------+
              |                          |
          [ Pump + ]                  [ Flyback Diode 1N5819 ]
          [ 12V DC ]                  (Cathode to +12V, Anode to Drain)
              |                          |
              +-------------+------------+
                            |
                         Drain
ESP32-C3 GPIO (6,7,10,20) --- Gate [ 100 Ohm Gate Resistor ]
                            |
                         Source
                            |
                   Common 12V/GND Rail

* Note: 10k Pull-down resistor hardwired from Gate to GND on every MOSFET.
```

---

### 3.4 Zone 1 Flow Meter & Reservoir Float
1. **Hall-Effect Pulse Flow Meter (YF-S201 or similar):**
   - VCC -> 5V (or 3.3V)
   - GND -> GND
   - Signal -> **GPIO 2** (Configured as `INPUT_PULLUP`, interrupt on falling edge).
2. **Reservoir Reed Float Switch:**
   - One lead -> **GPIO 3** (Internal pull-up enabled).
   - Second lead -> **GND**.
   - Closed to GND when water is present (`reservoir_wet = true`).

---

## 4. Wire Gauge & Installation Guidelines

| Circuit | Operating Voltage | Peak Current | Recommended Wire Gauge | Max Cable Run |
| :--- | :--- | :--- | :--- | :--- |
| **Main AC Pump Feed** | 230V AC | 25A | 4.0 mm² (12 AWG Copper) | 30 meters |
| **Contactor Coil** | 230V AC / 12V DC | 0.2A | 0.75 mm² (18 AWG) | 5 meters |
| **Opto Float Switches** | 5V DC | 20 mA | 0.5 mm² Shielded Twisted Pair | 50 meters |
| **12V DC Drip Pumps** | 12V DC | 2.5A per zone | 1.5 mm² (16 AWG) | 10 meters |
| **I2C Bus (RTC/ADC)** | 3.3V DC | <5 mA | Ribbon Cable / 0.2 mm² | 30 cm |
