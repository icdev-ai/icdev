/**
 * @file sparkpilot_sdk.c
 * @brief SparkPilot Device SDK implementation.
 *
 * CUI // SP-CTI
 *
 * Three FreeRTOS tasks:
 *   1. vSparkPilotMqttTask  — MQTT client: connect, subscribe, publish, reconnect
 *   2. vSparkPilotCmdTask   — Command handler: dequeue and execute agent commands
 *   3. vSparkPilotTelTask   — Telemetry reporter: periodic health/sensor reports
 *
 * Memory budget: ~8KB flash, ~2KB RAM (stack + queue + state).
 */

#include "sparkpilot_sdk.h"
#include <string.h>
#include <stdio.h>

/* ── Internal State ───────────────────────────────────────────────── */
static sp_config_t     s_config;
static QueueHandle_t   s_cmd_queue   = NULL;
static TaskHandle_t    s_mqtt_task   = NULL;
static TaskHandle_t    s_cmd_task    = NULL;
static TaskHandle_t    s_tel_task    = NULL;
static volatile uint8_t s_mqtt_connected = 0;
static volatile uint8_t s_running        = 0;
static sp_telemetry_t  s_last_telemetry;

/* ── MQTT Topics ──────────────────────────────────────────────────── */
static char s_topic_cmd[80];    /* sparkpilot/devices/{id}/cmd    */
static char s_topic_tel[80];    /* sparkpilot/devices/{id}/telemetry */
static char s_topic_status[80]; /* sparkpilot/devices/{id}/status */

/* ── Forward Declarations ─────────────────────────────────────────── */
static void vSparkPilotMqttTask(void *pvParameters);
static void vSparkPilotCmdTask(void *pvParameters);
static void vSparkPilotTelTask(void *pvParameters);

/* ── Default Telemetry Collector ──────────────────────────────────── */
static void default_collect_telemetry(sp_telemetry_t *report)
{
    report->heap_free_bytes       = (uint32_t)xPortGetFreeHeapSize();
    report->stack_watermark_bytes = (uint32_t)uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t);
    report->cpu_usage_pct         = 0;  /* Requires runtime stats hook */
    report->uptime_seconds        = (uint32_t)(xTaskGetTickCount() / configTICK_RATE_HZ);
    report->temperature_c_x10    = 0;
    report->rssi_dbm             = 0;
    report->inference_count      = 0;
    report->inference_latency_ms = 0;
}

/* ── Lifecycle ────────────────────────────────────────────────────── */

sp_error_t sparkpilot_init(void)
{
    sp_config_t cfg;
    memset(&cfg, 0, sizeof(cfg));
    strncpy(cfg.device_id, "sp-device", SPARKPILOT_DEVICE_ID_MAX_LEN - 1);
    strncpy(cfg.broker_host, SPARKPILOT_MQTT_BROKER, sizeof(cfg.broker_host) - 1);
    cfg.broker_port           = SPARKPILOT_MQTT_PORT;
    cfg.telemetry_interval_ms = SPARKPILOT_TELEMETRY_INTERVAL_MS;
    cfg.mqtt_qos              = 1;
    cfg.enable_tls            = 0;
    cfg.on_command            = NULL;
    cfg.on_connected          = NULL;
    cfg.on_disconnected       = NULL;
    cfg.collect_telemetry     = default_collect_telemetry;
    return sparkpilot_init_with_config(&cfg);
}

sp_error_t sparkpilot_init_with_config(const sp_config_t *config)
{
    if (!config) return SP_ERR_INIT;

    memcpy(&s_config, config, sizeof(sp_config_t));

    /* Build MQTT topics */
    snprintf(s_topic_cmd,    sizeof(s_topic_cmd),    "sparkpilot/devices/%s/cmd",       s_config.device_id);
    snprintf(s_topic_tel,    sizeof(s_topic_tel),    "sparkpilot/devices/%s/telemetry", s_config.device_id);
    snprintf(s_topic_status, sizeof(s_topic_status), "sparkpilot/devices/%s/status",    s_config.device_id);

    /* Create command queue */
    s_cmd_queue = xQueueCreate(SPARKPILOT_CMD_QUEUE_LENGTH, sizeof(sp_command_t));
    if (!s_cmd_queue) return SP_ERR_NO_MEMORY;

    memset(&s_last_telemetry, 0, sizeof(s_last_telemetry));

    return SP_OK;
}

