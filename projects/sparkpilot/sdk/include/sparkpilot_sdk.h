/**
 * @file sparkpilot_sdk.h
 * @brief SparkPilot Device SDK — Thin C library (~8KB flash) for FreeRTOS devices.
 *
 * CUI // SP-CTI
 *
 * Provides 3 FreeRTOS tasks:
 *   1. MQTT Client Task — bridge between device and SparkPilot cloud agents
 *   2. Command Handler Task — executes agent instructions (OTA, config, reboot, etc.)
 *   3. Telemetry Reporter Task — sends health/sensor data at configurable intervals
 *
 * Supported commands:
 *   OTA_UPDATE, CONFIG_SET, REBOOT, DIAG_DUMP, MODEL_UPDATE, TASK_CONTROL
 *
 * @note This SDK is designed for minimal footprint: ~8KB flash, ~2KB RAM.
 *       It uses FreeRTOS primitives only — no dynamic allocation after init.
 */

#ifndef SPARKPILOT_SDK_H
#define SPARKPILOT_SDK_H

#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "semphr.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ── Version ──────────────────────────────────────────────────────── */
#define SPARKPILOT_SDK_VERSION_MAJOR    1
#define SPARKPILOT_SDK_VERSION_MINOR    0
#define SPARKPILOT_SDK_VERSION_PATCH    0
#define SPARKPILOT_SDK_VERSION_STRING   "1.0.0"

/* ── Configuration Defaults ───────────────────────────────────────── */
#ifndef SPARKPILOT_MQTT_BROKER
#define SPARKPILOT_MQTT_BROKER          "mqtt.sparkpilot.local"
#endif

#ifndef SPARKPILOT_MQTT_PORT
#define SPARKPILOT_MQTT_PORT            1883
#endif

#ifndef SPARKPILOT_TELEMETRY_INTERVAL_MS
#define SPARKPILOT_TELEMETRY_INTERVAL_MS 10000  /* 10 seconds */
#endif

#ifndef SPARKPILOT_CMD_QUEUE_LENGTH
#define SPARKPILOT_CMD_QUEUE_LENGTH     8
#endif

#ifndef SPARKPILOT_DEVICE_ID_MAX_LEN
#define SPARKPILOT_DEVICE_ID_MAX_LEN    32
#endif

/* ── Error Codes ──────────────────────────────────────────────────── */
typedef enum {
    SP_OK = 0,
    SP_ERR_INIT = -1,
    SP_ERR_MQTT_CONNECT = -2,
    SP_ERR_MQTT_PUBLISH = -3,
    SP_ERR_MQTT_SUBSCRIBE = -4,
    SP_ERR_QUEUE_FULL = -5,
    SP_ERR_INVALID_CMD = -6,
    SP_ERR_OTA_FAILED = -7,
    SP_ERR_NO_MEMORY = -8,
} sp_error_t;

/* ── Command Types ────────────────────────────────────────────────── */
typedef enum {
    SP_CMD_OTA_UPDATE = 0,
    SP_CMD_CONFIG_SET,
    SP_CMD_REBOOT,
    SP_CMD_DIAG_DUMP,
    SP_CMD_MODEL_UPDATE,
    SP_CMD_TASK_CONTROL,
    SP_CMD_MAX,
} sp_cmd_type_t;

/* ── Command Message (CBOR-encodable) ─────────────────────────────── */
typedef struct {
    sp_cmd_type_t type;
    char          payload[256];   /* JSON/CBOR payload */
    uint16_t      payload_len;
    uint32_t      timestamp;
    uint8_t       priority;       /* 0 = normal, 1 = high, 2 = critical */
} sp_command_t;

/* ── Telemetry Report ─────────────────────────────────────────────── */
typedef struct {
    uint32_t heap_free_bytes;
    uint32_t stack_watermark_bytes;
    uint8_t  cpu_usage_pct;
    uint32_t uptime_seconds;
    int16_t  temperature_c_x10;   /* Temperature in 0.1°C units */
    int16_t  rssi_dbm;            /* WiFi/BLE signal strength */
    uint32_t inference_count;
    uint16_t inference_latency_ms;
} sp_telemetry_t;

/* ── Configuration ────────────────────────────────────────────────── */
typedef struct {
    char     device_id[SPARKPILOT_DEVICE_ID_MAX_LEN];
    char     broker_host[64];
    uint16_t broker_port;
    uint32_t telemetry_interval_ms;
    uint8_t  mqtt_qos;            /* 0, 1, or 2 */
    uint8_t  enable_tls;          /* 0 = plain, 1 = TLS */

    /* User callbacks */
    void (*on_command)(const sp_command_t *cmd);
    void (*on_connected)(void);
    void (*on_disconnected)(void);

    /* User telemetry hook — called before each telemetry report */
    void (*collect_telemetry)(sp_telemetry_t *report);
} sp_config_t;

/* ── Lifecycle API ────────────────────────────────────────────────── */

/**
 * @brief Initialize SparkPilot SDK with default configuration.
 * @return SP_OK on success, error code on failure.
 */
sp_error_t sparkpilot_init(void);

/**
 * @brief Initialize SparkPilot SDK with custom configuration.
 * @param config Pointer to configuration struct.
 * @return SP_OK on success, error code on failure.
 */
sp_error_t sparkpilot_init_with_config(const sp_config_t *config);

/**
 * @brief Start all SparkPilot FreeRTOS tasks (MQTT, command handler, telemetry).
 * @return SP_OK on success.
 */
sp_error_t sparkpilot_start(void);

/**
 * @brief Stop all SparkPilot tasks gracefully.
 */
void sparkpilot_stop(void);

/**
 * @brief Get SDK version string.
 * @return Version string (e.g., "1.0.0").
 */
const char* sparkpilot_version(void);

/* ── MQTT API ─────────────────────────────────────────────────────── */

/**
 * @brief Connect to MQTT broker.
 * @param broker Broker hostname or IP.
 * @param port Broker port.
 * @return SP_OK on success.
 */
sp_error_t sparkpilot_mqtt_connect(const char *broker, uint16_t port);

/**
 * @brief Publish message to MQTT topic.
 * @param topic MQTT topic string.
 * @param payload Message payload.
 * @param payload_len Payload length in bytes.
 * @param qos MQTT QoS level (0, 1, or 2).
 * @return SP_OK on success.
 */
sp_error_t sparkpilot_mqtt_publish(const char *topic, const void *payload,
                                    uint16_t payload_len, uint8_t qos);

/**
 * @brief Check if MQTT is connected.
 * @return 1 if connected, 0 otherwise.
 */
uint8_t sparkpilot_mqtt_is_connected(void);

/* ── Command API ──────────────────────────────────────────────────── */

/**
 * @brief Queue a command for execution by the command handler task.
 * @param cmd Pointer to command struct.
 * @return SP_OK if queued, SP_ERR_QUEUE_FULL if queue is full.
 */
sp_error_t sparkpilot_queue_command(const sp_command_t *cmd);

/* ── Telemetry API ────────────────────────────────────────────────── */

/**
 * @brief Get current telemetry snapshot.
 * @param report Pointer to telemetry struct to fill.
 */
void sparkpilot_get_telemetry(sp_telemetry_t *report);

/**
 * @brief Force an immediate telemetry report (outside normal interval).
 */
void sparkpilot_send_telemetry_now(void);

#ifdef __cplusplus
}
#endif

#endif /* SPARKPILOT_SDK_H */
