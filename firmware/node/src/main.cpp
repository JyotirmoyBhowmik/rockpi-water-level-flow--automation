/**
 * ESP32-C3 Super Mini - Wireless Precision Irrigation & Sensor Node (Protocol v1.1)
 * Interfaces:
 *   - 4x Logic-Level N-MOSFET pump drivers: GPIO 0, 1, 3, 10
 *   - 1x Zone 1 Hall-Effect flow meter (interrupt-driven): GPIO 4
 *   - 1x Reservoir float switch (debounced): GPIO 5
 *   - ADS1115 (0x48) 16-bit I2C ADC (SDA GPIO 6, SCL GPIO 7)
 *   - Hardware Task Watchdog (10s)
 *   - Hardcoded 60s maximum continuous pump cutoff
 *   - HMAC-SHA256 canonical packet signing and command verification
 */

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_task_wdt.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <ArduinoJson.h>
#include "mbedtls/md.h"
#include "config.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#define ESPNOW_SHARED_KEY "rockpi-garden-v1.1-key"
#endif

// ----------------------------------------------------------------------------
// Actuator & Sensor State
// ----------------------------------------------------------------------------
struct PumpChannel {
    uint8_t pin;
    bool running;
    unsigned long off_at_ms;
};

static PumpChannel pumps[4] = {
    {PIN_PUMP_ZONE_1, false, 0},
    {PIN_PUMP_ZONE_2, false, 0},
    {PIN_PUMP_ZONE_3, false, 0},
    {PIN_PUMP_ZONE_4, false, 0}
};

static Adafruit_ADS1115 ads;
static bool ads_available = false;

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
RTC_DATA_ATTR static uint32_t boot_counter = 0;
static uint32_t tx_seq = 0;
static uint32_t rx_last_seq = 0;

// Timing counters
static unsigned long last_heartbeat_ms = 0;
static unsigned long last_soil_read_ms = 0;
static unsigned long last_flow_report_ms = 0;

static esp_now_peer_info_t gateway_peer;

void blink_led(int ms = 20) {
    digitalWrite(PIN_STATUS_LED, LOW);
    delay(ms);
    digitalWrite(PIN_STATUS_LED, HIGH);
}

// ----------------------------------------------------------------------------
// Cryptographic Signing & Verification Helper (HMAC-SHA256 truncated to 16 hex)
// ----------------------------------------------------------------------------
static void compute_hmac_16(const char *payload, char *out_hex) {
    uint8_t hmac_result[32];
    mbedtls_md_context_t ctx;
    mbedtls_md_type_t md_type = MBEDTLS_MD_SHA256;

    mbedtls_md_init(&ctx);
    mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(md_type), 1);
    mbedtls_md_hmac_starts(&ctx, (const unsigned char *)ESPNOW_SHARED_KEY, strlen(ESPNOW_SHARED_KEY));
    mbedtls_md_hmac_update(&ctx, (const unsigned char *)payload, strlen(payload));
    mbedtls_md_hmac_finish(&ctx, hmac_result);
    mbedtls_md_free(&ctx);

    for (int i = 0; i < 8; i++) {
        sprintf(out_hex + (i * 2), "%02x", hmac_result[i]);
    }
    out_hex[16] = '\0';
}

static bool send_packet(JsonDocument &doc) {
    doc["v"] = "1.1";
    doc["seq"] = tx_seq++;

    char unsigned_buf[256];
    serializeJson(doc, unsigned_buf, sizeof(unsigned_buf));

    char sig[17];
    compute_hmac_16(unsigned_buf, sig);
    doc["sig"] = sig;

    char final_buf[280];
    size_t len = serializeJson(doc, final_buf, sizeof(final_buf));
    if (len == 0 || len >= sizeof(final_buf)) return false;

    esp_err_t res = esp_now_send(GATEWAY_MAC, (const uint8_t *)final_buf, len);
    return (res == ESP_OK);
}

static void send_ack(const char *cmd_id, const char *status, const char *msg = nullptr) {
    JsonDocument doc;
    doc["t"] = "ack";
    doc["id"] = cmd_id;
    doc["status"] = status;
    if (msg) doc["message"] = msg;
    send_packet(doc);
}

