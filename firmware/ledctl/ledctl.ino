// ledctl v2 - ESP32-C3 firmware (addressable WS2812B strip: frames from the laptop -> FPGA)
// Wi-Fi hotspot "ledctl" + phone web app -> UART commands to the FPGA.
// FPGA does the I2S mic + beat detection and sends 'B' on every beat.
//
// Pins (ESP32-C3-MINI-1):
//   GPIO4 = UART TX -> FPGA pin 70      GPIO5 = UART RX <- FPGA pin 69
//   GPIO8 = I2C SDA, GPIO2 = I2C SCL   (INA219 @0x40, 10 mOhm shunt -> 1 mA / LSB)
//   GPIO7 = status LED
//   GPIO0/1/3/10 = FPGA JTAG, GPIO6 = FPGA RECONFIG  -> never touched here
//
// UART frame to FPGA: 0xA5, cmd, a, b, c
//   'C' r g b   base colour      'M' mode (0 static, 1 music, 2 chill, 3 off)
//   'T' ratio8 floor             beat threshold (ratio in 1/8 steps)

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <Wire.h>
#include "page.h"
#include "fpga_loader.h"
#include "audio.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

// BLE: phone app writes "m=music&h=30&v=80" to CMD, gets state JSON on STATE (notify)
#define BLE_SVC   "6e1d0001-8c2a-4f0e-9b1a-1edc7a5e0001"
#define BLE_CMD   "6e1d0002-8c2a-4f0e-9b1a-1edc7a5e0001"
#define BLE_STATE "6e1d0003-8c2a-4f0e-9b1a-1edc7a5e0001"
// BLE frames: [led_off_lo, led_off_hi, flags(bit0 = last chunk -> show), R,G,B, R,G,B, ...]
#define BLE_FRAME "6e1d0004-8c2a-4f0e-9b1a-1edc7a5e0001"

#define AP_SSID "ledctl"
#define AP_PASS "ledctl1234"

#define PIN_TX 4
#define PIN_RX 5
#define PIN_SDA 8
#define PIN_SCL 2
#define PIN_LED 7

#define CURRENT_LIMIT_MA 2100   // USB socket is 2.4 A
#define DUTY_MAX 191            // 75 % per channel hard cap

WebServer server(80);
Preferences prefs;

enum Mode { M_OFF, M_SOLID, M_BREATHE, M_MUSIC, M_PARTY, M_CHILL, M_MAKEOUT, M_PULSE, M_STUDY, M_COUNT };
const char *MODE_NAMES[M_COUNT] = {"off", "solid", "breathe", "music", "party", "chill", "makeout", "pulse", "study"};
uint32_t studyStart = 0;
String fpgaStatus = "not loaded";
volatile bool fpgaReloadReq = false;

struct State {
  uint8_t mode = M_SOLID;
  uint16_t hue = 30;   // 0-359
  uint8_t sat = 70;    // 0-100
  uint8_t bri = 60;    // 5-100
  uint8_t sens = 5;    // 1-10 beat sensitivity
  uint16_t nleds = 100; // LEDs on the addressable strip
  uint8_t t0h = 11, t1h = 22, tbit = 34;   // WS2812 bit timing in FPGA clock cycles (27 MHz)
} st;

// ---------- per-LED frames from the laptop (USB or BLE) -> FPGA ----------
#define MAX_LEDS 300
uint8_t frameBuf[MAX_LEDS * 3];
uint32_t framesMs = 0;               // last frame forwarded
uint32_t frameCount = 0;
uint32_t frameBad = 0;               // frames dropped because the checksum didn't match
void forwardFrame(const uint8_t *rgb, uint16_t n);

uint16_t partyHue = 0;
uint32_t beats = 0, lastBeatMs = 0;

// ---------- audio from the FPGA (every 10 ms + on beats): 0xFF, loud, level, beatcount, bass, mid, high ----------
Audio au;
uint8_t aBeatCnt = 0;
bool aSeen = false;
float flash = 0;                   // 1 on beat, decays
float kick = 0;                    // colour kick on beat, decays
float drop = 0;                    // big hit after a quiet part
uint32_t phraseBeats = 0;

// Music mode walks through a curated palette (no greens / yellow-greens, they look cheap on RGB strips)
const uint16_t PALETTE[] = {330, 290, 250, 210, 188, 345, 270, 15, 225, 310, 35, 200};
const int NPAL = sizeof(PALETTE) / sizeof(PALETTE[0]);
float palTarget = 0, palPos = 0;   // position along the palette (fractional = blend)

