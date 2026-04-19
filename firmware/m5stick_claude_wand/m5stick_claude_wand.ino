/*
 * M5StickC Plus — ClaudeWand firmware v2
 *
 * Laptop → Watch (write characteristic):
 *   S:<text>        — set status text
 *   N:<text>        — notification: buzz + flash + show text for 3 s
 *   A:<text>        — ask yes/no: show question, wait for BTN_A / BTN_B
 *   B:<ms>          — buzz for <ms> milliseconds
 *   T:<unix_ts>     — sync RTC time (seconds since epoch)
 *   C               — clear / return to idle
 *
 * Watch → Laptop (notify characteristic):
 *   APPROVE / REJECT                    — response to A: command
 *   BTN_A / BTN_B                       — buttons in IDLE state
 *   INTERRUPT                           — user selected "Interrupt" from menu
 *   SHAKE / FLICK_FORWARD / FLICK_BACK / ROTATE_CW / ROTATE_CCW
 *   TILT_UP / TILT_DOWN / TILT_LEFT / TILT_RIGHT
 */

#include <M5StickCPlus.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// ── BLE identifiers ───────────────────────────────────────────────────────────
#define DEVICE_NAME     "M5ClaudeWand"
#define SERVICE_UUID    "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define EVENT_CHAR_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"
#define CMD_CHAR_UUID   "beb5483e-36e1-4688-b7f5-ea07361b26a9"

// ── Hardware pins ─────────────────────────────────────────────────────────────
#define LED_PIN    10
#define BUZZER_PIN  2

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
enum WandState { IDLE, ASKING, NOTIFYING, MENU };
WandState     state        = IDLE;
String        statusText   = "Waiting...";
String        notifyText   = "";
String        askText      = "";
bool          displayDirty = true;
unsigned long notifyUntil  = 0;
String        lastMessage  = "";

// ── Menu ──────────────────────────────────────────────────────────────────────
const char* MENU_ITEMS[] = { "View Status", "Last Message", "Interrupt Claude", "Disconnect" };
const int   MENU_COUNT   = 4;
int         menuIndex    = 0;
WandState   menuReturnState = IDLE;

// ── Long-press tracking ───────────────────────────────────────────────────────
unsigned long btnADownAt   = 0;
bool          btnALongFired = false;
#define LONG_PRESS_MS 800

// ── Time ──────────────────────────────────────────────────────────────────────
bool          timeSet      = false;
unsigned long timeBase     = 0;   // millis() when time was synced
unsigned long epochBase    = 0;   // unix timestamp at sync point

// ── Progress indicator ────────────────────────────────────────────────────────
int     progressStep  = 0;
int     progressTotal = 0;
String  progressLabel = "";
bool    showProgress  = false;

// ── Pending actions ───────────────────────────────────────────────────────────
volatile int  pendingBuzzMs      = 0;
volatile int  pendingBuzzPattern = 0;  // 1=done, 2=error, 3=warn
volatile int  pendingBlinks      = 0;
volatile bool pendingRestartAdv  = false;

// ── IMU state ─────────────────────────────────────────────────────────────────
float accX, accY, accZ, gyroX, gyroY, gyroZ;
String        currentTilt            = "";
unsigned long tiltStartTime          = 0;
unsigned long lastTiltSentTime       = 0;
unsigned long lastInstantGestureTime = 0;

// ── Colours (RGB565) ──────────────────────────────────────────────────────────
#define COL_GREEN   0x07E0
#define COL_YELLOW  0xFFE0
#define COL_CYAN    0x07FF
#define COL_RED     0xF800
#define COL_ORANGE  0xFD20
#define COL_PURPLE  0x781F
#define COL_DGREY   0x4208
#define COL_WHITE   0xFFFF
#define COL_BLACK   0x0000

// ── Hardware helpers ──────────────────────────────────────────────────────────

void ledSet(bool on) { digitalWrite(LED_PIN, on ? LOW : HIGH); }

