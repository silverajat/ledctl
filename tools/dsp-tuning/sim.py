"""Replays a board capture through the ESP32 audio + light algorithm and plots what the strip would do.
usage: python3 sim.py capture.csv [out.png]
"""
import sys, math, colorsys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def load(path):
    rows, play = [], None
    for line in open(path):
        p = line.strip().split(",")
        if p[0] == "PLAY": play = int(p[1]); continue
        if len(p) != 8: continue
        try: rows.append([int(v) for v in p])
        except ValueError: pass
    a = np.array(rows)
    return a, play

class NoiseKF:
    def __init__(s): s.x = 0; s.P = 25; s.R = 4; s.init = False; s.snr = 0; s.peak = 10; s.out = 0
    def update(s, z, P_learn_sig=2.0):
        Q = 0.0005
        if not s.init: s.x = z; s.P = 4; s.init = True
        s.P += Q
        S = s.P + s.R; y = z - s.x
        if y < P_learn_sig * math.sqrt(S):
            K = s.P / S
            K = max(K, 0.05) if y < 0 else min(K, 0.0015)   # down fast, up very slowly (~minutes)
            s.x += K * y; s.P *= (1 - K)
            s.R += 0.005 * (y * y - s.R); s.R = min(max(s.R, 0.5), 5.0)
        else:
            s.x += 0.00005 * y; s.P = min(s.P, 2)
        s.snr = max(0.0, z - s.x - 2.0 * math.sqrt(s.R))
        s.peak += (s.snr - s.peak) * (0.3 if s.snr > s.peak else 0.0015)
        s.peak = max(s.peak, 8)
        s.out = min(s.snr / s.peak, 1.0)

def run(a, sens=5, P=None):
    P = P or {}
    kf = [NoiseKF() for _ in range(4)]
    prev = [0, 0, 0]; fm = 0; fv = 1; last_on = -1e9; last_any = -1e9; lastcnt = None
    env = envs = b = m = h = hs = sp = 0
    out = []
    for r in a:
        t, cnt = r[1], r[7]
        level, lb, lm, lh = r[3], r[4], r[5], r[6]
        for k, z in zip(kf, (level, lb, lm, lh)): k.update(z)
        s = [kf[1].snr, kf[2].snr, kf[3].snr]; w = [1.0, 0.8, 0.6]
        flux = sum(wi * max(0, si - pi) for wi, si, pi in zip(w, s, prev)); prev = s
        thr = fm + max(P.get("fk", 1.6) * math.sqrt(fv), P.get("fmin", 4.5) - 0.3 * sens)
        onset = flux > thr and t - last_on > 140
        if onset: last_on = t
        fm += (flux - fm) * 0.02; fv += ((flux - fm) ** 2 - fv) * 0.02
        gate = 12 - sens
        fb = lastcnt is not None and cnt != lastcnt and (kf[1].snr > gate or kf[0].snr > gate)
        lastcnt = cnt
        beat = (fb or onset) and t - last_any > 140
        if beat: last_any = t
        f = lambda v, tg, up, dn: v + (tg - v) * (up if tg > v else dn)
        env = f(env, kf[0].out, 0.6, 0.12); envs = f(envs, kf[0].out, 0.004, 0.004)
        b = f(b, kf[1].out, 0.7, 0.15); m = f(m, kf[2].out, 0.6, 0.15); h = f(h, kf[3].out, 0.8, 0.25)
        out.append(dict(t=t, beat=beat, env=env, envs=envs, b=b, m=m, h=h, raw=(level, lb, lm, lh),
                        floor=kf[0].x, snr=kf[0].snr))
    return out

def render_music(fr, P=None):
    """the Music-mode light engine; returns per-frame (r,g,b) 0..1 after gamma, and brightness"""
    P = P or {}
    hue_base = 300.0; hue_now = 300.0; tilt = 0; flash = 0; kick = 0; glow = 0.0; phrase = 0
    bright = 0.0; last_beat = -1e9
    res = []
    prev_t = fr[0]["t"]
    for x in fr:
        dt = max(0.001, (x["t"] - prev_t) / 1000); prev_t = x["t"]
        flash *= math.exp(-dt / P.get("flash_tau", 0.28)); kick *= math.exp(-dt / 0.5)
        if x["beat"]:
            last_beat = x["t"]; phrase += 1
            flash = min(1.0, flash + 0.75); kick = 1.0
            hue_base += 70 if phrase % 8 == 0 else 6 + 10 * x["env"]
        tilt += ((x["h"] - x["b"]) - tilt) * 0.02
        pull = min(abs(tilt) * 0.8, 0.5)
        wc = 345.0 if tilt < 0 else 200.0
        d0 = (wc - hue_base + 540) % 360 - 180
        target = hue_base + d0 * pull + 15 * kick
        quiet = x["env"] < 0.03 and x["t"] - last_beat > 3000
        if quiet: hue_base += 0.08; target = hue_base
        dh = (target - hue_now + 540) % 360 - 180
        hue_now = (hue_now + dh * P.get("hue_rate", 0.04)) % 360
        energy = max(x["b"], 0.75 * x["m"], 0.5 * x["h"])
        # perceptual brightness: solid floor, energy swells it, beats push it up, all smoothed
        glow += ((P.get("floor", 0.55) + P.get("e_gain", 0.3) * energy) - glow) * (0.08 if energy > glow else 0.03)
        target_b = min(1.0, glow + P.get("beat_gain", 0.28) * flash)
        bright += (target_b - bright) * (0.5 if target_b > bright else 0.12)
        sat = 1.0
        r, g, bb = colorsys.hsv_to_rgb(hue_now / 360, sat, 1.0)
        v = bright ** 2.2
        res.append((r * v, g * v, bb * v, bright, hue_now))
    return res

