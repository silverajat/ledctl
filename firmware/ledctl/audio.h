// Audio front end on the ESP32: takes the FPGA's 10 ms telemetry (log levels per band)
// and removes steady background noise (fan, aircon) with a per-band Kalman noise-floor tracker.
//
// Units: the FPGA sends log2(level) in 1/4 steps, so 1 step = 0.75 dB.
//
// Noise model (per band):   floor[k+1] = floor[k] + w,  w ~ N(0, Q)      (background drifts slowly)
//                           z[k]       = floor[k] + v,  v ~ N(0, R)      (fan noise flickers around it)
// Music shows up as positive innovations, so only samples within 2 sigma above the predicted floor
// (or below it) update the floor (a gated / robust Kalman filter); R (how much the fan flickers)
// is learned from those samples only and capped, so steady quiet music can't be learned as "fan".
// Signal = how far the band is above floor + 2 sigma  -> the fan maps to ~0.
#pragma once
#include <Arduino.h>

struct NoiseKF {
  float x = 0, P = 25, R = 4;   // floor estimate, its variance, measurement noise variance
  bool init = false;
  float snr = 0, peak = 10, lo = 0, out = 0;  // dB-ish above the noise, slow peak / trough, normalised 0..1

  void update(float z) {
    const float Q = 0.0005f;
    if (!init) { x = z; P = 4; init = true; }
    P += Q;
    float S = P + R, y = z - x;
    if (y < 2.0f * sqrtf(S)) {             // looks like background (or quieter): learn
      float K = P / S;
      K = (y < 0) ? fmaxf(K, 0.05f) : fminf(K, 0.0015f);   // down fast, up only over minutes
      x += K * y;
      P *= (1 - K);
      if (fabsf(y) < 2.0f * sqrtf(S)) R += 0.005f * (y * y - R);
      R = constrain(R, 0.5f, 5.0f);
    } else {                                // music: hold the floor, but let it creep up over many minutes
      x += 0.00005f * y;                    // (so turning the fan up eventually gets learned)
      if (P > 2) P = 2;
    }
    snr = z - x - 2.0f * sqrtf(R);
    if (snr < 0) snr = 0;
    // normalise inside the music's own dynamic range: lo follows the quiet moments, peak the loud ones.
    // (with a mic the fan sets the bottom; with a clean digital feed the song's troughs do)
    if (snr < lo) lo += (snr - lo) * 0.05f; else lo += (snr - lo) * 0.002f;
    if (snr > peak) peak += (snr - peak) * 0.3f; else peak += (snr - peak) * 0.0015f;
    float base = lo * 0.85f;                     // keep a little headroom under the troughs
    if (peak < base + 10) peak = base + 10;      // at least ~7.5 dB of range
    out = constrain((snr - base) / (peak - base), 0.0f, 1.0f);
  }
  float floorDb() const { return x * 0.75f; }
  float sigmaDb() const { return sqrtf(R) * 0.75f; }
};

// Beat tracker: tempo from the autocorrelation of the onset strength over the last 4 s,
// then a phase-locked beat clock (PLL) so beats land on the grid even when single hits are weak.
struct BeatTracker {
  static const int N = 400;          // 4 s of 10 ms bins
  int16_t buf[N] = {0};
  int head = 0;
  uint32_t n = 0, frame = 0;
  float L = 50, conf = 0;            // period in 10 ms frames, confidence (z-score of the ACF peak)
  float next = -1;
  float w[71];
  int16_t x[N];

  BeatTracker() { for (int i = 0; i < 71; i++) { float l = log2f((30 + i) / 52.0f) / 0.8f; w[i] = expf(-0.5f * l * l); } }

  bool push(float o) {               // o = onset strength of this 10 ms bin; returns true on a beat
    buf[head] = (int16_t)constrain(o * 32, 0.0f, 32000.0f);
    head = (head + 1) % N; n++; frame++;
    if (n >= 200 && frame % 25 == 0) analyse();
    if (next >= 0 && conf > 0 && frame >= next - 2) { next += L; return true; }   // fire 20 ms early (latency)
    return false;
  }

