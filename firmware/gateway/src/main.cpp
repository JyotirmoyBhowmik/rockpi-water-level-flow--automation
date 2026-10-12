/**
 * ESP32-C3 Super Mini - USB Serial to ESP-NOW Garden Gateway (Protocol v1.1)
 * Speaks JSON Lines over USB CDC Serial (115200 baud).
 * Bridges commands to Wireless Garden Node via Encrypted Unicast ESP-NOW.
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

static inline bool is_broadcast_mac(const uint8_t mac[6]) {
    for (int i = 0; i < 6; i++) {
        if (mac[i] != 0xFF) return false;
    }
    return true;
}

static inline bool is_zero_mac(const uint8_t mac[6]) {
    for (int i = 0; i < 6; i++) {
        if (mac[i] != 0x00) return false;
    }
    return true;
}

static void load_16byte_key(const char *key_str, uint8_t out[16]) {
    if (!key_str) return;
    size_t len = strlen(key_str);
    if (len == 32) {
        for (int i = 0; i < 16; i++) {
            char byte_buf[3] = { key_str[i * 2], key_str[i * 2 + 1], '\0' };
            out[i] = (uint8_t)strtol(byte_buf, NULL, 16);
        }
    } else {
        size_t n = len < 16 ? len : 16;
        memcpy(out, key_str, n);
    }
}

// Callback invoked when an ESP-NOW packet is received from the garden node
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
void on_esp_now_recv(const esp_now_recv_info_t *recv_info, const uint8_t *data, int len) {
    const uint8_t *src_mac = recv_info->src_addr;
#else
void on_esp_now_recv(const uint8_t *mac_addr, const uint8_t *data, int len) {
    const uint8_t *src_mac = mac_addr;
#endif
    if (len <= 0 || len >= 512) {
        return;
    }

    // Verify sender MAC matches configured unicast target node
    if (memcmp(src_mac, TARGET_NODE_MAC, 6) != 0) {
        return; // Drop packet from unauthenticated/foreign MAC
    }

    // Directly stream payload line to Serial to preserve exact bytes and signature
    Serial.write(data, len);
    Serial.println();
    blink_led(10);
}

void on_esp_now_send(const uint8_t *mac_addr, esp_now_send_status_t status) {
    if (status != ESP_NOW_SEND_SUCCESS) {
        Serial.println("{\"v\":1,\"t\":\"gw_err\",\"err\":\"esp_now_delivery_failed\"}");
    }
}

void setup() {
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, HIGH); // Off initially

    Serial.begin(SERIAL_BAUD_RATE);
    unsigned long wait_start = millis();
    while (!Serial && (millis() - wait_start < 2000)) {
        delay(10);
    }

    // Validate unicast peer address
    if (is_broadcast_mac(TARGET_NODE_MAC) || is_zero_mac(TARGET_NODE_MAC)) {
        Serial.println("{\"v\":1,\"t\":\"gw_err\",\"err\":\"broadcast_mac_not_allowed\"}");
        while (true) {
            blink_led(50);
            delay(500);
        }
    }

    // Initialize Wi-Fi in Station mode
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    // Set Wi-Fi channel
    esp_wifi_set_promiscuous(true);
    esp_wifi_set_channel(ESPNOW_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false);

    // Initialize ESP-NOW
    if (esp_now_init() != ESP_OK) {
        Serial.println("{\"v\":1,\"t\":\"gw_err\",\"err\":\"esp_now_init_failed\"}");
        return;
    }

    // Set Primary Master Key (PMK)
    uint8_t pmk_bytes[16] = {0};
    load_16byte_key(ESPNOW_PMK, pmk_bytes);
    esp_now_set_pmk(pmk_bytes);

    esp_now_register_recv_cb(on_esp_now_recv);
    esp_now_register_send_cb(on_esp_now_send);

    // Add target peer (Encrypted Unicast)
    memset(&peer_info, 0, sizeof(peer_info));
    memcpy(peer_info.peer_addr, TARGET_NODE_MAC, 6);
    peer_info.channel = ESPNOW_WIFI_CHANNEL;
    peer_info.encrypt = true;
    load_16byte_key(ESPNOW_LMK, peer_info.lmk);

    if (esp_now_add_peer(&peer_info) != ESP_OK) {
        Serial.println("{\"v\":1,\"t\":\"gw_err\",\"err\":\"add_peer_failed\"}");
    }

    // Gateway announce
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
        Serial.print("{\"v\":1,\"t\":\"gw_hb\",\"uptime_s\":");
        Serial.print(now / 1000);
        Serial.println("}");
    }

    // 2. Read JSON Lines commands from Host USB Serial (ROCK Pi S)
    while (Serial.available() > 0) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (serial_rx_idx > 0) {
                serial_rx_buf[serial_rx_idx] = '\0';

                // Forward exact signed bytes to wireless node over ESP-NOW
                esp_err_t result = esp_now_send(
                    TARGET_NODE_MAC,
                    (const uint8_t *)serial_rx_buf,
                    serial_rx_idx
                );

                if (result == ESP_OK) {
                    blink_led(15);
                } else {
                    Serial.print("{\"v\":1,\"t\":\"gw_err\",\"err\":\"esp_now_send_error\",\"code\":");
                    Serial.print((int)result);
                    Serial.println("}");
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