// smooth 1-D value noise in [-1, 1] and a 3-octave version: organic, never-repeating motion
float hash1(int32_t i) { uint32_t h = (uint32_t)i * 2654435761u; h ^= h >> 15; h *= 2246822519u; h ^= h >> 13; return (h & 0xFFFF) / 32767.5f - 1.0f; }
float noise1(float x) { int32_t i = (int32_t)floorf(x); float f = x - i; f = f * f * (3 - 2 * f); return hash1(i) + (hash1(i + 1) - hash1(i)) * f; }
float fbm(float x) { return (noise1(x) + 0.5f * noise1(2.03f * x + 17.1f) + 0.25f * noise1(4.11f * x + 41.7f)) / 1.75f; }
float easeInOut(float f) { return f * f * (3 - 2 * f); }
float hueLerp(float a, float b, float f) { float d = fmodf(b - a + 540.0f, 360.0f) - 180.0f; return fmodf(a + d * f + 360.0f, 360.0f); }

// Study mode: Pomodoro. 25 min focus in steady cool white (5000-6500 K-ish, no flicker),
// then 5 min break in warm amber that slowly breathes. Crossfades over 15 s, with 3 soft pulses to tell you.
const uint32_t FOCUS_MS = 25UL * 60 * 1000, BREAK_MS = 5UL * 60 * 1000, XFADE_MS = 15000;
String studyText() {
  uint32_t t = (millis() - studyStart) % (FOCUS_MS + BREAK_MS);
  bool focus = t < FOCUS_MS;
  uint32_t left = focus ? FOCUS_MS - t : FOCUS_MS + BREAK_MS - t;
  char b[32]; snprintf(b, sizeof(b), "%s %lu:%02lu", focus ? "Focus" : "Break", (unsigned long)(left / 60000), (unsigned long)((left / 1000) % 60));
  return String(b);
}

float palHue(float pos) {
  int i = ((int)floorf(pos)) % NPAL; if (i < 0) i += NPAL;
  float f = pos - floorf(pos); f = f * f * (3 - 2 * f);
  int a = PALETTE[i], b = PALETTE[(i + 1) % NPAL];
  int d = ((b - a + 540) % 360) - 180;
  return fmodf(a + d * f + 360.0f, 360.0f);
}

void onBeat() {
  beats++;
  lastBeatMs = millis();
  flash = fminf(1.0f, flash + 0.75f);
  kick = 1.0f;
  phraseBeats++;
  if (st.mode == M_PARTY) partyHue = (partyHue + 97 + (esp_random() % 60)) % 360;
  if (st.mode == M_MUSIC) {
    if (au.envSlow < 0.3f && au.env > 0.7f) {          // drop: loud hit after a quieter stretch
      drop = 1.0f; palTarget += 3; phraseBeats = 0;
      au.envSlow = 0.6f;                               // don't re-trigger on the next beat
    } else if (phraseBeats % 8 == 0) {
      palTarget += 1;                                  // new phrase -> next colour
    } else {
      palTarget += 0.12f + 0.12f * au.env;             // every beat leans towards the next colour
    }
  }
}

uint32_t streamUntil = 0;   // USB serial 'S': stream raw telemetry as CSV for 40 s (for tuning on the laptop)
uint32_t liveMs = 0;        // laptop "music brain" sends the finished colour ("lv=h,s,v") 100x a second
uint16_t liveH = 0; uint8_t liveS = 0; float liveV = 0;
uint32_t phoneAudioMs = 0;  // last time the phone app sent its own audio levels (then the board mic is ignored)
bool phoneAudio() { return phoneAudioMs && millis() - phoneAudioMs < 600; }

void afterFrame(bool fpgaBeat) {
  // when the tempo tracker is locked its beat clock drives the lights (steady, on the grid);
  // otherwise single onsets do
  static uint32_t lastAny = 0;
  if (!au.locked() && (fpgaBeat || au.onset) && millis() - lastAny > 140) { lastAny = millis(); onBeat(); }
}

// "pa=level,bass,mid,high" from the phone (log2 x4 units, like the FPGA)
void onPhoneAudio(const String &v) {
  int a[4] = {0, 0, 0, 0}, k = 0, p = 0;
  while (k < 4 && p <= (int)v.length()) {
    int c = v.indexOf(',', p); if (c < 0) c = v.length();
    a[k++] = v.substring(p, c).toInt(); p = c + 1;
  }
  phoneAudioMs = millis();
  au.frame(a[0], a[1], a[2], a[3], st.sens);
  afterFrame(false);
}

