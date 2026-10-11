#pragma once
#include <Arduino.h>

// ============================================================================
// ESP32-C3 Garden Node Pinout & Timing Configuration
// ============================================================================

#define ESPNOW_WIFI_CHANNEL 1

// Gateway MAC Address: Default broadcast (FF:FF:FF:FF:FF:FF)
static const uint8_t GATEWAY_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// I2C Bus for ADS1115 (0x48)
#define I2C_SDA_PIN 4
#define I2C_SCL_PIN 5

// 4x 12V Submersible Pump MOSFET Gates (Logic-level N-Channel, safe default LOW)
#define PIN_PUMP_ZONE_1 6
#define PIN_PUMP_ZONE_2 7
#define PIN_PUMP_ZONE_3 10
#define PIN_PUMP_ZONE_4 20

// Reservoir Float Switch (Reed switch with internal pull-up; closed to GND when wet)
#define PIN_RESERVOIR_FLOAT 3

// Zone 1 Hall-Effect Flow Meter (Pulse counter via hardware interrupt)
#define PIN_FLOW_METER 2

// Status LED (ESP32-C3 Super Mini onboard LED on GPIO 8, active low)
#define PIN_STATUS_LED 8

// Hardcoded Firmware Safety Limits
#define MAX_PUMP_HARD_TIMEOUT_S 60 // Never allow any pump to run > 60s continuously
#define HEARTBEAT_INTERVAL_MS   5000
#define SOIL_READ_INTERVAL_MS    10000
#define FLOW_REPORT_INTERVAL_MS  5000
#define FLOAT_DEBOUNCE_MS        100