// ----------------------------------------------------------------------------
// ESP-NOW Command Receive Handler
// ----------------------------------------------------------------------------
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
void on_data_recv(const esp_now_recv_info_t *recv_info, const uint8_t *data, int len) {
#else
void on_data_recv(const uint8_t *mac_addr, const uint8_t *data, int len) {
#endif
    if (len <= 0 || len >= 300) return;

    char cmd_str[310];
    memcpy(cmd_str, data, len);
    cmd_str[len] = '\0';

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, cmd_str);
    if (err) return;

    if (strcmp(doc["v"] | "", "1.1") != 0) return;

    const char *sig_in = doc["sig"] | "";
    if (strlen(sig_in) != 16) return;

    // Verify HMAC
    doc.remove("sig");
    char canonical_buf[256];
    serializeJson(doc, canonical_buf, sizeof(canonical_buf));
    char expected_sig[17];
    compute_hmac_16(canonical_buf, expected_sig);
    if (strcmp(sig_in, expected_sig) != 0) return;

    uint32_t seq = doc["seq"] | 0;
    if (rx_last_seq > 0 && seq <= rx_last_seq) return; // Monotonic check
    rx_last_seq = seq;

    const char *type = doc["t"] | "";
    const char *cmd_id = doc["id"] | "";

    if (strcmp(type, "stop_all") == 0) {
        for (int i = 0; i < 4; i++) {
            digitalWrite(pumps[i].pin, LOW);
            pumps[i].running = false;
            pumps[i].off_at_ms = 0;
        }
        send_ack(cmd_id, "ok");
        blink_led(30);
        return;
    }

    if (strcmp(type, "stop") == 0) {
        int z = doc["z"] | 0;
        if (z >= 1 && z <= 4) {
            int idx = z - 1;
            digitalWrite(pumps[idx].pin, LOW);
            pumps[idx].running = false;
            pumps[idx].off_at_ms = 0;
            send_ack(cmd_id, "ok");
            blink_led(20);
        } else {
            send_ack(cmd_id, "rejected", "Invalid zone");
        }
        return;
    }

    if (strcmp(type, "cmd") == 0) {
        int z = doc["z"] | 0;
        int req_s = doc["run_s"] | 15;
        if (z >= 1 && z <= 4) {
            int idx = z - 1;
            int safe_s = req_s;
            if (safe_s <= 0) safe_s = 5;
            if (safe_s > MAX_PUMP_HARD_TIMEOUT_S) safe_s = MAX_PUMP_HARD_TIMEOUT_S;

            digitalWrite(pumps[idx].pin, HIGH);
            pumps[idx].running = true;
            pumps[idx].off_at_ms = millis() + ((unsigned long)safe_s * 1000UL);
            send_ack(cmd_id, "ok");
            blink_led(20);
        } else {
            send_ack(cmd_id, "rejected", "Invalid zone");
        }
        return;
    }
}

// ----------------------------------------------------------------------------
// Setup
// ----------------------------------------------------------------------------
void setup() {
    boot_counter++;
    pinMode(PIN_STATUS_LED, OUTPUT);
    digitalWrite(PIN_STATUS_LED, HIGH);

    // 1. Initialize Pump MOSFETs to forced LOW
    for (int i = 0; i < 4; i++) {
        pinMode(pumps[i].pin, OUTPUT);
        digitalWrite(pumps[i].pin, LOW);
        pumps[i].running = false;
        pumps[i].off_at_ms = 0;
    }

    // 2. Setup Reservoir Float with internal pull-up
    pinMode(PIN_RESERVOIR_FLOAT, INPUT_PULLUP);
    last_reservoir_wet = (digitalRead(PIN_RESERVOIR_FLOAT) == LOW);

    // 3. Setup Flow Meter interrupt (Zone 1)
    pinMode(PIN_FLOW_METER, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_FLOW_METER), flow_meter_isr, FALLING);

    // 4. Initialize I2C and ADS1115 (Fault-Tolerant)
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
    ads_available = ads.begin(0x48);
    if (ads_available) {
        ads.setGain(GAIN_ONE);
    }

    // 5. Initialize Wi-Fi & ESP-NOW
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    esp_wifi_set_promiscuous(true, NULL);
    esp_wifi_set_channel(ESPNOW_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false, NULL);

    if (esp_now_init() == ESP_OK) {
        esp_now_register_recv_cb(on_data_recv);

        memset(&gateway_peer, 0, sizeof(gateway_peer));
        memcpy(gateway_peer.peer_addr, GATEWAY_MAC, 6);
        gateway_peer.channel = ESPNOW_WIFI_CHANNEL;
        gateway_peer.encrypt = false;
        esp_now_add_peer(&gateway_peer);
    }

    // 6. Setup Task Watchdog (10s)
    esp_task_wdt_init(WATCHDOG_TIMEOUT_S, true);
    esp_task_wdt_add(NULL);

    blink_led(100);
}