void onAudio(uint8_t loud, uint8_t level, uint8_t cnt, uint8_t lb, uint8_t lm, uint8_t lh) {
  if (streamUntil && (int32_t)(streamUntil - millis()) > 0)
    Serial.printf("%lu,%u,%u,%u,%u,%u,%u\n", (unsigned long)millis(), loud, level, lb, lm, lh, cnt);
  if (phoneAudio()) { aBeatCnt = cnt; return; }    // phone is feeding us the music directly
  au.frame(level, lb, lm, lh, st.sens);
  // beats: the FPGA's bass beat (if it's clearly above the fan) OR the ESP's all-band onset detector
  float gate = 12 - st.sens;   // in 0.75 dB steps above floor + 2 sigma: sens 1 -> 8 dB, sens 10 -> 1.5 dB
  bool fpgaBeat = aSeen && cnt != aBeatCnt && (au.bass.snr > gate || au.lvl.snr > gate);
  aBeatCnt = cnt; aSeen = true;
  afterFrame(fpgaBeat);
}

uint8_t fpgaWs = 0;                    // strip refreshes the FPGA has sent (diagnostics)
uint8_t fpgaRx = 0;                    // packets the FPGA says it received (diagnostics)
void audioRx() {
  static uint8_t buf[8]; static int n = -1;
  while (Serial1.available()) {
    uint8_t c = Serial1.read();
    if (c == 0xFF) { n = 0; continue; }
    if (n < 0) continue;
    buf[n++] = c;
    if (n == 8) { onAudio(buf[0], buf[1], buf[2], buf[3], buf[4], buf[5]); fpgaRx = buf[6]; fpgaWs = buf[7]; n = -1; }
  }
}
int currentMa = -1;
extern float capScale;
void forwardFrame(const uint8_t *rgb, uint16_t n) {
  if (n > MAX_LEDS) n = MAX_LEDS;
  uint8_t hdr[3] = {0xA7, (uint8_t)(n & 0xFF), (uint8_t)(n >> 8)};
  Serial1.write(hdr, 3);
  if (capScale < 0.999f) {             // INA219 says we're over the current limit: scale down
    static uint8_t tmp[MAX_LEDS * 3];
    uint16_t k = (uint16_t)(capScale * 256);
    for (int i = 0; i < n * 3; i++) tmp[i] = (rgb[i] * k) >> 8;
    Serial1.write(tmp, n * 3);
  } else Serial1.write(rgb, n * 3);
  framesMs = millis();
  frameCount++;
}
bool inaOk = false;
float capScale = 1.0f;
uint8_t sentR = 0, sentG = 0, sentB = 0, sentMode = 255, sentRatio = 0;
uint32_t lastKeepalive = 0;

// ---------- FPGA link ----------
void sendFrame(uint8_t cmd, uint8_t a, uint8_t b = 0, uint8_t c = 0) {
  uint8_t f[5] = {0xA5, cmd, a, b, c};
  Serial1.write(f, 5);
}

void hsvToRgb(uint16_t h, uint8_t s, float v, uint8_t &r, uint8_t &g, uint8_t &b) {
  float S = s / 100.0f, H = (h % 360) / 60.0f;
  float c = v * S, x = c * (1 - fabsf(fmodf(H, 2) - 1)), m = v - c;
  float R, G, B;
  int i = (int)H;
  switch (i) {
    case 0: R = c; G = x; B = 0; break;
    case 1: R = x; G = c; B = 0; break;
    case 2: R = 0; G = c; B = x; break;
    case 3: R = 0; G = x; B = c; break;
    case 4: R = x; G = 0; B = c; break;
    default: R = c; G = 0; B = x; break;
  }
  r = (uint8_t)lroundf((R + m) * DUTY_MAX);
  g = (uint8_t)lroundf((G + m) * DUTY_MAX);
  b = (uint8_t)lroundf((B + m) * DUTY_MAX);
}

// brightness 0-1 after gamma, times the current-limit scale
void pushColour(uint16_t h, uint8_t s, float level) {
  float v = powf(constrain(level, 0.0f, 1.0f), 2.2f) * capScale;
  uint8_t r, g, b;
  hsvToRgb(h, s, v, r, g, b);
  if (r != sentR || g != sentG || b != sentB) {
    sendFrame('C', r, g, b);
    sentR = r; sentG = g; sentB = b;
  }
}

// all the light effects run here on the ESP32; the FPGA just outputs the colour (mode 0)
uint8_t fpgaModeFor(uint8_t m) { return m == M_OFF ? 3 : 0; }

