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
// ESP32-C3 Garden Gateway Configuration
// Translates USB Serial JSON Lines <-> ESP-NOW Wireless Packets
// ============================================================================

#define SERIAL_BAUD_RATE 115200
#define ESPNOW_WIFI_CHANNEL 1

// Gateway Heartbeat Interval
#define GW_HEARTBEAT_INTERVAL_MS 10000

// Target Node MAC Address: Unicast only.
// Must be set to your actual ESP32-C3 Garden Node MAC address.
// Broadcast (FF:FF:FF:FF:FF:FF) or all-zeros will cause initialization refusal.
static const uint8_t TARGET_NODE_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
