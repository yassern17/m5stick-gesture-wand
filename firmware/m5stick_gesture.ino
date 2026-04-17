/*
 * M5StickC Plus — GestureWand firmware
 *
 * Broadcasts IMU-based gestures over BLE GATT notify.
 * Laptop client (laptop/laptop_client.py) connects and maps gestures to actions.
 *
 * ── Tunable constants ────────────────────────────────────────────────────────
 * All thresholds live at the top of this file. Adjust them to suit your
 * wrist orientation and sensitivity preferences, then re-flash.
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * Required library: M5StickCPlus (install via Arduino Library Manager)
 */

#include <M5StickCPlus.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// ── BLE identifiers ──────────────────────────────────────────────────────────
// Must match laptop/laptop_client.py
#define DEVICE_NAME       "M5GestureWand"
#define SERVICE_UUID      "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define GESTURE_CHAR_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"

// ── Gesture thresholds ───────────────────────────────────────────────────────
#define TILT_ANGLE_THRESHOLD  28.0f   // degrees from neutral before a tilt fires
#define SHAKE_ACCEL_THRESHOLD  2.2f   // total g-force that triggers a shake
#define FLICK_GYRO_THRESHOLD  280.0f  // deg/s gyro spike that triggers a flick/rotate
#define GESTURE_COOLDOWN_MS    400    // min ms between one-shot gestures (shake/flick)
#define TILT_FIRST_DELAY_MS    600    // ms of hold before tilt starts repeating
#define TILT_REPEAT_MS         180    // ms between repeated tilt events while held

// ── Globals ──────────────────────────────────────────────────────────────────
BLEServer*         pServer      = nullptr;
BLECharacteristic* pGestureChar = nullptr;
bool deviceConnected  = false;
bool prevConnected    = false;

float accX, accY, accZ;
float gyroX, gyroY, gyroZ;

// Tilt state
String currentTilt    = "";
unsigned long tiltStartTime   = 0;
unsigned long lastTiltSentTime = 0;

// One-shot gesture state
unsigned long lastInstantGestureTime = 0;

// ── BLE server callbacks ──────────────────────────────────────────────────────
class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer*) {
    deviceConnected = true;
    drawStatus("Connected", TFT_GREEN);
  }
  void onDisconnect(BLEServer*) {
    deviceConnected = false;
    drawStatus("Advertising", TFT_ORANGE);
  }

  void drawStatus(const char* msg, uint16_t color) {
    M5.Lcd.fillRect(0, 28, 240, 20, TFT_BLACK);
    M5.Lcd.setTextColor(color, TFT_BLACK);
    M5.Lcd.setTextSize(2);
    M5.Lcd.setCursor(5, 30);
    M5.Lcd.print(msg);
  }
};

// ── Send a gesture name over BLE ──────────────────────────────────────────────
void sendGesture(const String& name) {
  Serial.println("GESTURE: " + name);

  // Show on display
  M5.Lcd.fillRect(0, 60, 240, 30, TFT_BLACK);
  M5.Lcd.setTextColor(TFT_YELLOW, TFT_BLACK);
  M5.Lcd.setTextSize(2);
  M5.Lcd.setCursor(5, 65);
  M5.Lcd.print(name);

  if (!deviceConnected) return;
  pGestureChar->setValue(name.c_str());
  pGestureChar->notify();
}

