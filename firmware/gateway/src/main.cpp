/**
 * ESP32-C3 Super Mini - USB Serial to ESP-NOW Garden Gateway
 * Speaks JSON Lines v1 over USB CDC Serial (115200 baud).
 * Bridges commands to Wireless Garden Node via ESP-NOW.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <ArduinoJson.h>
#include "config.h"

// Buffer for serial line reading
static char serial_rx_buf[512];
static size_t serial_rx_idx = 0;

static unsigned long last_gw_hb_ms = 0;
static esp_now_peer_info_t peer_info;

// Onboard LED on ESP32-C3 Super Mini (GPIO 8, active low)
#define LED_PIN 8

void blink_led(int ms = 20) {
    digitalWrite(LED_PIN, LOW);
    delay(ms);
    digitalWrite(LED_PIN, HIGH);
}

// Callback invoked when an ESP-NOW packet is received from the garden node
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
void on_esp_now_recv(const esp_now_recv_info_t *recv_info, const uint8_t *data, int len) {
    int8_t rssi = recv_info->rx_ctrl->rssi;
#else
void on_esp_now_recv(const uint8_t *mac_addr, const uint8_t *data, int len) {
    int8_t rssi = -60; // Approximate fallback if IDF < 5
#endif
    if (len <= 0 || len >= 512) {
        return;
    }

    // Null-terminate the incoming payload
    char payload_str[516];
    memcpy(payload_str, data, len);
    payload_str[len] = '\0';

    // Parse to ensure valid JSON and inject RSSI if heartbeat
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload_str);
    if (!err) {
        if (doc["t"] == "hb" && !doc.containsKey("rssi")) {
            doc["rssi"] = rssi;
        }
        serializeJson(doc, Serial);
        Serial.println();
    } else {
        // Fallback: print raw line if JSON parse fails
        Serial.println(payload_str);
    }

    blink_led(10);
}

// Callback invoked when ESP-NOW packet send completes
void on_esp_now_send(const uint8_t *mac_addr, esp_now_send_status_t status) {
    // Optional delivery logging or retry trigger
}

void setup() {
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, HIGH); // Off initially

    Serial.begin(SERIAL_BAUD_RATE);
    unsigned long wait_start = millis();
    while (!Serial && (millis() - wait_start < 2000)) {
        delay(10);
    }

    // Initialize Wi-Fi in Station mode
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    // Set Wi-Fi channel
    esp_wifi_set_promiscuous(true, NULL);
    esp_wifi_set_channel(ESPNOW_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false, NULL);

    // Initialize ESP-NOW
    if (esp_now_init() != ESP_OK) {
        Serial.println("{\"v\":1,\"err\":\"esp_now_init_failed\"}");
        return;
    }

    esp_now_register_recv_cb(on_esp_now_recv);
    esp_now_register_send_cb(on_esp_now_send);

    // Add target peer (broadcast or unicast)
    memset(&peer_info, 0, sizeof(peer_info));
    memcpy(peer_info.peer_addr, TARGET_NODE_MAC, 6);
    peer_info.channel = ESPNOW_WIFI_CHANNEL;
    peer_info.encrypt = false;

    if (esp_now_add_peer(&peer_info) != ESP_OK) {
        Serial.println("{\"v\":1,\"err\":\"add_peer_failed\"}");
    }

    // Initial gateway announce
    Serial.print("{\"v\":1,\"t\":\"gw_init\",\"channel\":");
    Serial.print(ESPNOW_WIFI_CHANNEL);
    Serial.println("}");

    blink_led(100);
}

void loop() {
    unsigned long now = millis();

    // 1. Emit Gateway Heartbeat every 10 seconds
    if (now - last_gw_hb_ms >= GW_HEARTBEAT_INTERVAL_MS) {
        last_gw_hb_ms = now;
        JsonDocument hb_doc;
        hb_doc["v"] = 1;
        hb_doc["t"] = "gw_hb";
        hb_doc["uptime_s"] = now / 1000;
        serializeJson(hb_doc, Serial);
        Serial.println();
    }

    // 2. Read JSON Lines commands from Host USB Serial (ROCK Pi S)
    while (Serial.available() > 0) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (serial_rx_idx > 0) {
                serial_rx_buf[serial_rx_idx] = '\0';

                // Validate incoming JSON
                JsonDocument cmd_doc;
                DeserializationError err = deserializeJson(cmd_doc, serial_rx_buf);
                if (!err && cmd_doc["v"] == 1) {
                    // Send packet to wireless node over ESP-NOW
                    esp_err_t result = esp_now_send(
                        TARGET_NODE_MAC,
                        (const uint8_t *)serial_rx_buf,
                        serial_rx_idx
                    );

                    if (result == ESP_OK) {
                        blink_led(15);
                    } else {
                        Serial.print("{\"v\":1,\"err\":\"esp_now_send_error\",\"code\":");
                        Serial.print((int)result);
                        Serial.println("}");
                    }
                } else {
                    Serial.println("{\"v\":1,\"err\":\"invalid_json_command\"}");
                }

                serial_rx_idx = 0;
            }
        } else {
            if (serial_rx_idx < sizeof(serial_rx_buf) - 1) {
                serial_rx_buf[serial_rx_idx++] = c;
            } else {
                // Buffer overflow protection: reset index
                serial_rx_idx = 0;
            }
        }
    }
}
