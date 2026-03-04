# E2E Test: SparkPilot Dashboard

## Prerequisites
- SparkPilot dashboard running on port 5050: `python tools/dashboard/app.py --port 5050`
- Database initialized: `python tools/db/init_sparkpilot_db.py`
- Missions seeded: `python tools/missions/mission_engine.py --seed --json`
- Peripherals seeded: `python tools/simulator/sim_runner.py --seed --json`

## Test Steps

### 1. Home Dashboard
1. Navigate to `http://127.0.0.1:5050/`
2. Verify page title is "SparkPilot — Dashboard"
3. Verify CUI banner "CUI // SP-CTI" is visible at top and bottom
4. Verify stat grid shows: Devices, Online, Firmware Builds, Missions (≥7), Missions Completed, ML Models, Sim Sessions, Crash Dumps, OTA Pending, NL Commands
5. Verify "Natural Language Command" input and "Generate" button are present
6. Take screenshot: `sparkpilot-home-desktop.png`

### 2. NL-to-Firmware Command
1. On the Home page, type "Blink an LED every 2 seconds" into the NL command input
2. Click "Generate"
3. Verify a code block appears with FreeRTOS C code containing:
   - `#include "FreeRTOS.h"`
   - `vBlinkTask`
   - `vTaskDelay(pdMS_TO_TICKS(2000))`
4. Take screenshot: `sparkpilot-nl-command.png`

### 3. Missions Page
1. Navigate to `/missions`
2. Verify page title is "SparkPilot — Missions"
3. Verify 7 mission cards are displayed:
   - Mission 1: Hello, LED! (beginner, 100 XP)
   - Mission 2: Sensor Explorer (beginner, 150 XP)
   - Mission 3: WiFi Wrangler (intermediate, 200 XP)
   - Mission 4: MQTT Messenger (intermediate, 250 XP)
   - Mission 5: AI Detective (advanced, 400 XP)
   - Mission 6: Silicon Upgrade (advanced, 500 XP, Hardware Required badge)
   - Mission 7: Fleet Commander (expert, 600 XP)
4. Verify each card has: difficulty badge, XP reward, estimated time, objectives list, "Start Mission" button
5. Take screenshot: `sparkpilot-missions.png`

### 4. Simulator Page
1. Navigate to `/simulator`
2. Verify page title is "SparkPilot — Simulator"
3. Verify "Launch Simulator" button is present
4. Verify 7 virtual peripherals are displayed (Accelerometer, Push Button, Red LED, Green LED, OLED Display, Potentiometer, Temperature Sensor)
5. Click "Launch Simulator" button
6. Accept the alert dialog confirming session creation
7. Verify new session appears in the sessions table with status "Running"
8. Take screenshot: `sparkpilot-simulator.png`

### 5. Fleet Page
1. Navigate to `/devices`
2. Verify page title is "SparkPilot — Fleet"
3. Verify stat grid shows Total Devices, Online, Offline counts
4. Verify register form with device name input, board dropdown (Simulator, ESP32-S3, STM32F407, nRF52840, RPi Pico), and Register button
5. Enter "Test Device" as name, select "ESP32-S3" from dropdown, click Register
6. Accept the alert confirming registration
7. Verify device appears in table with board "esp32-s3" and status "registered"
8. Take screenshot: `sparkpilot-fleet.png`

### 6. Firmware Page
1. Navigate to `/firmware`
2. Verify page title is "SparkPilot — Firmware"
3. Verify "Firmware Builds" and "OTA Update Log" sections are present
4. Verify empty state messages when no data exists
5. Take screenshot: `sparkpilot-firmware.png`

### 7. Edge AI Page
1. Navigate to `/edge-ai`
2. Verify page title is "SparkPilot — Edge AI"
3. Verify "Models" and "Inference Telemetry" sections are present
4. Verify empty state messages when no models registered
5. Take screenshot: `sparkpilot-edge-ai.png`

### 8. Self-Heal Page
1. Navigate to `/crashes`
2. Verify page title is "SparkPilot — Self-Heal"
3. Verify "Crash Dump Log & Self-Healing" heading
4. Verify empty state: "No crash dumps recorded. That's good news!"
5. Take screenshot: `sparkpilot-crashes.png`

### 9. Responsive Design
1. Resize viewport to 375x812 (mobile)
2. Navigate to `/` and verify stat cards stack to single column
3. Take screenshot: `sparkpilot-home-mobile.png`
4. Resize viewport to 768x1024 (tablet)
5. Verify stat cards show 2-column grid
6. Take screenshot: `sparkpilot-home-tablet.png`
7. Resize back to 1280x800 (desktop)

### 10. Health Check
1. Navigate to `/health`
2. Verify JSON response contains `{"status": "healthy", "app": "sparkpilot", "version": "1.0.0"}`

## Pass Criteria
- All 7 pages load without errors (no 500s, no tracebacks)
- CUI banner visible on every page (top and bottom)
- NL-to-firmware generates valid FreeRTOS C code
- Device registration works end-to-end (form → API → table update)
- Simulator session creation works end-to-end
- All mission cards display with correct data
- Responsive breakpoints work (mobile: 1-col, tablet: 2-col, desktop: 5-col)
- Console has no errors (favicon 404 is acceptable)
