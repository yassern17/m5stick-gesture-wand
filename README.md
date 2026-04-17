# M5StickC Plus Gesture Wand

Control your laptop with wrist gestures over Bluetooth using an M5StickC Plus (the orange one).

The watch detects motion via its built-in IMU and sends gesture events to your laptop over BLE. A Python client receives them and injects real keypresses — works on Wayland.

## Gestures

| Gesture | Default action |
|---|---|
| Tilt left | Previous track |
| Tilt right | Next track |
| Tilt up | Volume up |
| Tilt down | Volume down |
| Shake | Play / pause |
| Flick forward | → (next slide) |
| Flick back | ← (prev slide) |
| Rotate CW | Scroll down |
| Rotate CCW | Scroll up |
| Button A (big) | Space |
| Button B (small) | Escape |

## Hardware

- [M5StickC Plus](https://shop.m5stack.com/products/m5stickc-plus-esp32-pico-mini-iot-development-kit) — ESP32-PICO-D4 + MPU6886 IMU + BLE

## Setup

### 1 — Flash the firmware

Install [arduino-cli](https://arduino.github.io/arduino-cli/), then:

```bash
arduino-cli config add board_manager.additional_urls \
  https://m5stack.oss-cn-shenzhen.aliyuncs.com/resource/arduino/package_m5stack_index.json
arduino-cli core update-index
arduino-cli core install m5stack:esp32
arduino-cli lib install "M5StickCPlus"
./flash.sh
```

The M5Stick screen shows **"Advertising..."** when ready.

### 2 — Run the laptop client

```bash
cd laptop
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ..
./run.sh
```

On Linux, add yourself to the `input` and `uucp` groups first (one-time):

```bash
sudo usermod -aG input,uucp $USER
newgrp input
```

## Customising gestures

Everything lives in [`laptop/gestures.py`](laptop/gestures.py) — it's the only file you need to touch.

**Change an action:**
```python
def on_shake(zone: str):
    tap(e.KEY_MUTE)   # was KEY_PLAYPAUSE
```

**Add a new gesture:**
1. Call `sendGesture("MY_GESTURE")` somewhere in the firmware
2. Add a handler and map it in `gestures.py`:
```python
def on_my_gesture(zone: str):
    tap(e.KEY_BRIGHTNESSUP)

GESTURE_MAP = {
    ...
    "MY_GESTURE": on_my_gesture,
}
```

## Proximity zones

The client gates gestures by RSSI so accidental triggers don't fire from across the room:

| Zone | RSSI | Behaviour |
|---|---|---|
| NEAR | ≥ -65 dBm | Full gesture control |
| MEDIUM | ≥ -80 dBm | Gestures active |
| FAR | < -80 dBm | Gestures suppressed |

Thresholds are at the top of `gestures.py`.

## Tuning sensitivity

All thresholds are `#define` constants at the top of `firmware/m5stick_gesture/m5stick_gesture.ino`:

```cpp
#define TILT_ANGLE_THRESHOLD  28.0f   // degrees — lower = more sensitive
#define SHAKE_ACCEL_THRESHOLD  2.2f   // g-force above 1g
#define FLICK_GYRO_THRESHOLD  280.0f  // deg/s
```

Adjust and re-run `./flash.sh` to apply.