def plot(a, fr, res, out, title):
    t = np.array([x["t"] for x in fr]); t = (t - t[0]) / 1000
    fig, ax = plt.subplots(4, 1, figsize=(14, 9), sharex=True, gridspec_kw=dict(height_ratios=[1, 2, 2, 1.2]))
    rgb = np.array([[r[0], r[1], r[2]] for r in res])
    disp = np.clip(rgb ** (1 / 2.2), 0, 1)       # what the eye sees
    ax[0].imshow(disp[None, :, :], aspect="auto", extent=[t[0], t[-1], 0, 1]); ax[0].set_yticks([]); ax[0].set_title(title)
    raw = np.array([x["raw"] for x in fr])
    for i, n in enumerate(["level", "bass", "mid", "high"]): ax[1].plot(t, raw[:, i], lw=0.8, label=n)
    ax[1].plot(t, [x["floor"] for x in fr], "k--", lw=1, label="noise floor (KF)"); ax[1].legend(loc="upper right", fontsize=8); ax[1].set_ylabel("log level (0.75 dB)")
    for k in ["env", "b", "m", "h"]: ax[2].plot(t, [x[k] for x in fr], lw=0.8, label=k)
    bt = [tt for tt, x in zip(t, fr) if x["beat"]]
    ax[2].vlines(bt, 0, 1.05, color="k", lw=0.6, alpha=0.5, label="beats"); ax[2].legend(loc="upper right", fontsize=8)
    ax[3].plot(t, [r[3] for r in res], "k", lw=1); ax[3].set_ylim(0, 1.05); ax[3].set_ylabel("brightness"); ax[3].set_xlabel("s")
    plt.tight_layout(); plt.savefig(out, dpi=80)

if __name__ == "__main__":
    a, play = load(sys.argv[1])
    fr = run(a)
    res = render_music(fr)
    plot(a, fr, res, sys.argv[2] if len(sys.argv) > 2 else "sim.png", sys.argv[1])
    print("frames", len(fr), "beats", sum(x["beat"] for x in fr))

PALETTE = [330, 290, 250, 210, 188, 345, 270, 15, 225, 310, 35, 200]   # no greens / yellow-greens

def pal_hue(pos):
    n = len(PALETTE); i = int(math.floor(pos)) % n; f = pos - math.floor(pos)
    f = f * f * (3 - 2 * f)
    a, b = PALETTE[i], PALETTE[(i + 1) % n]
    d = (b - a + 540) % 360 - 180
    return (a + d * f) % 360

def render_music2(fr, P=None):
    P = P or {}
    pos = 0.0; pos_t = 0.0; tilt = 0; flash = 0; kick = 0; glow = 0.55; phrase = 0
    bright = 0.0; last_beat = -1e9; hue_now = PALETTE[0]
    res = []; prev_t = fr[0]["t"]
    for x in fr:
        dt = max(0.001, (x["t"] - prev_t) / 1000); prev_t = x["t"]
        flash *= math.exp(-dt / 0.28); kick *= math.exp(-dt / 0.5)
        if x["beat"]:
            last_beat = x["t"]; phrase += 1
            flash = min(1.0, flash + 0.75); kick = 1.0
            pos_t += 1.0 if phrase % 8 == 0 else 0.12 + 0.12 * x["env"]
        quiet = x["env"] < 0.03 and x["t"] - last_beat > 3000
        if quiet: pos_t += dt / 8.0           # idle: slow walk through the palette
        pos += (pos_t - pos) * 0.03           # glide (~0.3 s)
        tilt += ((x["h"] - x["b"]) - tilt) * 0.02
        target = pal_hue(pos) + max(-20, min(20, -25 * tilt)) * 0 + 12 * kick
        dh = (target - hue_now + 540) % 360 - 180
        hue_now = (hue_now + dh * 0.12) % 360
        energy = max(x["b"], 0.75 * x["m"], 0.5 * x["h"])
        glow += ((0.55 + 0.3 * energy) - glow) * (0.08 if 0.55 + 0.3 * energy > glow else 0.03)
        tb = min(1.0, glow + 0.28 * flash)
        bright += (tb - bright) * (0.5 if tb > bright else 0.12)
        r, g, b = colorsys.hsv_to_rgb(hue_now / 360, 1.0, 1.0); v = bright ** 2.2
        res.append((r * v, g * v, b * v, bright, hue_now))
    return res