sp_error_t sparkpilot_start(void)
{
    s_running = 1;

    BaseType_t ret;

    ret = xTaskCreate(vSparkPilotMqttTask, "sp_mqtt",
                      configSPARKPILOT_MQTT_TASK_STACK, NULL, 4, &s_mqtt_task);
    if (ret != pdPASS) return SP_ERR_NO_MEMORY;

    ret = xTaskCreate(vSparkPilotCmdTask, "sp_cmd",
                      configSPARKPILOT_CMD_TASK_STACK, NULL, 3, &s_cmd_task);
    if (ret != pdPASS) return SP_ERR_NO_MEMORY;

    ret = xTaskCreate(vSparkPilotTelTask, "sp_tel",
                      configSPARKPILOT_TELEMETRY_TASK_STACK, NULL, 2, &s_tel_task);
    if (ret != pdPASS) return SP_ERR_NO_MEMORY;

    return SP_OK;
}

void sparkpilot_stop(void)
{
    s_running = 0;
    /* Tasks will self-terminate on next loop iteration */
    vTaskDelay(pdMS_TO_TICKS(100));
    if (s_mqtt_task) { vTaskDelete(s_mqtt_task); s_mqtt_task = NULL; }
    if (s_cmd_task)  { vTaskDelete(s_cmd_task);  s_cmd_task  = NULL; }
    if (s_tel_task)  { vTaskDelete(s_tel_task);  s_tel_task  = NULL; }
}

const char* sparkpilot_version(void)
{
    return SPARKPILOT_SDK_VERSION_STRING;
}

/* ── MQTT API ─────────────────────────────────────────────────────── */

sp_error_t sparkpilot_mqtt_connect(const char *broker, uint16_t port)
{
    /*
     * NOTE: Actual MQTT implementation depends on the HAL/platform:
     *   - ESP32: esp_mqtt_client
     *   - STM32: coreMQTT (FreeRTOS)
     *   - Simulator: mosquitto_client or stub
     *
     * This is a reference implementation showing the task structure.
     * The HAL adapter pattern abstracts the actual transport.
     */
    strncpy(s_config.broker_host, broker, sizeof(s_config.broker_host) - 1);
    s_config.broker_port = port;
    /* Connection happens in vSparkPilotMqttTask */
    return SP_OK;
}

uint8_t sparkpilot_mqtt_is_connected(void)
{
    return s_mqtt_connected;
}

sp_error_t sparkpilot_mqtt_publish(const char *topic, const void *payload,
                                    uint16_t payload_len, uint8_t qos)
{
    if (!s_mqtt_connected) return SP_ERR_MQTT_CONNECT;
    /*
     * Platform-specific publish:
     *   hal_mqtt_publish(topic, payload, payload_len, qos);
     */
    (void)topic; (void)payload; (void)payload_len; (void)qos;
    return SP_OK;
}

/* ── Command API ──────────────────────────────────────────────────── */

sp_error_t sparkpilot_queue_command(const sp_command_t *cmd)
{
    if (!cmd || !s_cmd_queue) return SP_ERR_INVALID_CMD;
    if (xQueueSend(s_cmd_queue, cmd, pdMS_TO_TICKS(100)) != pdPASS)
        return SP_ERR_QUEUE_FULL;
    return SP_OK;
}

/* ── Telemetry API ────────────────────────────────────────────────── */

void sparkpilot_get_telemetry(sp_telemetry_t *report)
{
    if (!report) return;
    if (s_config.collect_telemetry)
        s_config.collect_telemetry(report);
    memcpy(&s_last_telemetry, report, sizeof(sp_telemetry_t));
}

void sparkpilot_send_telemetry_now(void)
{
    if (s_tel_task)
        xTaskNotifyGive(s_tel_task);
}

/* ── Task Implementations ─────────────────────────────────────────── */

