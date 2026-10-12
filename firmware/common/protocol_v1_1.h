#ifndef PROTOCOL_V1_1_H
#define PROTOCOL_V1_1_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "sha256.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PROTOCOL_VERSION "1.1"

/**
 * Computes 16-hex truncated HMAC-SHA256 over canonical string.
 * out_sig16 must be at least 17 bytes (16 hex chars + null terminator).
 */
static inline void protocol_compute_sig16(const char *canonical_str, const char *secret_key, char *out_sig16) {
    uint8_t hmac_out[32];
    size_t klen = secret_key ? strlen(secret_key) : 0;
    size_t plen = canonical_str ? strlen(canonical_str) : 0;

    hmac_sha256((const uint8_t *)secret_key, klen, (const uint8_t *)canonical_str, plen, hmac_out);

    static const char hex_digits[] = "0123456789abcdef";
    for (int i = 0; i < 8; i++) {
        out_sig16[i * 2]     = hex_digits[(hmac_out[i] >> 4) & 0x0F];
        out_sig16[i * 2 + 1] = hex_digits[hmac_out[i] & 0x0F];
    }
    out_sig16[16] = '\0';
}

/**
 * Constant-time comparison of two 16-byte hex strings.
 */
static inline bool protocol_constant_time_compare(const char *a, const char *b) {
    int diff = 0;
    for (int i = 0; i < 16; i++) {
        if (!a[i] || !b[i]) return false;
        diff |= (a[i] ^ b[i]);
    }
    return (diff == 0);
}

/**
 * Appends ,"sig":"<16hex>"}\n to an unsigned canonical JSON string ending in '}'.
 */
static inline bool protocol_sign_in_place(char *buf, size_t max_size, const char *secret_key) {
    size_t len = strlen(buf);
    if (len < 2 || buf[len - 1] != '}') {
        return false;
    }
    // Remove the trailing '}' temporarily to compute signature over full canonical string
    char sig16[17];
    protocol_compute_sig16(buf, secret_key, sig16);

    // Overwrite the trailing '}' with ,"sig":"<sig16>"}\n
    // Needed additional size: 23 bytes (comma + "sig":" + 16 chars + "}" + "\n" + null)
    if (len - 1 + 25 >= max_size) {
        return false;
    }
    snprintf(buf + (len - 1), max_size - (len - 1), ",\"sig\":\"%s\"}\n", sig16);
    return true;
}

/**
 * Verifies and strips the signature from a raw JSON line.
 * Searches for ',"sig":"' suffix, restores '}', verifies HMAC-SHA256.
 * If valid, returns true and buf contains the exact unsigned canonical JSON string.
 */
static inline bool protocol_verify_and_strip_sig(char *buf, const char *secret_key, char *out_sig) {
    if (!buf || !secret_key) return false;

    // Strip trailing newlines or whitespace
    size_t len = strlen(buf);
    while (len > 0 && (buf[len - 1] == '\r' || buf[len - 1] == '\n' || buf[len - 1] == ' ')) {
        buf[--len] = '\0';
    }

    // Must end with '}'
    if (len < 26 || buf[len - 1] != '}') {
        return false;
    }

    // Locate ',"sig":"'
    char *sig_pos = strstr(buf, ",\"sig\":\"");
    if (!sig_pos) {
        return false;
    }

    // Ensure the structure matches ,"sig":"<16hex>"} (8 + 16 + 2 = 26 bytes)
    if (buf + len - sig_pos != 26) {
        return false;
    }

    char actual_sig[17];
    memcpy(actual_sig, sig_pos + 8, 16);
    actual_sig[16] = '\0';

    if (out_sig) {
        memcpy(out_sig, actual_sig, 17);
    }

    // Truncate at sig_pos and restore '}'
    *sig_pos = '}';
    *(sig_pos + 1) = '\0';

    // Compute expected signature on the restored string
    char expected_sig[17];
    protocol_compute_sig16(buf, secret_key, expected_sig);

    return protocol_constant_time_compare(actual_sig, expected_sig);
}