  void analyse() {
    int m = n < (uint32_t)N ? n : N;
    int32_t sum = 0;
    for (int i = 0; i < m; i++) { x[i] = buf[(head - m + i + N) % N]; sum += x[i]; }
    int16_t mean = sum / m;
    int64_t e = 0;
    for (int i = 0; i < m; i++) { x[i] -= mean; e += (int32_t)x[i] * x[i]; }
    if (e == 0) { conf = 0; return; }
    float r[71], rs = 0, rs2 = 0; int best = 0; float bestSc = -1e9;
    for (int k = 0; k < 71; k++) {
      int lag = 30 + k; int64_t acc = 0;
      for (int i = lag; i < m; i++) acc += (int32_t)x[i] * x[i - lag];
      r[k] = (float)acc / (float)e; rs += r[k]; rs2 += r[k] * r[k];
      float sc = r[k] * w[k];
      if (sc > bestSc) { bestSc = sc; best = k; }
    }
    float Lnew = 30 + best;
    if (best > 0 && best < 70) {
      float a = r[best - 1] * w[best - 1], b = bestSc, c = r[best + 1] * w[best + 1], d = a - 2 * b + c;
      if (d != 0) Lnew += 0.5f * (a - c) / d;
    }
    float mu = rs / 71, sd = sqrtf(fmaxf(rs2 / 71 - mu * mu, 1e-9f));
    float c = (r[best] - mu) / sd;
    if (fabsf(Lnew - L) / L < 0.08f) L = 0.8f * L + 0.2f * Lnew; else L = Lnew;
    conf = c > 1.5f ? c : 0;
    // phase: comb (3-tap smoothed) over up to 7 periods
    int nper = (int)((m - 1) / L); if (nper > 7) nper = 7; if (nper < 1) nper = 1;
    int bestPh = 0; int64_t bestV = INT64_MIN;
    for (int ph = 0; ph < (int)L; ph++) {
      int64_t v = 0;
      for (int q = 0; q < nper; q++) {
        int j = m - 1 - (int)lroundf(ph + q * L);
        if (j < 2 || j > m - 3) continue;
        for (int t = -2; t <= 2; t++) v += x[j + t];
      }
      if (v > bestV) { bestV = v; bestPh = ph; }
    }
    float pred = (float)frame - 1 - bestPh + L;
    while (pred < frame) pred += L;
    if (next < 0 || conf == 0) next = pred;
    else { float err = fmodf(pred - next + L * 1.5f, L) - L / 2; next += 0.1f * err; }
  }
  float bpm() const { return 6000.0f / L; }
};

struct Audio {
  NoiseKF lvl, bass, mid, high;
  uint8_t raw[4] = {0, 0, 0, 0};       // level, bass, mid, high as received
  float env = 0, envSlow = 0;          // overall loudness (0..1): fast, ~3 s
  float b = 0, m = 0, h = 0;           // smoothed band energies (0..1)
  float hSlow = 0, sparkle = 0;        // hi-hat transients
  uint32_t lastFrame = 0;
  bool live() const { return millis() - lastFrame < 500; }

  // onset (beat) detector from spectral flux: sudden rises in any band above the noise
  float prevSnr[3] = {0, 0, 0}, prevSnr2[3] = {0, 0, 0};
  float fluxMean = 0, fluxVar = 1;
  uint32_t lastOnset = 0;
  bool onset = false;      // set for one frame
  float flux = 0;

  BeatTracker tracker;
  float binOnset = 0;                // max flux inside the current 10 ms bin

  // call every 10 ms; returns true when the beat clock ticks (only while it's locked)
  bool tick() { bool f = tracker.push(binOnset); binOnset = 0; return f && tracker.conf >= 3.0f; }
  bool locked() const { return tracker.conf >= 3.0f; }

  void detectOnset(float sens) {
    float s[3] = {bass.snr, mid.snr, high.snr};
    const float w[3] = {1.0f, 0.8f, 0.6f};
    flux = 0;
    for (int i = 0; i < 3; i++) { float d = s[i] - prevSnr[i]; if (d > 0) flux += w[i] * d; prevSnr[i] = s[i]; }
    float fl1 = 0;  // equal-weight flux for the tempo tracker
    for (int i = 0; i < 3; i++) { float d = s[i] - prevSnr2[i]; if (d > 0) fl1 += d; prevSnr2[i] = s[i]; }
    if (fl1 > binOnset) binOnset = fl1;
    float sd = sqrtf(fluxVar);
    float thr = fluxMean + fmaxf(1.6f * sd, 4.5f - 0.3f * sens);   // sens 1..10 -> min jump 4.2..1.5 steps
    onset = false;
    if (flux > thr && millis() - lastOnset > 140) { onset = true; lastOnset = millis(); }
    fluxMean += (flux - fluxMean) * 0.02f;                       // ~0.5 s
    fluxVar += ((flux - fluxMean) * (flux - fluxMean) - fluxVar) * 0.02f;
  }

  void frame(uint8_t level, uint8_t lb, uint8_t lm, uint8_t lh, float sens) {
    raw[0] = level; raw[1] = lb; raw[2] = lm; raw[3] = lh;
    lastFrame = millis();
    lvl.update(level); bass.update(lb); mid.update(lm); high.update(lh);
    detectOnset(sens);
    auto follow = [](float &v, float t, float up, float down) { v += (t - v) * (t > v ? up : down); };
    follow(env, lvl.out, 0.6f, 0.12f);
    follow(envSlow, lvl.out, 0.004f, 0.004f);
    follow(b, bass.out, 0.7f, 0.15f);
    follow(m, mid.out, 0.6f, 0.15f);
    follow(h, high.out, 0.8f, 0.25f);
    follow(hSlow, high.out, 0.05f, 0.05f);
    if (high.out - hSlow > 0.35f && high.out > 0.5f) sparkle = 1.0f;
  }
};