uint16_t sentN = 0;
void pushMode(bool force = false) {
  if (force || st.nleds != sentN) { sendFrame('N', st.nleds & 0xFF, st.nleds >> 8); sendFrame('A', 1); sentN = st.nleds; }
  static uint32_t sentW = 0;
  uint32_t w = (st.t0h << 16) | (st.t1h << 8) | st.tbit;
  if (force || w != sentW) { sendFrame('W', st.t0h, st.t1h, st.tbit); sentW = w; }
  uint8_t fm = fpgaModeFor(st.mode);
  if (force || fm != sentMode) { sendFrame('M', fm); sentMode = fm; }
  // sensitivity 1..10 -> ratio 2.75x .. 1.25x (in 1/8 steps: 22 .. 10)
  uint8_t ratio = 22 - (uint8_t)lroundf((st.sens - 1) * 12 / 9.0f);
  if (force || ratio != sentRatio) { sendFrame('T', ratio, 8); sentRatio = ratio; }
}

void forceResendAll() {
  sentR = sentG = sentB = 255;  // invalid so next pushColour sends
  pushMode(true);
}

// ---------- INA219 ----------
bool inaRead(int &ma) {
  Wire.beginTransmission(0x40);
  Wire.write(0x01);  // shunt voltage register
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(0x40, 2) != 2) return false;
  int16_t raw = (Wire.read() << 8) | Wire.read();
  ma = raw;  // 10 uV / LSB over 10 mOhm = 1 mA / LSB
  return true;
}

void currentLoop() {
  static uint32_t t = 0;
  if (millis() - t < 50) return;
  t = millis();
  int ma;
  if (!inaRead(ma)) { inaOk = false; currentMa = -1; return; }
  inaOk = true;
  currentMa = ma;
  if (ma > CURRENT_LIMIT_MA) {
    capScale *= (float)(CURRENT_LIMIT_MA - 100) / ma;
    if (capScale < 0.2f) capScale = 0.2f;
  } else if (ma < CURRENT_LIMIT_MA - 300 && capScale < 1.0f) {
    capScale = fminf(1.0f, capScale * 1.02f);
  }
}

// ---------- settings ----------
void saveState() {
  prefs.putUChar("mode", st.mode);
  prefs.putUShort("hue", st.hue);
  prefs.putUChar("sat", st.sat);
  prefs.putUChar("bri", st.bri);
  prefs.putUChar("sens", st.sens);
  prefs.putUShort("nleds", st.nleds);
  prefs.putUChar("t0h", st.t0h); prefs.putUChar("t1h", st.t1h); prefs.putUChar("tbit", st.tbit);
}

void loadState() {
  st.mode = prefs.getUChar("mode", M_SOLID);
  st.hue = prefs.getUShort("hue", 30);
  st.sat = prefs.getUChar("sat", 70);
  st.bri = prefs.getUChar("bri", 60);
  st.sens = prefs.getUChar("sens", 5);
  st.nleds = constrain(prefs.getUShort("nleds", 100), 1, MAX_LEDS);
  st.t0h = prefs.getUChar("t0h", 11); st.t1h = prefs.getUChar("t1h", 22); st.tbit = prefs.getUChar("tbit", 34);
  if (st.mode >= M_COUNT) st.mode = M_SOLID;
}

// ---------- web ----------
float frameRate() {
  static uint32_t t0 = 0, c0 = 0; static float fps = 0;
  if (millis() - t0 > 1000) { fps = (frameCount - c0) * 1000.0f / (millis() - t0); t0 = millis(); c0 = frameCount; }
  return (millis() - framesMs < 1000) ? fps : 0;
}

String stateJson() {
  char buf[480];
  snprintf(buf, sizeof(buf),
           "{\"mode\":\"%s\",\"h\":%u,\"s\":%u,\"v\":%u,\"sens\":%u,\"beats\":%lu,\"ma\":%d,\"cap\":%d,\"ph\":%u,\"fpga\":\"%s\",\"lv\":%d,\"mic\":%d,\"sb\":%d,\"sm\":%d,\"sh\":%d,\"nf\":%d,\"raw\":[%u,%u,%u,%u],\"bpm\":%d,\"lk\":%d,\"study\":\"%s\",\"n\":%u,\"fps\":%d,\"w\":\"%u,%u,%u\",\"frx\":%u,\"fws\":%u,\"fbad\":%lu}",
           MODE_NAMES[st.mode], st.hue, st.sat, st.bri, st.sens, (unsigned long)beats, currentMa,
           (int)lroundf(capScale * 100), partyHue, fpgaStatus.substring(0, 40).c_str(),
           (int)lroundf(au.env * 100), au.live() ? (int)au.raw[0] : -1,
           (int)lroundf(au.b * 100), (int)lroundf(au.m * 100), (int)lroundf(au.h * 100),
           (int)lroundf(au.lvl.x), au.raw[0], au.raw[1], au.raw[2], au.raw[3],
           (int)lroundf(au.tracker.bpm()), au.locked() ? 1 : 0, st.mode == M_STUDY ? studyText().c_str() : "",
           st.nleds, (int)frameRate(), st.t0h, st.t1h, st.tbit, fpgaRx, fpgaWs, (unsigned long)frameBad);
  return String(buf);
}

