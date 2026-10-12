#pragma once
#include <Arduino.h>

#if !__has_include("secrets.h")
#error "Missing secrets.h or empty keys. Copy secrets.h.example and set ESPNOW_PMK, ESPNOW_LMK and ESPNOW_SHARED_KEY."
#else
#include "secrets.h"
#if !defined(ESPNOW_PMK) || !defined(ESPNOW_LMK) || !defined(ESPNOW_SHARED_KEY)
#error "Missing secrets.h or empty keys. Copy secrets.h.example and set ESPNOW_PMK, ESPNOW_LMK and ESPNOW_SHARED_KEY."
#endif
#endif

static_assert(sizeof(ESPNOW_PMK) > 1, "Missing secrets.h or empty keys. Copy secrets.h.example and set ESPNOW_PMK, ESPNOW_LMK and ESPNOW_SHARED_KEY.");
static_assert(sizeof(ESPNOW_LMK) > 1, "Missing secrets.h or empty keys. Copy secrets.h.example and set ESPNOW_PMK, ESPNOW_LMK and ESPNOW_SHARED_KEY.");
static_assert(sizeof(ESPNOW_SHARED_KEY) > 1, "Missing secrets.h or empty keys. Copy secrets.h.example and set ESPNOW_PMK, ESPNOW_LMK and ESPNOW_SHARED_KEY.");

// ============================================================================
// ESP32-C3 Garden Node Pinout & Timing Configuration (Protocol v1.1)
// Strictly avoids strapping pins (GPIO 2, 8, 9) for high-current loads
// ============================================================================

#define ESPNOW_WIFI_CHANNEL 1

// Gateway MAC Address: Unicast only.
// Must be set to your actual ESP32-C3 Gateway MAC address.
// Broadcast (FF:FF:FF:FF:FF:FF) or all-zeros will cause initialization refusal.
static const uint8_t GATEWAY_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// 4x 12V Submersible Pump MOSFET Gates (Logic-level N-MOSFET, active HIGH, safe default LOW)
#define PIN_PUMP_ZONE_1 0
#define PIN_PUMP_ZONE_2 1
#define PIN_PUMP_ZONE_3 3
#define PIN_PUMP_ZONE_4 10

// Zone 1 Hall-Effect Flow Meter (Pulse counter via 5V->3.3V divider to GPIO 4)
#define PIN_FLOW_METER 4

// Reservoir Float Switch (Reed switch with internal pull-up; closed to GND when wet)
#define PIN_RESERVOIR_FLOAT 5

// I2C Bus for ADS1115 (0x48)
#define I2C_SDA_PIN 6
#define I2C_SCL_PIN 7

// Status LED (ESP32-C3 onboard LED on GPIO 8, active LOW)
#define PIN_STATUS_LED 8

// Hardcoded Firmware Safety Limits
#define MAX_PUMP_HARD_TIMEOUT_S  60 // Never allow any pump to run > 60s continuously
#define MAX_CONCURRENT_PUMPS      2  // Maximum 2 pumps running simultaneously
#define LINK_LOSS_TIMEOUT_MS  60000 // 60s without valid command/ack from gateway -> safe mode
#define HEARTBEAT_INTERVAL_MS  5000
#define SOIL_READ_INTERVAL_MS 10000
#define FLOW_REPORT_INTERVAL_MS 1000
#define FLOAT_DEBOUNCE_MS       100
#define ADS_RETRY_INTERVAL_MS 30000
#define WATCHDOG_TIMEOUT_S       10