// ── Gesture detection (called every ~10 ms) ───────────────────────────────────
void detectGestures() {
  unsigned long now = millis();

  M5.IMU.getAccelData(&accX, &accY, &accZ);
  M5.IMU.getGyroData(&gyroX, &gyroY, &gyroZ);

  float pitch    = atan2(accY, sqrtf(accX*accX + accZ*accZ)) * 180.0f / PI;
  float roll     = atan2(-accX, accZ)                         * 180.0f / PI;
  float accelMag = sqrtf(accX*accX + accY*accY + accZ*accZ);
  float gyroMag  = sqrtf(gyroX*gyroX + gyroY*gyroY + gyroZ*gyroZ);

  // ── One-shot gestures: shake and flick/rotate ─────────────────────────────
  // These fire once per motion with a cooldown. They take priority over tilts.
  if (now - lastInstantGestureTime >= GESTURE_COOLDOWN_MS) {

    if (accelMag > SHAKE_ACCEL_THRESHOLD) {
      sendGesture("SHAKE");
      lastInstantGestureTime = now;
      currentTilt = ""; // cancel any active tilt
      return;
    }

    if (gyroMag > FLICK_GYRO_THRESHOLD) {
      float ax = abs(gyroX), ay = abs(gyroY), az = abs(gyroZ);
      if (ax >= ay && ax >= az) {
        sendGesture(gyroX > 0 ? "FLICK_FORWARD" : "FLICK_BACK");
      } else if (az >= ax && az >= ay) {
        sendGesture(gyroZ > 0 ? "ROTATE_CW" : "ROTATE_CCW");
      }
      // gyroY dominant → wrist flick up/down, extend as needed
      lastInstantGestureTime = now;
      currentTilt = "";
      return;
    }
  }

  // ── Sustained tilt gestures (repeat while held) ───────────────────────────
  String detected = "";
  if      (pitch >  TILT_ANGLE_THRESHOLD) detected = "TILT_UP";
  else if (pitch < -TILT_ANGLE_THRESHOLD) detected = "TILT_DOWN";
  else if (roll  >  TILT_ANGLE_THRESHOLD) detected = "TILT_RIGHT";
  else if (roll  < -TILT_ANGLE_THRESHOLD) detected = "TILT_LEFT";

  if (detected.length() > 0) {
    if (detected != currentTilt) {
      // Entered a new tilt direction — fire immediately
      currentTilt      = detected;
      tiltStartTime    = now;
      lastTiltSentTime = now;
      sendGesture(currentTilt);
    } else {
      // Held in same direction — repeat after initial delay, then at repeat interval
      unsigned long held         = now - tiltStartTime;
      unsigned long sinceLastSent = now - lastTiltSentTime;
      if (held >= TILT_FIRST_DELAY_MS && sinceLastSent >= TILT_REPEAT_MS) {
        sendGesture(currentTilt);
        lastTiltSentTime = now;
      }
    }
  } else {
    currentTilt = ""; // back to neutral
  }
}

// ── Setup ─────────────────────────────────────────────────────────────────────
void setup() {
  M5.begin();
  M5.IMU.Init();
  Serial.begin(115200);

  M5.Lcd.setRotation(3); // landscape
  M5.Lcd.fillScreen(TFT_BLACK);

  M5.Lcd.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Lcd.setTextSize(2);
  M5.Lcd.setCursor(5, 5);
  M5.Lcd.print("GestureWand");

  M5.Lcd.setTextColor(TFT_ORANGE, TFT_BLACK);
  M5.Lcd.setCursor(5, 30);
  M5.Lcd.print("Starting BLE...");

  BLEDevice::init(DEVICE_NAME);
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  BLEService* pService = pServer->createService(SERVICE_UUID);

  pGestureChar = pService->createCharacteristic(
    GESTURE_CHAR_UUID,
    BLECharacteristic::PROPERTY_NOTIFY
  );
  pGestureChar->addDescriptor(new BLE2902());

  pService->start();

  BLEAdvertising* pAdv = BLEDevice::getAdvertising();
  pAdv->addServiceUUID(SERVICE_UUID);
  pAdv->setScanResponse(true);
  pAdv->setMinPreferred(0x06);
  BLEDevice::startAdvertising();

  M5.Lcd.fillRect(0, 28, 240, 20, TFT_BLACK);
  M5.Lcd.setTextColor(TFT_ORANGE, TFT_BLACK);
  M5.Lcd.setCursor(5, 30);
  M5.Lcd.print("Advertising...");
}

// ── Loop ──────────────────────────────────────────────────────────────────────
void loop() {
  M5.update();

  detectGestures();

  // Physical buttons as explicit gestures
  if (M5.BtnA.wasPressed()) sendGesture("BTN_A");
  if (M5.BtnB.wasPressed()) sendGesture("BTN_B");

  // Restart advertising after disconnect
  if (!deviceConnected && prevConnected) {
    delay(300);
    pServer->startAdvertising();
  }
  prevConnected = deviceConnected;

  delay(10); // ~100 Hz
}