void doBuzz(int ms) {
    if (ms <= 0) return;
    ledcWriteTone(BUZZER_PIN, 2000); delay(ms); ledcWriteTone(BUZZER_PIN, 0);
}

void doBlinkLED(int times) {
    for (int i = 0; i < times; i++) { ledSet(true); delay(80); ledSet(false); delay(80); }
}

void sendEvent(const char* ev) {
    if (!deviceConnected || !pEventChar) return;
    pEventChar->setValue(ev);
    pEventChar->notify();
}

// ── Time helpers ──────────────────────────────────────────────────────────────

unsigned long nowEpoch() {
    if (!timeSet) return 0;
    return epochBase + (millis() - timeBase) / 1000;
}

String formatTime() {
    if (!timeSet) return "--:--";
    unsigned long t = nowEpoch();
    int h = (t % 86400) / 3600;
    int m = (t % 3600) / 60;
    char buf[6];
    snprintf(buf, sizeof(buf), "%02d:%02d", h, m);
    return String(buf);
}

// ── Battery ───────────────────────────────────────────────────────────────────

int batteryPercent() {
    float v = M5.Axp.GetBatVoltage();
    // 3.0 V = 0%, 4.2 V = 100%
    int pct = (int)((v - 3.0f) / 1.2f * 100.0f);
    return constrain(pct, 0, 100);
}

// ── Display helpers ───────────────────────────────────────────────────────────
// Landscape 240 × 135

// Draw text with word-wrap. sz=1→6px wide, sz=2→12px wide.
void drawWrapped(const String& text, int x, int y, uint8_t sz, int maxLines) {
    M5.Lcd.setTextSize(sz);
    int charW        = sz * 6;
    int lineH        = sz * 10;
    int charsPerLine = (240 - x - 4) / charW;
    int pos = 0, len = text.length();
    for (int line = 0; line < maxLines && pos < len; line++) {
        int end = min(pos + charsPerLine, len);
        if (end < len) {
            int brk = -1;
            for (int i = end - 1; i > pos; i--) {
                if (text[i] == ' ') { brk = i; break; }
            }
            if (brk > pos) end = brk;
        }
        M5.Lcd.setCursor(x, y + line * lineH);
        M5.Lcd.print(text.substring(pos, end));
        pos = end;
        while (pos < len && text[pos] == ' ') pos++;
    }
}

// ── Display ───────────────────────────────────────────────────────────────────

void drawHeader() {
    // Time (left)
    M5.Lcd.setTextSize(1);
    M5.Lcd.setTextColor(COL_WHITE);
    M5.Lcd.setCursor(5, 4);
    M5.Lcd.print(formatTime());

    // Battery bar + % (right)
    int pct = batteryPercent();
    uint16_t batCol = pct > 40 ? COL_GREEN : (pct > 15 ? COL_YELLOW : COL_RED);
    char buf[8]; snprintf(buf, sizeof(buf), "%d%%", pct);
    int bw = 30; // bar width
    int bh = 7;
    int bx = 240 - bw - 36;
    int by = 4;
    M5.Lcd.drawRect(bx, by, bw, bh, COL_DGREY);
    M5.Lcd.fillRect(bx+1, by+1, (bw-2)*pct/100, bh-2, batCol);
    M5.Lcd.setTextColor(batCol);
    M5.Lcd.setCursor(240 - 30, 4);
    M5.Lcd.print(buf);

    M5.Lcd.drawFastHLine(0, 14, 240, COL_DGREY);
}

