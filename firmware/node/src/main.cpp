/**
 * ESP32-C3 Super Mini - Wireless Precision Irrigation & Sensor Node (Protocol v1.1)
 * Interfaces:
 *   - 4x Logic-Level N-MOSFET pump drivers: GPIO 0, 1, 3, 10
 *   - 1x Zone 1 Hall-Effect flow meter (interrupt-driven): GPIO 4
 *   - 1x Reservoir float switch (debounced): GPIO 5
 *   - ADS1115 (0x48) 16-bit I2C ADC (SDA GPIO 6, SCL GPIO 7)
 *   - Hardware Task Watchdog (10s)
 *   - Hardcoded 60s maximum continuous pump cutoff
 *   - Max 2 concurrent pumps enforcement
 *   - Reservoir dry cutoff & linkloss cutoff
 *   - Non-blocking FreeRTOS queue for ESP-NOW packet processing
 *   - HMAC-SHA256 canonical packet signing and command verification
 */

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_task_wdt.h>
#include <esp_system.h>
#include <esp_random.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <ArduinoJson.h>
#include "config.h"
#include "protocol_v1_1.h"

// ----------------------------------------------------------------------------
// Actuator & Sensor State
// ----------------------------------------------------------------------------
struct PumpChannel {
    uint8_t pin;
    bool running;
    unsigned long started_at_ms;
    unsigned long off_at_ms;
};

static PumpChannel pumps[4] = {
    {PIN_PUMP_ZONE_1, false, 0, 0},
    {PIN_PUMP_ZONE_2, false, 0, 0},
    {PIN_PUMP_ZONE_3, false, 0, 0},
    {PIN_PUMP_ZONE_4, false, 0, 0}
};

static Adafruit_ADS1115 ads;
static bool ads_available = false;
static unsigned long last_ads_retry_ms = 0;

// Flow meter pulse delta tracking
static volatile uint32_t flow_pulse_counter = 0;
static uint32_t last_reported_flow_pulses = 0;
static void IRAM_ATTR flow_meter_isr() {
    flow_pulse_counter++;
}

// Reservoir float state & debounce
static bool last_reservoir_wet = false;
static int last_raw_float_reading = -1;
static unsigned long last_float_debounce_ms = 0;

// Monotonic sequence and boot tracking
static uint32_t node_boot_nonce = 0;
static uint32_t node_tx_seq = 0;

// Host Pi session tracking
static bool pi_session_active = false;
static uint32_t pi_last_nonce = 0;
static uint32_t pi_last_seq = 0;

// Timing counters
static unsigned long last_heartbeat_ms = 0;
static unsigned long last_soil_read_ms = 0;
static unsigned long last_flow_report_ms = 0;
static unsigned long last_valid_rx_ms = 0;

static esp_now_peer_info_t gateway_peer;

// FreeRTOS non-blocking receive queue
#define RX_QUEUE_SIZE 8
#define RX_BUF_MAX 300

struct RxPacketItem {
    uint16_t len;
    char data[RX_BUF_MAX];
};

static QueueHandle_t rx_packet_queue = NULL;