uint32_t saveAt = 0;

// shared by the web page (HTTP query) and the app (BLE "k=v&k=v")
bool applyParam(const String &k, const String &v) {
  if (k == "m") {
    for (int i = 0; i < M_COUNT; i++)
      if (v == MODE_NAMES[i]) { if (i == M_STUDY) studyStart = millis(); st.mode = i; return true; }
    return false;
  }
  if (k == "h") { st.hue = constrain(v.toInt(), 0, 359); return true; }
  if (k == "s") { st.sat = constrain(v.toInt(), 0, 100); return true; }
  if (k == "v") { st.bri = constrain(v.toInt(), 5, 100); return true; }
  if (k == "sens") { st.sens = constrain(v.toInt(), 1, 10); return true; }
  if (k == "n") { st.nleds = constrain(v.toInt(), 1, MAX_LEDS); return true; }
  if (k == "w") {                       // "w=t0h,t1h,tbit" in 27 MHz cycles
    int c1 = v.indexOf(','), c2 = v.indexOf(',', c1 + 1);
    if (c1 > 0 && c2 > c1) {
      st.t0h = constrain(v.substring(0, c1).toInt(), 2, 60);
      st.t1h = constrain(v.substring(c1 + 1, c2).toInt(), 3, 80);
      st.tbit = constrain(v.substring(c2 + 1).toInt(), 20, 120);
      return true;
    }
    return false;
  }
  if (k == "reload") { fpgaReloadReq = true; return false; }
  if (k == "pa") { onPhoneAudio(v); return false; }
  if (k == "lv") {
    int c1 = v.indexOf(','), c2 = v.indexOf(',', c1 + 1);
    if (c1 > 0 && c2 > c1) {
      liveH = constrain(v.substring(0, c1).toInt(), 0, 359);
      liveS = constrain(v.substring(c1 + 1, c2).toInt(), 0, 100);
      liveV = constrain(v.substring(c2 + 1).toInt(), 0, 1000) / 1000.0f;
      liveMs = millis();
    }
    return false;
  }
  return false;
}

void settingsChanged() { pushMode(); saveAt = millis() + 2000; }

void handleApi() {
  bool any = false;
  for (int i = 0; i < server.args(); i++) any |= applyParam(server.argName(i), server.arg(i));
  if (any) settingsChanged();
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", stateJson());
}

// ---------- BLE ----------
BLECharacteristic *bleState = nullptr;
volatile bool bleConnected = false;
volatile int bleConns = 0;            // phone app + laptop sync can be connected at the same time
String bleInbox;                       // filled in the BLE task, applied in loop()
portMUX_TYPE bleMux = portMUX_INITIALIZER_UNLOCKED;

class SrvCb : public BLEServerCallbacks {
  void onConnect(BLEServer *s) override { bleConns++; bleConnected = true; BLEDevice::startAdvertising(); }
  void onDisconnect(BLEServer *s) override { if (bleConns > 0) bleConns--; bleConnected = bleConns > 0; BLEDevice::startAdvertising(); }
};
class CmdCb : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *c) override {
    String v = c->getValue();
    portENTER_CRITICAL(&bleMux);
    if (bleInbox.length() && v.length()) bleInbox += "&";
    bleInbox += v;
    portEXIT_CRITICAL(&bleMux);
  }
};

uint8_t bleFrame[MAX_LEDS * 3];
volatile int bleFrameN = 0;           // >0: a complete frame is waiting for loop()
volatile uint16_t bleFrameMax = 0;
class FrameCb : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *c) override {
    size_t len = c->getLength();
    const uint8_t *d = c->getData();
    if (len < 6) return;
    uint16_t off = d[0] | (d[1] << 8);
    uint16_t cnt = (len - 3) / 3;
    if (off + cnt > MAX_LEDS) return;
    portENTER_CRITICAL(&bleMux);
    memcpy(bleFrame + off * 3, d + 3, cnt * 3);
    if (off + cnt > bleFrameMax) bleFrameMax = off + cnt;
    if (d[2] & 1) { bleFrameN = bleFrameMax; bleFrameMax = 0; }
    portEXIT_CRITICAL(&bleMux);
  }
};

