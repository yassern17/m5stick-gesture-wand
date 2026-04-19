/*
 * M5StickC Plus — ClaudeWand firmware
 *
 * Bidirectional BLE bridge between the watch and the laptop MCP server.
 *
 * Laptop → Watch (write characteristic):
 *   S:<text>   — set status text on display
 *   N:<text>   — notification: buzz + flash + show text for 3 s
 *   A:<text>   — ask yes/no: show question, wait for BTN_A / BTN_B
 *   B:<ms>     — buzz for <ms> milliseconds
 *   C          — clear / return to idle
 *
 * Watch → Laptop (notify characteristic):
 *   APPROVE          — BTN_A pressed while in ASKING state
 *   REJECT           — BTN_B pressed while in ASKING state
 *   BTN_A / BTN_B    — buttons pressed in IDLE state
 *   SHAKE / FLICK_FORWARD / FLICK_BACK / ROTATE_CW / ROTATE_CCW
 *   TILT_UP / TILT_DOWN / TILT_LEFT / TILT_RIGHT
 *
 * Required library: M5StickCPlus (Arduino Library Manager)
 */

#include <M5StickCPlus.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// ── BLE identifiers ───────────────────────────────────────────────────────────
#define DEVICE_NAME     "M5ClaudeWand"
#define SERVICE_UUID    "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define EVENT_CHAR_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"  // watch → laptop
#define CMD_CHAR_UUID   "beb5483e-36e1-4688-b7f5-ea07361b26a9"  // laptop → watch

// ── Gesture thresholds ────────────────────────────────────────────────────────
#define TILT_ANGLE_THRESHOLD  28.0f
#define SHAKE_ACCEL_THRESHOLD  2.2f
#define FLICK_GYRO_THRESHOLD  280.0f
#define GESTURE_COOLDOWN_MS    400
#define TILT_FIRST_DELAY_MS    600
#define TILT_REPEAT_MS         180

// ── BLE handles ───────────────────────────────────────────────────────────────
BLEServer*         pServer    = nullptr;
BLECharacteristic* pEventChar = nullptr;
BLECharacteristic* pCmdChar   = nullptr;
bool deviceConnected = false;

// ── Wand state machine ────────────────────────────────────────────────────────
enum WandState { IDLE, ASKING, NOTIFYING };
WandState     state        = IDLE;
String        statusText   = "Waiting...";
String        notifyText   = "";
String        askText      = "";
bool          displayDirty = true;
unsigned long notifyUntil  = 0;

// ── IMU state ─────────────────────────────────────────────────────────────────
float accX, accY, accZ, gyroX, gyroY, gyroZ;
String        currentTilt            = "";
unsigned long tiltStartTime          = 0;
unsigned long lastTiltSentTime       = 0;
unsigned long lastInstantGestureTime = 0;

// ── Helpers ───────────────────────────────────────────────────────────────────

void buzz(int ms) {
    if (ms <= 0) return;
    M5.Beep.tone(2000, ms);
    delay(ms);
    M5.Beep.mute();
}

void sendEvent(const char* ev) {
    if (!deviceConnected || !pEventChar) return;
    pEventChar->setValue(ev);
    pEventChar->notify();
}

// ── Display ───────────────────────────────────────────────────────────────────
// Landscape: 240 × 135 px

