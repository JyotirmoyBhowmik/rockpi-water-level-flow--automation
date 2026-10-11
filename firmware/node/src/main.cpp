/**
 * ESP32-C3 Super Mini - Wireless Precision Irrigation & Sensor Node
 * Interfaces:
 *   - ADS1115 (0x48) 16-bit ADC: 4x capacitive soil probes (A0..A3)
 *   - 4x Logic-Level N-MOSFET pump drivers with hardware firmware safety timer
 *   - 1x Reservoir float switch (debounced input)
 *   - 1x Zone 1 pulse flow meter (interrupt-driven pulse counter)
 * Speaks ESP-NOW to ESP32-C3 Gateway.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <ArduinoJson.h>
#include "config.h"

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

// Flow meter pulse counter (Zone 1)
static volatile uint32_t flow_pulse_counter = 0;
static void IRAM_ATTR flow_meter_isr() {
    flow_pulse_counter++;
}

// Reservoir float state & debounce
static bool last_reservoir_wet = false;
static int last_raw_float_reading = -1;
static unsigned long last_float_debounce_ms = 0;

// Timing counters
static unsigned long last_heartbeat_ms = 0;
static unsigned long last_soil_read_ms = 0;
static unsigned long last_flow_report_ms = 0;
static uint32_t heartbeat_seq = 0;

static esp_now_peer_info_t gateway_peer;

void blink_led(int ms = 20) {
    digitalWrite(PIN_STATUS_LED, LOW);
    delay(ms);
    digitalWrite(PIN_STATUS_LED, HIGH);
}

// ----------------------------------------------------------------------------
// Transmission Helper
// ----------------------------------------------------------------------------
static bool send_espnow_json(JsonDocument &doc) {
    char buffer[256];
    size_t len = serializeJson(doc, buffer, sizeof(buffer));
    if (len == 0 || len >= sizeof(buffer)) {
        return false;
    }
    esp_err_t res = esp_now_send(GATEWAY_MAC, (const uint8_t *)buffer, len);
    return (res == ESP_OK);
}

// ----------------------------------------------------------------------------
// ESP-NOW Command Receive Handler
// ----------------------------------------------------------------------------
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
void on_data_recv(const esp_now_recv_info_t *recv_info, const uint8_t *data, int len) {
#else
void on_data_recv(const uint8_t *mac_addr, const uint8_t *data, int len) {
#endif
    if (len <= 0 || len >= 256) return;

    char cmd_str[260];
    memcpy(cmd_str, data, len);
    cmd_str[len] = '\0';

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, cmd_str);
    if (err || doc["v"] != 1) return;

    const char *cmd = doc["cmd"] | "";

    // Emergency or manual Stop All
    if (strcmp(cmd, "stop_all") == 0) {
        for (int i = 0; i < 4; i++) {
            digitalWrite(pumps[i].pin, LOW);
            pumps[i].running = false;
            pumps[i].off_at_ms = 0;
        }
        blink_led(50);
        return;
    }

    // Individual Pump Command
    if (strcmp(cmd, "pump") == 0) {
        int z = doc["z"] | 0;
        int state = doc["state"] | 0;
        int req_duration = doc["duration_s"] | 15;

        if (z >= 1 && z <= 4) {
            int idx = z - 1;
            if (state == 0) {
                // Turn OFF
                digitalWrite(pumps[idx].pin, LOW);
                pumps[idx].running = false;
                pumps[idx].off_at_ms = 0;
            } else {
                // Turn ON with HARDWARE FIRMWARE TIMEOUT GUARANTEE
                // Clamped strictly to MAX_PUMP_HARD_TIMEOUT_S (60s)
                int safe_duration = req_duration;
                if (safe_duration <= 0) safe_duration = 15;
                if (safe_duration > MAX_PUMP_HARD_TIMEOUT_S) safe_duration = MAX_PUMP_HARD_TIMEOUT_S;

                digitalWrite(pumps[idx].pin, HIGH);
                pumps[idx].running = true;
                pumps[idx].off_at_ms = millis() + ((unsigned long)safe_duration * 1000UL);
            }
            blink_led(20);
        }
    }
}

// ----------------------------------------------------------------------------
// Setup
// ----------------------------------------------------------------------------
void setup() {
    pinMode(PIN_STATUS_LED, OUTPUT);
    digitalWrite(PIN_STATUS_LED, HIGH); // Off initially

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

    // 4. Initialize I2C and ADS1115
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
    ads_available = ads.begin(0x48);
    if (ads_available) {
        ads.setGain(GAIN_ONE); // +/- 4.096V range (1 bit = 0.125mV)
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

    // Initial broadcast of reservoir state at boot
    JsonDocument res_doc;
    res_doc["v"] = 1;
    res_doc["t"] = "res";
    res_doc["wet"] = last_reservoir_wet;
    res_doc["ts_ms"] = millis();
    send_espnow_json(res_doc);

    blink_led(100);
}

// ----------------------------------------------------------------------------
// Main Loop
// ----------------------------------------------------------------------------
void loop() {
    unsigned long now = millis();

    // 1. HARDWARE TIMEOUT ENFORCEMENT: Cut any pump that has reached off_at_ms
    for (int i = 0; i < 4; i++) {
        if (pumps[i].running && (long)(now - pumps[i].off_at_ms) >= 0) {
            digitalWrite(pumps[i].pin, LOW);
            pumps[i].running = false;
            pumps[i].off_at_ms = 0;
        }
    }

    // 2. Debounce & Report Reservoir Float Switch
    int raw_float = digitalRead(PIN_RESERVOIR_FLOAT);
    if (raw_float != last_raw_float_reading) {
        last_raw_float_reading = raw_float;
        last_float_debounce_ms = now;
    }
    if ((now - last_float_debounce_ms) >= FLOAT_DEBOUNCE_MS) {
        bool current_wet = (raw_float == LOW);
        if (current_wet != last_reservoir_wet) {
            last_reservoir_wet = current_wet;
            JsonDocument res_doc;
            res_doc["v"] = 1;
            res_doc["t"] = "res";
            res_doc["wet"] = current_wet;
            res_doc["ts_ms"] = now;
            send_espnow_json(res_doc);
            blink_led(10);
        }
    }

    // 3. Heartbeat Transmission every 5s
    if (now - last_heartbeat_ms >= HEARTBEAT_INTERVAL_MS) {
        last_heartbeat_ms = now;
        JsonDocument hb_doc;
        hb_doc["v"] = 1;
        hb_doc["t"] = "hb";
        hb_doc["seq"] = heartbeat_seq++;
        hb_doc["uptime_s"] = now / 1000;
        send_espnow_json(hb_doc);
    }

    // 4. Soil Moisture Probes Read (ADS1115 A0..A3) every 10s
    if (now - last_soil_read_ms >= SOIL_READ_INTERVAL_MS) {
        last_soil_read_ms = now;
        if (ads_available) {
            for (int z = 1; z <= 4; z++) {
                int16_t raw_adc = ads.readADC_SingleEnded(z - 1);
                float volts = ads.computeVolts(raw_adc);

                JsonDocument soil_doc;
                soil_doc["v"] = 1;
                soil_doc["t"] = "soil";
                soil_doc["z"] = z;
                soil_doc["volts"] = round(volts * 1000.0) / 1000.0;
                soil_doc["ts_ms"] = now;
                send_espnow_json(soil_doc);
                delay(10); // Inter-packet space
            }
        }
    }

    // 5. Zone 1 Flow Meter Telemetry (every 5s while Zone 1 is running)
    if (pumps[0].running && (now - last_flow_report_ms >= FLOW_REPORT_INTERVAL_MS)) {
        last_flow_report_ms = now;
        JsonDocument flow_doc;
        flow_doc["v"] = 1;
        flow_doc["t"] = "flow";
        flow_doc["z"] = 1;
        flow_doc["pulses"] = flow_pulse_counter;
        flow_doc["flow_lpm"] = 0.0; // Raw pulse count is authoritative
        flow_doc["ts_ms"] = now;
        send_espnow_json(flow_doc);
    }
}