void drawDisplay() {
    M5.Lcd.fillScreen(COL_BLACK);

    if (!deviceConnected) {
        drawHeader();
        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(COL_ORANGE);
        M5.Lcd.setCursor(5, 22);
        M5.Lcd.print("Advertising...");
        M5.Lcd.setTextColor(COL_DGREY);
        M5.Lcd.setCursor(5, 38);
        M5.Lcd.print(DEVICE_NAME);
        displayDirty = false;
        return;
    }

    switch (state) {

    case IDLE: {
        drawHeader();
        M5.Lcd.fillCircle(10, 26, 4, COL_GREEN);
        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(COL_GREEN);
        M5.Lcd.setCursor(18, 22);
        M5.Lcd.print("CLAUDE WAND");
        M5.Lcd.drawFastHLine(0, 33, 240, COL_DGREY);

        if (showProgress) {
            M5.Lcd.setTextColor(COL_WHITE);
            drawWrapped(progressLabel, 5, 38, 1, 1);
            // Progress bar
            int barX = 5, barY = 52, barW = 190, barH = 10;
            int filled = (progressTotal > 0)
                ? max(0, (barW - 2) * progressStep / progressTotal) : 0;
            M5.Lcd.drawRect(barX, barY, barW, barH, COL_DGREY);
            M5.Lcd.fillRect(barX + 1, barY + 1, filled, barH - 2, COL_GREEN);
            // Step counter right of bar
            char stepBuf[10];
            snprintf(stepBuf, sizeof(stepBuf), "%d/%d", progressStep, progressTotal);
            M5.Lcd.setTextSize(1);
            M5.Lcd.setTextColor(COL_GREEN);
            M5.Lcd.setCursor(200, 54);
            M5.Lcd.print(stepBuf);
            // Current status below bar (word-wrapped, 2 lines)
            M5.Lcd.setTextColor(COL_DGREY);
            drawWrapped(statusText, 5, 68, 1, 2);
        } else {
            M5.Lcd.setTextColor(COL_WHITE);
            drawWrapped(statusText, 5, 40, 2, 2);
        }

        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(COL_DGREY);
        M5.Lcd.setCursor(5, 122);
        M5.Lcd.print("[B] Menu");
        break;
    }

    case NOTIFYING:
        drawHeader();
        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(COL_YELLOW);
        M5.Lcd.setCursor(5, 22);
        M5.Lcd.print("NOTIFICATION");
        M5.Lcd.drawFastHLine(0, 33, 240, COL_YELLOW);
        M5.Lcd.setTextColor(COL_WHITE);
        drawWrapped(notifyText, 5, 40, 2, 2);
        break;

    case ASKING:
        drawHeader();
        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(COL_CYAN);
        M5.Lcd.setCursor(5, 22);
        M5.Lcd.print("APPROVE?");
        M5.Lcd.drawFastHLine(0, 33, 240, COL_CYAN);
        M5.Lcd.setTextColor(COL_WHITE);
        drawWrapped(askText, 5, 40, 2, 2);
        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(COL_GREEN);
        M5.Lcd.setCursor(5, 122);
        M5.Lcd.print("[A] YES");
        M5.Lcd.setTextColor(COL_RED);
        M5.Lcd.setCursor(170, 122);
        M5.Lcd.print("[B] NO");
        break;

    case MENU: {
        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(COL_PURPLE);
        M5.Lcd.setCursor(5, 4);
        M5.Lcd.print("MENU");
        M5.Lcd.drawFastHLine(0, 14, 240, COL_PURPLE);
        for (int i = 0; i < MENU_COUNT; i++) {
            uint16_t col = (i == menuIndex) ? COL_WHITE : COL_DGREY;
            M5.Lcd.setTextColor(col);
            M5.Lcd.setCursor(10, 20 + i * 22);
            M5.Lcd.print(i == menuIndex ? "> " : "  ");
            M5.Lcd.print(MENU_ITEMS[i]);
        }
        M5.Lcd.setTextSize(1);
        M5.Lcd.setTextColor(COL_GREEN);
        M5.Lcd.setCursor(5, 122);
        M5.Lcd.print("[A] Select");
        M5.Lcd.setTextColor(COL_RED);
        M5.Lcd.setCursor(140, 122);
        M5.Lcd.print("[B] Next/Back");
        break;
    }
    }

    displayDirty = false;
}

// ── BLE command callback ──────────────────────────────────────────────────────

class CmdCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic* pChar) override {
        String raw = pChar->getValue();
        if (raw.length() < 1) return;
        char   cmd     = raw[0];
        String payload = (raw.length() >= 2) ? raw.substring(2) : "";

        switch (cmd) {
        case 'S':
            statusText = payload;
            if (state == IDLE) displayDirty = true;
            break;
        case 'N':
            notifyText   = payload;
            lastMessage  = payload;
            notifyUntil  = millis() + 3000;
            state        = NOTIFYING;
            displayDirty = true;
            pendingBuzzMs = 200;
            pendingBlinks = 3;
            break;
        case 'A':
            askText       = payload;
            state         = ASKING;
            displayDirty  = true;
            pendingBuzzMs = 200;
            pendingBlinks = 3;
            break;
        case 'B':
            if      (payload == "done")  pendingBuzzPattern = 1;
            else if (payload == "error") pendingBuzzPattern = 2;
            else if (payload == "warn")  pendingBuzzPattern = 3;
            else                         pendingBuzzMs = payload.toInt();
            break;
        case 'T': {
            // Sync time: T:<unix_seconds>
            unsigned long ts = (unsigned long)payload.toInt();
            if (ts > 1000000000UL) {
                epochBase = ts;
                timeBase  = millis();
                timeSet   = true;
                displayDirty = true;
            }
            break;
        }
        case 'P': {
            // P:<step>/<total>:<label>
            int slashPos = payload.indexOf('/');
            int colonPos = payload.indexOf(':', slashPos + 1);
            if (slashPos > 0 && colonPos > slashPos) {
                progressStep  = payload.substring(0, slashPos).toInt();
                progressTotal = payload.substring(slashPos + 1, colonPos).toInt();
                progressLabel = payload.substring(colonPos + 1);
                showProgress  = true;
                if (state == IDLE) displayDirty = true;
            }
            break;
        }
        case 'C':
            state        = IDLE;
            statusText   = "Ready";
            showProgress = false;
            displayDirty = true;
            break;
        }
    }
};

// ── BLE server callbacks ──────────────────────────────────────────────────────

class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer*) override {
        deviceConnected = true;
        statusText      = "Connected";
        state           = IDLE;
        displayDirty    = true;
    }
    void onDisconnect(BLEServer*) override {
        deviceConnected   = false;
        state             = IDLE;
        displayDirty      = true;
        pendingRestartAdv = true;
    }
};

// ── Gesture detection ─────────────────────────────────────────────────────────