// ----------------------------------------------------------------------------
// Main Loop
// ----------------------------------------------------------------------------
void loop() {
    esp_task_wdt_reset();
    unsigned long now = millis();

    // 1. HARDWARE TIMEOUT ENFORCEMENT: Cut any pump running past off_at_ms
    for (int i = 0; i < 4; i++) {
        if (pumps[i].running && (long)(now - pumps[i].off_at_ms) >= 0) {
            digitalWrite(pumps[i].pin, LOW);
            pumps[i].running = false;
            pumps[i].off_at_ms = 0;
        }
    }

    // 2. Debounce Reservoir Float Switch
    int raw_float = digitalRead(PIN_RESERVOIR_FLOAT);
    if (raw_float != last_raw_float_reading) {
        last_raw_float_reading = raw_float;
        last_float_debounce_ms = now;
    }
    if ((now - last_float_debounce_ms) >= FLOAT_DEBOUNCE_MS) {
        bool current_wet = (raw_float == LOW);
        if (current_wet != last_reservoir_wet) {
            last_reservoir_wet = current_wet;
        }
    }

    // 3. Heartbeat Transmission every 5s
    if (now - last_heartbeat_ms >= HEARTBEAT_INTERVAL_MS) {
        last_heartbeat_ms = now;
        JsonDocument hb_doc;
        hb_doc["t"] = "hb";
        hb_doc["boot"] = boot_counter;
        hb_doc["uptime_s"] = now / 1000;
        hb_doc["res_wet"] = last_reservoir_wet;
        hb_doc["ads_ok"] = ads_available;
        JsonArray p_arr = hb_doc.createNestedArray("pumps");
        for (int i = 0; i < 4; i++) {
            p_arr.add(pumps[i].running ? 1 : 0);
        }
        hb_doc["rssi"] = WiFi.RSSI();
        send_packet(hb_doc);
    }

    // 4. Soil Moisture Probes Read (ADS1115 A0..A3) every 10s
    if (now - last_soil_read_ms >= SOIL_READ_INTERVAL_MS) {
        last_soil_read_ms = now;
        if (ads_available) {
            for (int z = 1; z <= 4; z++) {
                int16_t raw_adc = ads.readADC_SingleEnded(z - 1);
                float volts = ads.computeVolts(raw_adc);

                JsonDocument soil_doc;
                soil_doc["t"] = "soil";
                soil_doc["z"] = z;
                soil_doc["volts"] = round(volts * 1000.0) / 1000.0;
                send_packet(soil_doc);
                delay(10);
            }
        }
    }

    // 5. Zone 1 Flow Meter Delta Telemetry
    if (pumps[0].running && (now - last_flow_report_ms >= FLOW_REPORT_INTERVAL_MS)) {
        last_flow_report_ms = now;
        uint32_t current_pulses = flow_pulse_counter;
        uint32_t delta = current_pulses - last_reported_flow_pulses;
        last_reported_flow_pulses = current_pulses;

        JsonDocument flow_doc;
        flow_doc["t"] = "flow";
        flow_doc["z"] = 1;
        flow_doc["pulses_delta"] = delta;
        send_packet(flow_doc);
    }
}