/**
 * MQTT Client Task
 * - Connects to broker with exponential backoff on failure
 * - Subscribes to command topic
 * - Routes incoming messages to command queue
 * - Publishes status on connect/disconnect
 */
static void vSparkPilotMqttTask(void *pvParameters)
{
    (void)pvParameters;
    uint32_t backoff_ms = 1000;
    const uint32_t max_backoff_ms = 60000;

    while (s_running)
    {
        if (!s_mqtt_connected)
        {
            /*
             * hal_mqtt_connect(s_config.broker_host, s_config.broker_port);
             * On success: s_mqtt_connected = 1; backoff_ms = 1000;
             * On failure: double backoff
             */
            s_mqtt_connected = 1;  /* Stub: always succeeds in reference impl */
            backoff_ms = 1000;

            if (s_config.on_connected)
                s_config.on_connected();

            /* Subscribe to command topic */
            /* hal_mqtt_subscribe(s_topic_cmd, s_config.mqtt_qos); */

            /* Publish online status */
            const char *status = "{\"status\":\"online\"}";
            sparkpilot_mqtt_publish(s_topic_status, status,
                                   (uint16_t)strlen(status), 1);
        }

        /* Process incoming MQTT messages (platform-specific) */
        /* hal_mqtt_process(100); */
        vTaskDelay(pdMS_TO_TICKS(100));
    }

    vTaskDelete(NULL);
}

/**
 * Command Handler Task
 * - Dequeues commands from s_cmd_queue
 * - Dispatches to appropriate handler
 * - Reports result back via MQTT
 */
static void vSparkPilotCmdTask(void *pvParameters)
{
    (void)pvParameters;
    sp_command_t cmd;

    while (s_running)
    {
        if (xQueueReceive(s_cmd_queue, &cmd, pdMS_TO_TICKS(1000)) == pdPASS)
        {
            /* User callback first */
            if (s_config.on_command)
                s_config.on_command(&cmd);

            /* Built-in command handling */
            switch (cmd.type)
            {
                case SP_CMD_OTA_UPDATE:
                    /* Trigger OTA download + flash via MCUboot */
                    break;
                case SP_CMD_CONFIG_SET:
                    /* Apply runtime configuration change */
                    break;
                case SP_CMD_REBOOT:
                    /* hal_system_reset(); */
                    break;
                case SP_CMD_DIAG_DUMP:
                    /* Collect diagnostics and publish to MQTT */
                    sparkpilot_send_telemetry_now();
                    break;
                case SP_CMD_MODEL_UPDATE:
                    /* Download and load new TFLite model */
                    break;
                case SP_CMD_TASK_CONTROL:
                    /* Suspend/resume/delete FreeRTOS tasks */
                    break;
                default:
                    break;
            }
        }
    }

    vTaskDelete(NULL);
}

/**
 * Telemetry Reporter Task
 * - Collects system metrics at configurable intervals
 * - Publishes CBOR-encoded telemetry to MQTT
 * - Supports immediate report via task notification
 */
static void vSparkPilotTelTask(void *pvParameters)
{
    (void)pvParameters;
    sp_telemetry_t report;
    char buf[256];

    while (s_running)
    {
        /* Wait for interval or immediate notification */
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(s_config.telemetry_interval_ms));

        sparkpilot_get_telemetry(&report);

        /* Format as JSON (CBOR in production) */
        int len = snprintf(buf, sizeof(buf),
            "{\"heap\":%lu,\"stack\":%lu,\"cpu\":%u,\"up\":%lu,"
            "\"temp\":%d,\"rssi\":%d,\"infer\":%lu,\"lat\":%u}",
            (unsigned long)report.heap_free_bytes,
            (unsigned long)report.stack_watermark_bytes,
            report.cpu_usage_pct,
            (unsigned long)report.uptime_seconds,
            report.temperature_c_x10,
            report.rssi_dbm,
            (unsigned long)report.inference_count,
            report.inference_latency_ms);

        if (s_mqtt_connected && len > 0)
        {
            sparkpilot_mqtt_publish(s_topic_tel, buf, (uint16_t)len, 0);
        }
    }

    vTaskDelete(NULL);
}