void setupBle() {
  BLEDevice::init("ledctl");
  BLEDevice::setMTU(517);
  BLEServer *srv = BLEDevice::createServer();
  srv->setCallbacks(new SrvCb());
  BLEService *svc = srv->createService(BLE_SVC);
  BLECharacteristic *cmd = svc->createCharacteristic(BLE_CMD, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  cmd->setCallbacks(new CmdCb());
  BLECharacteristic *frm = svc->createCharacteristic(BLE_FRAME, BLECharacteristic::PROPERTY_WRITE_NR | BLECharacteristic::PROPERTY_WRITE);
  frm->setCallbacks(new FrameCb());
  bleState = svc->createCharacteristic(BLE_STATE, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  bleState->setValue(stateJson());
  svc->start();
  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(BLE_SVC);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
}

// "k=v&k=v" from BLE or the laptop's USB link; returns true if a setting changed
bool applyQuery(const String &in) {
  bool any = false;
  int p = 0;
  while (p < (int)in.length()) {
    int amp = in.indexOf('&', p); if (amp < 0) amp = in.length();
    String kv = in.substring(p, amp);
    int eq = kv.indexOf('=');
    if (eq > 0) any |= applyParam(kv.substring(0, eq), kv.substring(eq + 1));
    p = amp + 1;
  }
  if (any) settingsChanged();
  return any;
}

void bleLoop() {
  String in;
  static uint8_t fcopy[MAX_LEDS * 3];
  int fn = 0;
  portENTER_CRITICAL(&bleMux);
  in = bleInbox; bleInbox = "";
  if (bleFrameN) { fn = bleFrameN; memcpy(fcopy, bleFrame, fn * 3); bleFrameN = 0; }
  portEXIT_CRITICAL(&bleMux);
  if (fn) forwardFrame(fcopy, fn);
  bool any = false;
  if (in.length()) any = applyQuery(in);
  // push state: right after a change, and every 150 ms (beats counter)
  static uint32_t t = 0;
  static String last;
  if (bleConnected && (any || millis() - t > 150)) {
    String j = stateJson();
    if (any || j != last) {
      bleState->setValue(j);
      bleState->notify();
      last = j;
    }
    t = millis();
  }
}

void setupWeb() {
  server.on("/", []() { server.send_P(200, "text/html", PAGE_HTML); });
  server.on("/manifest.json", []() { server.send_P(200, "application/manifest+json", MANIFEST_JSON); });
  server.on("/icon.svg", []() { server.send_P(200, "image/svg+xml", ICON_SVG); });
  server.on("/api", handleApi);
  server.onNotFound([]() {  // anything else -> the app
    server.sendHeader("Location", "http://192.168.4.1/");
    server.send(302, "text/plain", "");
  });
  server.begin();
}

// ---------- main ----------
void setup() {
  Serial.setRxBufferSize(8192);      // laptop frames arrive at ~18 KB/s; don't drop bytes while Wi-Fi/BLE run
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);
  Serial1.setTxBufferSize(1024);
  Serial1.setRxBufferSize(512);
  Serial1.begin(1000000, SERIAL_8N1, PIN_RX, PIN_TX);   // 1 Mbaud: room for 60 fps x 300 LEDs
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);

  // program the FPGA over JTAG (it has no working config flash yet)
  digitalWrite(PIN_LED, HIGH);
  delay(50);
  fpgaStatus = fpga::load();
  digitalWrite(PIN_LED, LOW);
  Serial.println("FPGA: " + fpgaStatus);

  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  Wire.beginTransmission(0x40); Wire.write(0x00); Wire.write(0x39); Wire.write(0xFF); Wire.endTransmission();
  prefs.begin("ledctl", false);
  loadState();

  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(IPAddress(192, 168, 4, 1), IPAddress(192, 168, 4, 1), IPAddress(255, 255, 255, 0));
  WiFi.softAP(AP_SSID, AP_PASS, 6);
  WiFi.setSleep(false);
  MDNS.begin("ledctl");
  MDNS.addService("http", "tcp", 80);
  setupWeb();
  setupBle();

  delay(300);  // FPGA loads from flash in well under this
  forceResendAll();
  Serial.println("ledctl up: BLE 'ledctl', or join Wi-Fi 'ledctl' (pw ledctl1234), open http://192.168.4.1");
}