void drawDisplay() {
    M5.Lcd.fillScreen(TFT_BLACK);

    if (!deviceConnected) {
        M5.Lcd.setTextColor(TFT_ORANGE);
        M5.Lcd.setTextSize(2);
        M5.Lcd.setCursor(8, 45);
        M5.Lcd.print("Advertising...");
        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(TFT_DARKGREY);
        M5.Lcd.setCursor(8, 80);
        M5.Lcd.print(DEVICE_NAME);
        displayDirty = false;
        return;
    }

    switch (state) {
    case IDLE:
        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(TFT_GREEN);
        M5.Lcd.setCursor(5, 4);
        M5.Lcd.print("CLAUDE WAND");
        M5.Lcd.drawFastHLine(0, 14, 240, TFT_DARKGREY);

        M5.Lcd.setTextSize(2);
        M5.Lcd.setTextColor(TFT_WHITE);
        M5.Lcd.setCursor(5, 22);
        M5.Lcd.print(statusText.substring(0, 17));
        break;

    case NOTIFYING:
        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(TFT_YELLOW);
        M5.Lcd.setCursor(5, 4);
        M5.Lcd.print("NOTIFICATION");
        M5.Lcd.drawFastHLine(0, 14, 240, TFT_YELLOW);

        M5.Lcd.setTextSize(2);
        M5.Lcd.setTextColor(TFT_WHITE);
        M5.Lcd.setCursor(5, 22);
        M5.Lcd.print(notifyText.substring(0, 17));
        break;

    case ASKING:
        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(TFT_CYAN);
        M5.Lcd.setCursor(5, 4);
        M5.Lcd.print("APPROVE?");
        M5.Lcd.drawFastHLine(0, 14, 240, TFT_CYAN);

        M5.Lcd.setTextSize(2);
        M5.Lcd.setTextColor(TFT_WHITE);
        M5.Lcd.setCursor(5, 22);
        M5.Lcd.print(askText.substring(0, 17));

        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(TFT_GREEN);
        M5.Lcd.setCursor(5, 118);
        M5.Lcd.print("[A] YES");
        M5.Lcd.setTextColor(TFT_RED);
        M5.Lcd.setCursor(170, 118);
        M5.Lcd.print("[B] NO");
        break;
    }

    displayDirty = false;
}

// ── BLE command callback (laptop → watch) ─────────────────────────────────────

class CmdCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic* pChar) override {
        std::string raw = pChar->getValue();
        if (raw.size() < 1) return;

        char   cmd     = raw[0];
        String payload = (raw.size() >= 2) ? String(raw.substr(2).c_str()) : "";

        switch (cmd) {
        case 'S':
            statusText = payload;
            if (state == IDLE) displayDirty = true;
            break;

        case 'N':
            notifyText = payload;
            notifyUntil = millis() + 3000;
            state = NOTIFYING;
            displayDirty = true;
            buzz(200);
            for (int i = 0; i < 3; i++) {
                M5.Axp.SetLed(true);  delay(80);
                M5.Axp.SetLed(false); delay(80);
            }
            break;

        case 'A':
            askText = payload;
            state = ASKING;
            displayDirty = true;
            buzz(100);
            break;

        case 'B':
            buzz(payload.toInt());
            break;

        case 'C':
            state = IDLE;
            statusText = "Ready";
            displayDirty = true;
            break;
        }
    }
};

// ── BLE server callbacks ──────────────────────────────────────────────────────

class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer*) override {
        deviceConnected = true;
        statusText = "Connected";
        state = IDLE;
        displayDirty = true;
    }
    void onDisconnect(BLEServer* pSvr) override {
        deviceConnected = false;
        state = IDLE;
        displayDirty = true;
        pSvr->getAdvertising()->start();
    }
};

// ── Gesture detection (IDLE only, ~100 Hz) ────────────────────────────────────

