/*
 * ESP32 BLE Anchor — GestureWand spatial mapper
 *
 * Scans for the M5GestureWand advertising packets and streams a smoothed
 * RSSI over WiFi/UDP to the laptop mapper. Each anchor is a stationary
 * observation point; combine the laptop's own reading with one or more of
 * these and the mapper can fingerprint which spot in the home the watch is
 * currently at.
 *
 * ── Before flashing ─────────────────────────────────────────────────────────
 *   1. Set WIFI_SSID / WIFI_PASSWORD below.
 *   2. Give this anchor a unique ANCHOR_ID (shown in the GUI).
 *   3. Install the espressif core once:
 *        arduino-cli core install esp32:esp32
 *   4. Flash:
 *        ./flash.sh scanner /dev/ttyUSB0
 * ────────────────────────────────────────────────────────────────────────────
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>

// ── Configure me ─────────────────────────────────────────────────────────────
#define WIFI_SSID        "your-wifi-ssid"
#define WIFI_PASSWORD    "your-wifi-password"
#define ANCHOR_ID        "esp32-a"           // unique per anchor; visible in GUI
#define UDP_PORT         42042               // must match laptop mapper
#define WATCH_NAME       "M5GestureWand"
#define RSSI_EMA_ALPHA   0.30f
#define SCAN_SECONDS     1                   // BLE scan window per cycle
#define WIFI_RETRY_MS    15000

// ── State ────────────────────────────────────────────────────────────────────
WiFiUDP   udp;
BLEScan*  pScan      = nullptr;
float     rssiEma    = -80.0f;
bool      sawWatch   = false;                // true if we saw the watch this scan window
uint32_t  seq        = 0;
unsigned long lastWifiRetry = 0;

// ── BLE scan callback ────────────────────────────────────────────────────────
class AdvCallback : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice dev) override {
    if (!dev.haveName() || dev.getName() != WATCH_NAME) return;
    int raw = dev.getRSSI();
    if (sawWatch) {
      rssiEma = RSSI_EMA_ALPHA * raw + (1.0f - RSSI_EMA_ALPHA) * rssiEma;
    } else {
      rssiEma  = raw;
      sawWatch = true;
    }
  }
};

// ── WiFi ─────────────────────────────────────────────────────────────────────
void connectWifi() {
  Serial.printf("WiFi: connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500);
    Serial.print('.');
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("WiFi: ok — IP=%s  broadcast=%s\n",
                  WiFi.localIP().toString().c_str(),
                  WiFi.broadcastIP().toString().c_str());
  } else {
    Serial.println("WiFi: failed — will retry from loop().");
  }
}

// ── Emit one UDP sample ──────────────────────────────────────────────────────
void sendSample(bool fresh) {
  if (WiFi.status() != WL_CONNECTED) return;
  char buf[160];
  int n = snprintf(buf, sizeof(buf),
    "{\"id\":\"%s\",\"rssi\":%.1f,\"seq\":%u,\"fresh\":%s}",
    ANCHOR_ID, rssiEma, (unsigned)seq++, fresh ? "true" : "false");
  udp.beginPacket(WiFi.broadcastIP(), UDP_PORT);
  udp.write((uint8_t*)buf, n);
  udp.endPacket();
  Serial.println(buf);
}

// ── Setup ────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println("── ESP32 GestureWand Anchor ──");
  Serial.printf("Anchor ID: %s\n", ANCHOR_ID);

  connectWifi();
  udp.begin(UDP_PORT);

  BLEDevice::init("");
  pScan = BLEDevice::getScan();
  pScan->setAdvertisedDeviceCallbacks(new AdvCallback(), /*wantDuplicates=*/true);
  pScan->setActiveScan(true);
  pScan->setInterval(100);
  pScan->setWindow(99);
  Serial.println("BLE scanner ready.");
}

// ── Loop ─────────────────────────────────────────────────────────────────────
void loop() {
  // Blocking scan window — callback fires for each advert received.
  sawWatch = false;  // reset at start of window; freshness reflects this window
  pScan->start(SCAN_SECONDS, false);
  pScan->clearResults();

  sendSample(sawWatch);

  if (WiFi.status() != WL_CONNECTED &&
      millis() - lastWifiRetry > WIFI_RETRY_MS) {
    lastWifiRetry = millis();
    Serial.println("WiFi: reconnecting...");
    WiFi.reconnect();
  }
}