void loop() {
  server.handleClient();
  bleLoop();
  currentLoop();

  audioRx();
  digitalWrite(PIN_LED, (millis() - lastBeatMs) < 60 ? HIGH : LOW);

  // colour for this instant
  static uint32_t frameT = 0;
  uint32_t now = millis();
  if (now - frameT >= 10) {  // 100 Hz
    float dt = (now - frameT) / 1000.0f;
    frameT = now;
    if (au.tick()) onBeat();                     // tempo-locked beat clock
    flash *= expf(-dt / 0.28f);
    kick *= expf(-dt / 0.5f);
    drop *= expf(-dt / 0.45f);
    au.sparkle *= expf(-dt / 0.06f);
    float level = st.bri / 100.0f;
    float energy = fmaxf(au.b, fmaxf(0.75f * au.m, 0.5f * au.h));
    bool quiet = !au.live() || (au.env < 0.03f && millis() - lastBeatMs > 3000);
    // shared "glow": a solid base that swells with the music (never drops to dark), beats push on top
    static float glow = 0.55f, bright = 0.55f;
    float gTarget = 0.55f + (quiet ? 0 : 0.3f * energy);
    glow += (gTarget - glow) * (gTarget > glow ? 0.08f : 0.03f);
    float bTarget = fminf(1.0f, glow + 0.28f * flash + 0.25f * drop);
    bright += (bTarget - bright) * (bTarget > bright ? 0.5f : 0.12f);
    switch (st.mode) {
      case M_OFF: break;
      case M_BREATHE: {
        // paced breathing at 6 breaths/min (the "resonance" rate): 4 s in, 6 s out.
        // deep swing (10% -> 100%) and the colour warms slightly as it fills
        float t = fmodf(millis() / 1000.0f, 10.0f), f;
        if (t < 4) f = easeInOut(t / 4); else f = 1 - easeInOut((t - 4) / 6);
        float hue = hueLerp(st.hue, st.hue + 25, f);
        pushColour((uint16_t)hue, (uint8_t)(st.sat * (1 - 0.15f * f)), level * (0.10f + 0.90f * f));
        break;
      }
      case M_MUSIC: {
        if (liveMs && millis() - liveMs < 300) {     // laptop is driving: show its colour directly
          pushColour(liveH, liveS, level * liveV);
          break;
        }
        // colours glide through a curated palette: each beat leans towards the next colour,
        // every 8 beats (a phrase) moves on, drops jump ahead; hi-hats sparkle slightly towards white
        static float hueNow = PALETTE[0];
        if (quiet) palTarget += dt / 8.0f;          // no music: slow walk through the palette
        palPos += (palTarget - palPos) * 0.03f;
        float target = palHue(palPos) + 12.0f * kick;
        float dh = fmodf(target - hueNow + 540.0f, 360.0f) - 180.0f;
        hueNow = fmodf(hueNow + dh * 0.12f + 360.0f, 360.0f);
        int sat = 100 - (int)lroundf(22 * au.sparkle + 45 * drop);
        pushColour((uint16_t)hueNow, (uint8_t)constrain(sat, 40, 100), level * bright);
        break;
      }
      case M_PULSE: {
        // your colour, same glow + beat push, small hue nudge on the hit
        uint16_t h = (st.hue + (int)lroundf(14 * kick)) % 360;
        pushColour(h, st.sat, level * bright);
        break;
      }
      case M_PARTY: {
        // new colour on every beat, bigger flash, never fully dark
        if (millis() - lastBeatMs > 2500) partyHue = (partyHue + 1) % 360;  // drift when quiet
        float b = fminf(1.0f, 0.45f + 0.2f * energy + 0.45f * flash);
        pushColour(partyHue, 100, level * b);
        break;
      }
      case M_CHILL: {
        // aurora: the colour wanders +-60 deg around yours like slow light through water,
        // brightness rolls in long waves, and loud music adds a gentle swell
        float tt = millis() / 1000.0f;
        float hue = fmodf(st.hue + 60.0f * fbm(tt / 9.0f) + 360.0f, 360.0f);
        static float sm = 0; sm += (au.env - sm) * 0.02f;
        float b = 0.62f + 0.28f * fbm(tt / 5.0f + 100) + 0.15f * sm;
        int sat = constrain(st.sat + (int)lroundf(15 * fbm(tt / 7.0f + 50)), 30, 100);
        pushColour((uint16_t)hue, (uint8_t)sat, level * constrain(b, 0.2f, 1.0f));
        break;
      }
      case M_MAKEOUT: {
        // candlelight romance: deep reds and roses that drift towards magenta and violet now and then,
        // low warm glow with a slow 8 s breath and a faint candle flicker
        float tt = millis() / 1000.0f;
        float n = fbm(tt / 14.0f);                                  // -1..1, slow
        float hue = n < 0 ? hueLerp(350, 8, -n) : hueLerp(350, 285, n);
        float breath = 0.5f - 0.5f * cosf(2 * PI * fmodf(tt, 8.0f) / 8.0f);
        float flicker = 0.05f * noise1(tt * 3.1f) + 0.03f * noise1(tt * 7.3f + 9);
        pushColour((uint16_t)hue, 100, level * constrain(0.48f + 0.17f * breath + flicker, 0.2f, 1.0f));
        break;
      }
      case M_STUDY: {
        const float WH = 212, WS = 9, AH = 30, AS = 72;          // cool white / warm amber
        uint32_t t = (millis() - studyStart) % (FOCUS_MS + BREAK_MS);
        float x;                                                  // 0 = focus white, 1 = break amber
        if (t < FOCUS_MS - XFADE_MS) x = 0;
        else if (t < FOCUS_MS) x = easeInOut((t - (FOCUS_MS - XFADE_MS)) / (float)XFADE_MS);
        else if (t < FOCUS_MS + BREAK_MS - XFADE_MS) x = 1;
        else x = 1 - easeInOut((t - (FOCUS_MS + BREAK_MS - XFADE_MS)) / (float)XFADE_MS);
        float b = 1.0f;
        if (x > 0 && x < 1) b = 0.75f + 0.25f * cosf(2 * PI * 3 * x);   // 3 soft pulses during the change
        else if (x == 1) b = 0.8f + 0.2f * cosf(2 * PI * fmodf(millis() / 1000.0f, 10.0f) / 10.0f);  // break breathes
        pushColour((uint16_t)hueLerp(WH, AH, x), (uint8_t)(WS + (AS - WS) * x), level * b);
        break;
      }
      default:
        pushColour(st.hue, st.sat, level);
    }
  }

  // keep the FPGA in sync even if it was reloaded
  if (millis() - lastKeepalive > 2000) {
    lastKeepalive = millis();
    forceResendAll();
  }

  // USB serial: any byte -> print state (used by the laptop scripts); 'R' -> reload FPGA
  static String line;
  static int fstate = 0;                // USB frame packet: 0xA7/0xA8, n_lo, n_hi, 3n bytes (+ checksum for 0xA8)
  static bool fsum = false;
  static uint16_t fN = 0, fGot = 0;
  static uint32_t fT = 0;
  if (fstate && millis() - fT > 50) fstate = 0;    // half a packet and then nothing: drop it
  while (Serial.available()) {
    if (fstate == 3) {                   // bulk payload
      int want = fN * 3 - fGot;
      int got = Serial.read(frameBuf + fGot, min(want, Serial.available()));
      if (got > 0) fGot += got;
      fT = millis();
      if (fGot >= fN * 3) {
        if (!fsum) { forwardFrame(frameBuf, fN); fstate = 0; }
        else fstate = 4;                 // checksum byte next
      }
      continue;
    }
    if (fstate == 4) {
      int c = Serial.read();
      uint8_t sum = 0;
      for (int i = 0; i < fN * 3; i++) sum += frameBuf[i];
      if ((uint8_t)c == sum) forwardFrame(frameBuf, fN); else frameBad++;
      fstate = 0;
      continue;
    }
    int c = Serial.read();
    if (fstate == 1) { fN = c; fstate = 2; continue; }
    if (fstate == 2) { fN |= c << 8; fGot = 0; fstate = (fN > 0 && fN <= MAX_LEDS) ? 3 : 0; continue; }
    if (line.length() == 0 && (c == 0xA7 || c == 0xA8)) { fstate = 1; fsum = (c == 0xA8); fT = millis(); continue; }
    if (line.length() == 0 && (c == '?' || c == 'R' || c == 'S' || c == 'X')) {
      if (c == 'R') fpgaReloadReq = true;
      if (c == 'S') streamUntil = millis() + 40000;
      if (c == 'X') streamUntil = 0;
      if (c == '?' || c == 'R') Serial.println(stateJson());
      continue;
    }
    if (c == '\n' || c == '\r') {          // "pa=..." lines from the laptop helper over USB
      if (line.startsWith("pa=")) onPhoneAudio(line.substring(3));
      else if (line.length()) { applyQuery(line); Serial.println(stateJson()); }   // control from the laptop page
      line = "";
    } else if (line.length() < 64) line += (char)c;
  }

  if (fpgaReloadReq) {
    fpgaReloadReq = false;
    fpgaStatus = fpga::load();
    forceResendAll();
  }

  if (saveAt && (int32_t)(millis() - saveAt) > 0) { saveAt = 0; saveState(); }
}