// ============================================================================
// JSON String Escaping Helper (Escapes '"' and '\\' for canonical JSON lines)
// ============================================================================

static inline int protocol_json_escape(const char *src, char *out_buf, size_t out_size) {
    if (!out_buf || out_size == 0) return 0;
    if (!src) {
        out_buf[0] = '\0';
        return 0;
    }
    size_t out_idx = 0;
    for (size_t i = 0; src[i] != '\0'; i++) {
        char c = src[i];
        if (c == '"' || c == '\\') {
            if (out_idx + 2 >= out_size) {
                out_buf[out_idx] = '\0';
                return -1;
            }
            out_buf[out_idx++] = '\\';
            out_buf[out_idx++] = c;
        } else if (c == '\n') {
            if (out_idx + 2 >= out_size) { out_buf[out_idx] = '\0'; return -1; }
            out_buf[out_idx++] = '\\';
            out_buf[out_idx++] = 'n';
        } else if (c == '\r') {
            if (out_idx + 2 >= out_size) { out_buf[out_idx] = '\0'; return -1; }
            out_buf[out_idx++] = '\\';
            out_buf[out_idx++] = 'r';
        } else if (c == '\t') {
            if (out_idx + 2 >= out_size) { out_buf[out_idx] = '\0'; return -1; }
            out_buf[out_idx++] = '\\';
            out_buf[out_idx++] = 't';
        } else {
            if (out_idx + 1 >= out_size) {
                out_buf[out_idx] = '\0';
                return -1;
            }
            out_buf[out_idx++] = c;
        }
    }
    out_buf[out_idx] = '\0';
    return (int)out_idx;
}

#define json_escape protocol_json_escape

// ============================================================================
// Fixed-Order Message Formatters (Unsigned Canonical JSON)
// ============================================================================

static inline int format_hb_unsigned(char *buf, size_t size, uint32_t nonce, uint32_t seq,
                                    uint32_t uptime_s, bool res_wet, bool ads_ok,
                                    const int pumps[4], int reset_reason, int rssi) {
    return snprintf(buf, size,
        "{\"v\":\"1.1\",\"t\":\"hb\",\"nonce\":%u,\"seq\":%u,\"uptime_s\":%u,\"res_wet\":%s,\"ads_ok\":%s,\"pumps\":[%d,%d,%d,%d],\"reset_reason\":%d,\"rssi\":%d}",
        nonce, seq, uptime_s, res_wet ? "true" : "false", ads_ok ? "true" : "false",
        pumps[0] ? 1 : 0, pumps[1] ? 1 : 0, pumps[2] ? 1 : 0, pumps[3] ? 1 : 0,
        reset_reason, rssi);
}

static inline int format_hb_env_unsigned(char *buf, size_t size, uint32_t nonce, uint32_t seq,
                                         uint32_t uptime_s, bool res_wet, bool ads_ok,
                                         const int pumps[4], int reset_reason, int rssi,
                                         float temp, float rh, float press) {
    return snprintf(buf, size,
        "{\"v\":\"1.1\",\"t\":\"hb\",\"nonce\":%u,\"seq\":%u,\"uptime_s\":%u,\"res_wet\":%s,\"ads_ok\":%s,\"pumps\":[%d,%d,%d,%d],\"reset_reason\":%d,\"rssi\":%d,\"env\":{\"temp\":%.3f,\"rh\":%.3f,\"press\":%.3f}}",
        nonce, seq, uptime_s, res_wet ? "true" : "false", ads_ok ? "true" : "false",
        pumps[0] ? 1 : 0, pumps[1] ? 1 : 0, pumps[2] ? 1 : 0, pumps[3] ? 1 : 0,
        reset_reason, rssi, temp, rh, press);
}