void blink_led(int ms = 20) {
    digitalWrite(PIN_STATUS_LED, LOW);
    // Non-blocking short blink indicator
    digitalWrite(PIN_STATUS_LED, HIGH);
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

// ----------------------------------------------------------------------------
// Packet Transmission Helpers
// ----------------------------------------------------------------------------
static bool transmit_packet(char *buf, size_t buf_size) {
    if (!protocol_sign_in_place(buf, buf_size, ESPNOW_SHARED_KEY)) {
        return false;
    }
    size_t len = strlen(buf);
    esp_err_t res = esp_now_send(GATEWAY_MAC, (const uint8_t *)buf, len);
    return (res == ESP_OK);
}

static void emit_pump_event(int z, bool on, const char *reason, int run_s) {
    char buf[256];
    format_pump_unsigned(buf, sizeof(buf), node_boot_nonce, node_tx_seq++, z, on, reason, run_s);
    transmit_packet(buf, sizeof(buf));
}

static void send_ack(const char *cmd_id, const char *status, const char *err = nullptr) {
    char buf[256];
    format_ack_unsigned(buf, sizeof(buf), node_boot_nonce, node_tx_seq++, cmd_id, status, err);
    transmit_packet(buf, sizeof(buf));
}

static void send_heartbeat(unsigned long now) {
    int p_arr[4];
    for (int i = 0; i < 4; i++) {
        p_arr[i] = pumps[i].running ? 1 : 0;
    }
    char buf[256];
    format_hb_unsigned(buf, sizeof(buf), node_boot_nonce, node_tx_seq++,
                       now / 1000UL, last_reservoir_wet, ads_available,
                       p_arr, (int)esp_reset_reason(), WiFi.RSSI());
    transmit_packet(buf, sizeof(buf));
}

static void send_soil(int z, float volts) {
    char buf[256];
    format_soil_unsigned(buf, sizeof(buf), node_boot_nonce, node_tx_seq++, z, volts);
    transmit_packet(buf, sizeof(buf));
}

static void send_flow(int z, uint32_t delta_pulses, int period_ms) {
    char buf[256];
    format_flow_unsigned(buf, sizeof(buf), node_boot_nonce, node_tx_seq++, z, (int)delta_pulses, period_ms);
    transmit_packet(buf, sizeof(buf));
}

// ----------------------------------------------------------------------------
// Sensor Robustness: Median of 8 ADS1115 Samples per Probe
// ----------------------------------------------------------------------------
static float read_probe_median(int channel) {
    if (!ads_available) return 0.0f;

    float samples[8];
    int valid_count = 0;

    for (int s = 0; s < 8; s++) {
        int16_t raw = ads.readADC_SingleEnded(channel);
        float v = ads.computeVolts(raw);
        if (v >= 0.0f && v <= 3.6f) {
            samples[valid_count++] = v;
        }
    }

    if (valid_count == 0) return 0.0f;

    // Insertion sort to extract median
    for (int i = 1; i < valid_count; i++) {
        float key = samples[i];
        int j = i - 1;
        while (j >= 0 && samples[j] > key) {
            samples[j + 1] = samples[j];
            j--;
        }
        samples[j + 1] = key;
    }

    return samples[valid_count / 2];
}

// ----------------------------------------------------------------------------
// Non-Blocking ESP-NOW Receive ISR Callback
// ----------------------------------------------------------------------------
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
void on_data_recv(const esp_now_recv_info_t *recv_info, const uint8_t *data, int len) {
    const uint8_t *src_mac = recv_info->src_addr;
#else
void on_data_recv(const uint8_t *mac_addr, const uint8_t *data, int len) {
    const uint8_t *src_mac = mac_addr;
#endif
    if (len <= 0 || len >= RX_BUF_MAX) return;

    // Reject packets not from authenticated gateway MAC
    if (memcmp(src_mac, GATEWAY_MAC, 6) != 0) return;

    if (rx_packet_queue != NULL) {
        RxPacketItem item;
        item.len = (uint16_t)len;
        memcpy(item.data, data, len);
        item.data[len] = '\0';

        BaseType_t xHigherPriorityTaskWoken = pdFALSE;
        xQueueSendFromISR(rx_packet_queue, &item, &xHigherPriorityTaskWoken);
        if (xHigherPriorityTaskWoken) {
            portYIELD_FROM_ISR();
        }
    }
}

// ----------------------------------------------------------------------------
// Command Execution & Verification (Called from loop)
// ----------------------------------------------------------------------------
static void process_incoming_packet(char *raw_buf, size_t len) {
    char out_sig[17];
    if (!protocol_verify_and_strip_sig(raw_buf, ESPNOW_SHARED_KEY, out_sig)) {
        return; // Signature invalid or tampered
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, raw_buf);
    if (err) return;

    if (strcmp(doc["v"] | "", "1.1") != 0) return;

    uint32_t cmd_nonce = doc["nonce"] | 0;
    uint32_t cmd_seq = doc["seq"] | 0;

    // Replay & Monotonic Session Tracking
    if (!pi_session_active || cmd_nonce != pi_last_nonce) {
        // Fresh Pi session accepted
        pi_session_active = true;
        pi_last_nonce = cmd_nonce;
        pi_last_seq = cmd_seq;
    } else {
        if (cmd_seq <= pi_last_seq) {
            return; // Out of order or replayed sequence
        }
        pi_last_seq = cmd_seq;
    }

    // Refresh valid packet timestamp for linkloss guard
    last_valid_rx_ms = millis();

    const char *type = doc["t"] | "";
    const char *cmd_id = doc["id"] | "";

    if (strcmp(type, "stop_all") == 0) {
        for (int i = 0; i < 4; i++) {
            if (pumps[i].running) {
                unsigned long run_s = (millis() - pumps[i].started_at_ms) / 1000UL;
                digitalWrite(pumps[i].pin, LOW);
                pumps[i].running = false;
                pumps[i].off_at_ms = 0;
                emit_pump_event(i + 1, false, "cmd", (int)run_s);
            }
        }
        send_ack(cmd_id, "ok", nullptr);
        return;
    }

    if (strcmp(type, "stop") == 0) {
        int z = doc["z"] | 0;
        if (z >= 1 && z <= 4) {
            int idx = z - 1;
            if (pumps[idx].running) {
                unsigned long run_s = (millis() - pumps[idx].started_at_ms) / 1000UL;
                digitalWrite(pumps[idx].pin, LOW);
                pumps[idx].running = false;
                pumps[idx].off_at_ms = 0;
                emit_pump_event(z, false, "cmd", (int)run_s);
            }
            send_ack(cmd_id, "ok", nullptr);
        } else {
            send_ack(cmd_id, "rejected", "invalid_zone");
        }
        return;
    }

    if (strcmp(type, "cmd") == 0) {
        int z = doc["z"] | 0;
        int req_s = doc["run_s"] | 15;

        if (z < 1 || z > 4) {
            send_ack(cmd_id, "rejected", "invalid_zone");
            return;
        }

        // Part 3.1: Reservoir dry check
        if (!last_reservoir_wet) {
            send_ack(cmd_id, "rejected", "reservoir_dry");
            return;
        }

        int target_idx = z - 1;

        // Part 3.3: Max 2 concurrent pumps enforcement
        int concurrent = 0;
        for (int i = 0; i < 4; i++) {
            if (pumps[i].running && i != target_idx) {
                concurrent++;
            }
        }
        if (concurrent >= MAX_CONCURRENT_PUMPS) {
            send_ack(cmd_id, "rejected", "limit");
            return;
        }

        // Clamp run duration (1 to 60s hard ceiling)
        int safe_s = req_s;
        if (safe_s <= 0) safe_s = 5;
        if (safe_s > MAX_PUMP_HARD_TIMEOUT_S) safe_s = MAX_PUMP_HARD_TIMEOUT_S;

        unsigned long now = millis();
        pumps[target_idx].running = true;
        pumps[target_idx].started_at_ms = now;
        pumps[target_idx].off_at_ms = now + ((unsigned long)safe_s * 1000UL);
        digitalWrite(pumps[target_idx].pin, HIGH);

        emit_pump_event(z, true, "cmd", safe_s);
        send_ack(cmd_id, "ok", nullptr);
        return;
    }
}

// ----------------------------------------------------------------------------
// Setup
// ----------------------------------------------------------------------------
void setup() {
    node_boot_nonce = esp_random();

    pinMode(PIN_STATUS_LED, OUTPUT);
    digitalWrite(PIN_STATUS_LED, HIGH);

    // 1. Initialize Pump MOSFETs to forced LOW
    for (int i = 0; i < 4; i++) {
        pinMode(pumps[i].pin, OUTPUT);
        digitalWrite(pumps[i].pin, LOW);
        pumps[i].running = false;
        pumps[i].started_at_ms = 0;
        pumps[i].off_at_ms = 0;
    }

    // 2. Setup Reservoir Float with internal pull-up (LOW = wet)
    pinMode(PIN_RESERVOIR_FLOAT, INPUT_PULLUP);
    last_reservoir_wet = (digitalRead(PIN_RESERVOIR_FLOAT) == LOW);

    // 3. Setup Flow Meter interrupt (Zone 1)
    pinMode(PIN_FLOW_METER, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_FLOW_METER), flow_meter_isr, FALLING);

    // 4. Initialize I2C and ADS1115
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
    ads_available = ads.begin(0x48);
    if (ads_available) {
        ads.setGain(GAIN_ONE);
    }

    // Validate unicast gateway address
    if (is_broadcast_mac(GATEWAY_MAC) || is_zero_mac(GATEWAY_MAC)) {
        while (true) {
            digitalWrite(PIN_STATUS_LED, LOW);
            delay(100);
            digitalWrite(PIN_STATUS_LED, HIGH);
            delay(500);
        }
    }

    // 5. Initialize Wi-Fi & ESP-NOW
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    esp_wifi_set_promiscuous(true);
    esp_wifi_set_channel(ESPNOW_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false);

    if (esp_now_init() == ESP_OK) {
        // Set Primary Master Key (PMK)
        uint8_t pmk_bytes[16] = {0};
        load_16byte_key(ESPNOW_PMK, pmk_bytes);
        esp_now_set_pmk(pmk_bytes);

        // Create receive queue
        rx_packet_queue = xQueueCreate(RX_QUEUE_SIZE, sizeof(RxPacketItem));
        esp_now_register_recv_cb(on_data_recv);

        // Add gateway peer (Encrypted Unicast)
        memset(&gateway_peer, 0, sizeof(gateway_peer));
        memcpy(gateway_peer.peer_addr, GATEWAY_MAC, 6);
        gateway_peer.channel = ESPNOW_WIFI_CHANNEL;
        gateway_peer.encrypt = true;
        load_16byte_key(ESPNOW_LMK, gateway_peer.lmk);
        esp_now_add_peer(&gateway_peer);
    }

    // 6. Setup Task Watchdog (10s)
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
    esp_task_wdt_config_t wdt_config = {
        .timeout_ms = WATCHDOG_TIMEOUT_S * 1000,
        .idle_core_mask = 0,
        .trigger_panic = true,
    };
    esp_task_wdt_reconfigure(&wdt_config);
#else
    esp_task_wdt_init(WATCHDOG_TIMEOUT_S, true);
    esp_task_wdt_add(NULL);
#endif

    digitalWrite(PIN_STATUS_LED, HIGH);
}

// ----------------------------------------------------------------------------
// Main Loop (Non-blocking, zero delay())
// ----------------------------------------------------------------------------
void loop() {
    esp_task_wdt_reset();
    unsigned long now = millis();

    // 1. Drain non-blocking incoming packet queue
    if (rx_packet_queue != NULL) {
        RxPacketItem item;
        while (xQueueReceive(rx_packet_queue, &item, 0) == pdTRUE) {
            process_incoming_packet(item.data, item.len);
        }
    }

    // 2. Hardware Run Timeout Guard (60s continuous cap)
    for (int i = 0; i < 4; i++) {
        if (pumps[i].running && (long)(now - pumps[i].off_at_ms) >= 0) {
            unsigned long run_s = (now - pumps[i].started_at_ms) / 1000UL;
            digitalWrite(pumps[i].pin, LOW);
            pumps[i].running = false;
            pumps[i].off_at_ms = 0;
            emit_pump_event(i + 1, false, "timeout", (int)run_s);
        }
    }

    // 3. Debounce Reservoir Float Switch
    int raw_float = digitalRead(PIN_RESERVOIR_FLOAT);
    if (raw_float != last_raw_float_reading) {
        last_raw_float_reading = raw_float;
        last_float_debounce_ms = now;
    }
    if ((now - last_float_debounce_ms) >= FLOAT_DEBOUNCE_MS) {
        bool current_wet = (raw_float == LOW);
        if (current_wet != last_reservoir_wet) {
            last_reservoir_wet = current_wet;

            // Part 3.1: If float goes dry, stop all pumps immediately within 1 loop pass
            if (!last_reservoir_wet) {
                for (int i = 0; i < 4; i++) {
                    if (pumps[i].running) {
                        unsigned long run_s = (now - pumps[i].started_at_ms) / 1000UL;
                        digitalWrite(pumps[i].pin, LOW);
                        pumps[i].running = false;
                        pumps[i].off_at_ms = 0;
                        emit_pump_event(i + 1, false, "dry", (int)run_s);
                    }
                }
            }
        }
    }

    // 4. Part 3.2: Linkloss cutoff (>60s without valid command/ack)
    if (last_valid_rx_ms > 0 && (now - last_valid_rx_ms >= LINK_LOSS_TIMEOUT_MS)) {
        for (int i = 0; i < 4; i++) {
            if (pumps[i].running) {
                unsigned long run_s = (now - pumps[i].started_at_ms) / 1000UL;
                digitalWrite(pumps[i].pin, LOW);
                pumps[i].running = false;
                pumps[i].off_at_ms = 0;
                emit_pump_event(i + 1, false, "linkloss", (int)run_s);
            }
        }
    }

    // 5. ADS1115 Fault Recovery (retry every 30s)
    if (!ads_available && (now - last_ads_retry_ms >= ADS_RETRY_INTERVAL_MS)) {
        last_ads_retry_ms = now;
        Wire.beginTransmission(0x48);
        if (Wire.endTransmission() == 0) {
            ads_available = ads.begin(0x48);
            if (ads_available) {
                ads.setGain(GAIN_ONE);
            }
        }
    }

    // 6. Heartbeat Transmission every 5s
    if (now - last_heartbeat_ms >= HEARTBEAT_INTERVAL_MS) {
        last_heartbeat_ms = now;
        send_heartbeat(now);
    }

    // 7. Soil Moisture Probes Read (ADS1115 A0..A3) every 10s (Median of 8)
    if (now - last_soil_read_ms >= SOIL_READ_INTERVAL_MS) {
        last_soil_read_ms = now;
        if (ads_available) {
            for (int z = 1; z <= 4; z++) {
                float volts = read_probe_median(z - 1);
                send_soil(z, volts);
            }
        }
    }

    // 8. Zone 1 Flow Meter Delta Telemetry
    if (pumps[0].running && (now - last_flow_report_ms >= FLOW_REPORT_INTERVAL_MS)) {
        last_flow_report_ms = now;
        uint32_t current_pulses = flow_pulse_counter;
        uint32_t delta = current_pulses - last_reported_flow_pulses;
        last_reported_flow_pulses = current_pulses;
        send_flow(1, delta, 1000);
    }
}
