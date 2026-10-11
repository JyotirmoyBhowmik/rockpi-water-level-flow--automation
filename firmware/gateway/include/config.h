#pragma once
#include <Arduino.h>

// ============================================================================
// ESP32-C3 Garden Gateway Configuration
// Translates USB Serial JSON Lines <-> ESP-NOW Wireless Packets
// ============================================================================

#define SERIAL_BAUD_RATE 115200
#define ESPNOW_WIFI_CHANNEL 1

// Gateway Heartbeat Interval
#define GW_HEARTBEAT_INTERVAL_MS 10000

// Target Node MAC Address: Default broadcast (FF:FF:FF:FF:FF:FF)
// Replace with the specific node MAC for unicast pairing if desired.
static const uint8_t TARGET_NODE_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