static inline int format_env_unsigned(char *buf, size_t size, uint32_t nonce, uint32_t seq,
                                      float temp, float rh, float press) {
    return snprintf(buf, size,
        "{\"v\":\"1.1\",\"t\":\"env\",\"nonce\":%u,\"seq\":%u,\"temp\":%.3f,\"rh\":%.3f,\"press\":%.3f}",
        nonce, seq, temp, rh, press);
}

static inline int format_soil_unsigned(char *buf, size_t size, uint32_t nonce, uint32_t seq,
                                      int z, float volts) {
    return snprintf(buf, size,
        "{\"v\":\"1.1\",\"t\":\"soil\",\"nonce\":%u,\"seq\":%u,\"z\":%d,\"volts\":%.3f}",
        nonce, seq, z, volts);
}

static inline int format_flow_unsigned(char *buf, size_t size, uint32_t nonce, uint32_t seq,
                                      int z, int delta_pulses, int period_ms) {
    return snprintf(buf, size,
        "{\"v\":\"1.1\",\"t\":\"flow\",\"nonce\":%u,\"seq\":%u,\"z\":%d,\"delta_pulses\":%d,\"period_ms\":%d}",
        nonce, seq, z, delta_pulses, period_ms);
}

static inline int format_pump_unsigned(char *buf, size_t size, uint32_t nonce, uint32_t seq,
                                      int z, bool on, const char *reason, int run_s) {
    char esc_reason[64];
    protocol_json_escape(reason ? reason : "", esc_reason, sizeof(esc_reason));
    return snprintf(buf, size,
        "{\"v\":\"1.1\",\"t\":\"pump\",\"nonce\":%u,\"seq\":%u,\"z\":%d,\"on\":%s,\"reason\":\"%s\",\"run_s\":%d}",
        nonce, seq, z, on ? "true" : "false", esc_reason, run_s);
}

static inline int format_ack_unsigned(char *buf, size_t size, uint32_t nonce, uint32_t seq,
                                     const char *id, const char *status, const char *err) {
    char esc_id[64];
    char esc_err[64];
    protocol_json_escape(id ? id : "", esc_id, sizeof(esc_id));
    protocol_json_escape(err ? err : "", esc_err, sizeof(esc_err));
    return snprintf(buf, size,
        "{\"v\":\"1.1\",\"t\":\"ack\",\"nonce\":%u,\"seq\":%u,\"id\":\"%s\",\"status\":\"%s\",\"err\":\"%s\"}",
        nonce, seq, esc_id, status ? status : "", esc_err);
}

static inline int format_cmd_unsigned(char *buf, size_t size, uint32_t nonce, uint32_t seq,
                                     const char *id, int z, int run_s) {
    char esc_id[64];
    protocol_json_escape(id ? id : "", esc_id, sizeof(esc_id));
    return snprintf(buf, size,
        "{\"v\":\"1.1\",\"t\":\"cmd\",\"nonce\":%u,\"seq\":%u,\"id\":\"%s\",\"z\":%d,\"run_s\":%d}",
        nonce, seq, esc_id, z, run_s);
}

static inline int format_stop_unsigned(char *buf, size_t size, uint32_t nonce, uint32_t seq,
                                      const char *id, int z) {
    char esc_id[64];
    protocol_json_escape(id ? id : "", esc_id, sizeof(esc_id));
    return snprintf(buf, size,
        "{\"v\":\"1.1\",\"t\":\"stop\",\"nonce\":%u,\"seq\":%u,\"id\":\"%s\",\"z\":%d}",
        nonce, seq, esc_id, z);
}

static inline int format_stop_all_unsigned(char *buf, size_t size, uint32_t nonce, uint32_t seq,
                                          const char *id) {
    char esc_id[64];
    protocol_json_escape(id ? id : "", esc_id, sizeof(esc_id));
    return snprintf(buf, size,
        "{\"v\":\"1.1\",\"t\":\"stop_all\",\"nonce\":%u,\"seq\":%u,\"id\":\"%s\"}",
        nonce, seq, esc_id);
}

#ifdef __cplusplus
}
#endif

#endif // PROTOCOL_V1_1_H