void detectGestures() {
    unsigned long now = millis();
    M5.IMU.getAccelData(&accX, &accY, &accZ);
    M5.IMU.getGyroData(&gyroX, &gyroY, &gyroZ);

    float pitch    = atan2(accY, sqrtf(accX*accX + accZ*accZ)) * 180.0f / PI;
    float roll     = atan2(-accX, accZ)                        * 180.0f / PI;
    float accelMag = sqrtf(accX*accX + accY*accY + accZ*accZ);
    float gyroMag  = sqrtf(gyroX*gyroX + gyroY*gyroY + gyroZ*gyroZ);

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
            unsigned long held = now - tiltStartTime;
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

    pinMode(LED_PIN, OUTPUT);
    ledSet(false);

    ledcAttach(BUZZER_PIN, 2000, 8);
    ledcWriteTone(BUZZER_PIN, 0);

    M5.Lcd.setRotation(3);
    M5.Lcd.fillScreen(COL_BLACK);

    BLEDevice::init(DEVICE_NAME);
    pServer = BLEDevice::createServer();
    pServer->setCallbacks(new ServerCallbacks());

    BLEService* pService = pServer->createService(SERVICE_UUID);

    pEventChar = pService->createCharacteristic(
        EVENT_CHAR_UUID, BLECharacteristic::PROPERTY_NOTIFY);
    pEventChar->addDescriptor(new BLE2902());

    pCmdChar = pService->createCharacteristic(
        CMD_CHAR_UUID,
        BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
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

unsigned long lastClockUpdate   = 0;
bool          batteryAlertSent  = false;

void loop() {
    M5.update();

    // Pending BLE-safe actions
    if (pendingRestartAdv) {
        pendingRestartAdv = false;
        delay(300);
        BLEDevice::startAdvertising();
    }
    if (pendingBuzzMs > 0) {
        int ms = pendingBuzzMs; pendingBuzzMs = 0; doBuzz(ms);
    }
    if (pendingBuzzPattern > 0) {
        int pat = pendingBuzzPattern; pendingBuzzPattern = 0;
        switch (pat) {
        case 1: doBuzz(80); delay(80); doBuzz(80); break;                        // done
        case 2: doBuzz(60); delay(60); doBuzz(60); delay(60); doBuzz(60); break; // error
        case 3: doBuzz(400); break;                                               // warn
        }
    }
    if (pendingBlinks > 0) {
        int n = pendingBlinks; pendingBlinks = 0; doBlinkLED(n);
    }

    // Auto-clear notification
    if (state == NOTIFYING && millis() > notifyUntil) {
        state = IDLE;
        displayDirty = true;
    }

    // Refresh clock every 30 s + battery low alert
    if (millis() - lastClockUpdate > 30000) {
        lastClockUpdate = millis();
        if (state == IDLE) displayDirty = true;
        // Send BATTERY_LOW once per low-threshold crossing; reset above 20%
        if (deviceConnected) {
            int pct = batteryPercent();
            if (pct < 15 && !batteryAlertSent) {
                sendEvent("BATTERY_LOW");
                batteryAlertSent = true;
            } else if (pct >= 20) {
                batteryAlertSent = false;
            }
        }
    }

    // ── Button handling ───────────────────────────────────────────────────────
    if (state == ASKING) {
        if (M5.BtnA.wasPressed()) {
            sendEvent("APPROVE");
            statusText   = "Approved";
            state        = IDLE;
            displayDirty = true;
        } else if (M5.BtnB.wasPressed()) {
            sendEvent("REJECT");
            statusText   = "Rejected";
            state        = IDLE;
            displayDirty = true;
        }

    } else if (state == MENU) {
        if (M5.BtnA.wasPressed()) {
            // Select current item
            switch (menuIndex) {
            case 0: // View Status
                state = IDLE;
                break;
            case 1: // Last Message
                notifyText  = lastMessage.length() > 0 ? lastMessage : "No messages yet";
                notifyUntil = millis() + 4000;
                state       = NOTIFYING;
                break;
            case 2: // Interrupt Claude
                sendEvent("INTERRUPT");
                state      = IDLE;
                statusText = "Interrupted";
                pendingBuzzMs = 80;
                break;
            case 3: // Disconnect
                state      = IDLE;
                statusText = "Disconnecting";
                displayDirty = true;
                // Force disconnect by stopping advertising then restarting
                // (the laptop will detect disconnection)
                pendingRestartAdv = true;
                break;
            }
            displayDirty = true;
        } else if (M5.BtnB.wasPressed()) {
            menuIndex = (menuIndex + 1) % MENU_COUNT;
            displayDirty = true;
        }

    } else {
        // IDLE / NOTIFYING
        if (M5.BtnA.isPressed()) {
            if (btnADownAt == 0) {
                btnADownAt    = millis();
                btnALongFired = false;
            } else if (!btnALongFired && millis() - btnADownAt >= LONG_PRESS_MS) {
                btnALongFired = true;
                sendEvent("BTN_A_LONG");
            }
        } else {
            if (btnADownAt > 0 && !btnALongFired) sendEvent("BTN_A");
            btnADownAt    = 0;
            btnALongFired = false;
        }
        if (M5.BtnB.wasPressed()) {
            menuIndex       = 0;
            menuReturnState = state;
            state           = MENU;
            displayDirty    = true;
        }
        if (deviceConnected) detectGestures();
    }

    if (displayDirty) drawDisplay();

    delay(10);
}