void detectGestures() {
    unsigned long now = millis();

    M5.IMU.getAccelData(&accX, &accY, &accZ);
    M5.IMU.getGyroData(&gyroX, &gyroY, &gyroZ);

    float pitch    = atan2(accY, sqrtf(accX*accX + accZ*accZ)) * 180.0f / PI;
    float roll     = atan2(-accX, accZ)                        * 180.0f / PI;
    float accelMag = sqrtf(accX*accX + accY*accY + accZ*accZ);
    float gyroMag  = sqrtf(gyroX*gyroX + gyroY*gyroY + gyroZ*gyroZ);

    // One-shot gestures
    if (now - lastInstantGestureTime >= GESTURE_COOLDOWN_MS) {
        if (accelMag > SHAKE_ACCEL_THRESHOLD) {
            sendEvent("SHAKE");
            lastInstantGestureTime = now;
            currentTilt = "";
            return;
        }
        if (gyroMag > FLICK_GYRO_THRESHOLD) {
            float ax = abs(gyroX), ay = abs(gyroY), az = abs(gyroZ);
            if (ax >= ay && ax >= az)
                sendEvent(gyroX > 0 ? "FLICK_FORWARD" : "FLICK_BACK");
            else if (az >= ax && az >= ay)
                sendEvent(gyroZ > 0 ? "ROTATE_CW" : "ROTATE_CCW");
            lastInstantGestureTime = now;
            currentTilt = "";
            return;
        }
    }

    // Sustained tilt gestures
    String detected = "";
    if      (pitch >  TILT_ANGLE_THRESHOLD) detected = "TILT_UP";
    else if (pitch < -TILT_ANGLE_THRESHOLD) detected = "TILT_DOWN";
    else if (roll  >  TILT_ANGLE_THRESHOLD) detected = "TILT_RIGHT";
    else if (roll  < -TILT_ANGLE_THRESHOLD) detected = "TILT_LEFT";

    if (detected.length() > 0) {
        if (detected != currentTilt) {
            currentTilt      = detected;
            tiltStartTime    = now;
            lastTiltSentTime = now;
            sendEvent(currentTilt.c_str());
        } else {
            unsigned long held          = now - tiltStartTime;
            unsigned long sinceLastSent = now - lastTiltSentTime;
            if (held >= TILT_FIRST_DELAY_MS && sinceLastSent >= TILT_REPEAT_MS) {
                sendEvent(currentTilt.c_str());
                lastTiltSentTime = now;
            }
        }
    } else {
        currentTilt = "";
    }
}

// ── Setup ─────────────────────────────────────────────────────────────────────

void setup() {
    M5.begin();
    M5.IMU.Init();
    M5.Axp.SetLed(false);
    M5.Lcd.setRotation(3);
    M5.Lcd.fillScreen(TFT_BLACK);

    BLEDevice::init(DEVICE_NAME);
    pServer = BLEDevice::createServer();
    pServer->setCallbacks(new ServerCallbacks());

    BLEService* pService = pServer->createService(SERVICE_UUID);

    pEventChar = pService->createCharacteristic(
        EVENT_CHAR_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );
    pEventChar->addDescriptor(new BLE2902());

    pCmdChar = pService->createCharacteristic(
        CMD_CHAR_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
    );
    pCmdChar->setCallbacks(new CmdCallbacks());

    pService->start();

    BLEAdvertising* pAdv = BLEDevice::getAdvertising();
    pAdv->addServiceUUID(SERVICE_UUID);
    pAdv->setScanResponse(true);
    pAdv->setMinPreferred(0x06);
    BLEDevice::startAdvertising();

    drawDisplay();
}

// ── Loop ──────────────────────────────────────────────────────────────────────

void loop() {
    M5.update();

    // Auto-clear notification after timeout
    if (state == NOTIFYING && millis() > notifyUntil) {
        state = IDLE;
        displayDirty = true;
    }

    // State-aware button handling
    if (state == ASKING) {
        if (M5.BtnA.wasPressed()) {
            sendEvent("APPROVE");
            statusText = "Approved";
            state = IDLE;
            displayDirty = true;
        } else if (M5.BtnB.wasPressed()) {
            sendEvent("REJECT");
            statusText = "Rejected";
            state = IDLE;
            displayDirty = true;
        }
    } else {
        if (M5.BtnA.wasPressed()) sendEvent("BTN_A");
        if (M5.BtnB.wasPressed()) sendEvent("BTN_B");
        if (deviceConnected) detectGestures();
    }

    if (displayDirty) drawDisplay();

    delay(10);
}
