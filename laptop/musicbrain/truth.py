"""Reference answers for a recorded song, worked out offline with the whole song available
(the live brain only ever hears the past, so it can't be this accurate).

  beats + downbeats : "Beat This!" (CPJKU, 2024), a state-of-the-art beat tracker
  sections          : self-similarity over the whole song (where the music changes character),
                      snapped to bars, then each section gets an energy level and a label
  drops / build-ups : sections where the full bass and kick come in after a quieter, bass-light part

  python truth.py recordings/Artist - Song.wav    ->  recordings/Artist - Song.truth.json
"""
import json
import math
import os
import sys
import wave
import numpy as np


def load_wav(path):
    w = wave.open(path, "rb")
    sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
    a = np.frombuffer(w.readframes(w.getnframes()), {2: np.int16, 4: np.int32}[sw]).astype(np.float32)
    a /= float(2 ** (8 * sw - 1))
    return a.reshape(-1, ch).mean(axis=1), sr


def beats_of(x, sr):
    from beat_this.inference import Audio2Beats
    model = Audio2Beats(checkpoint_path="final0", device="cpu", dbn=False)
    b, d = model(x.astype(np.float64), sr)
    return [float(t) for t in b], [float(t) for t in d]


def beat_features(x, sr, beats):
    """per-beat: 24 log-mel bands, 12 chroma, loudness, sub-bass, highs"""
    nfft, hop = 4096, 1024
    win = np.hanning(nfft)
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    n = 1 + (len(x) - nfft) // hop
    idx = np.arange(nfft)[None, :] + hop * np.arange(n)[:, None]
    F = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2
    t = (np.arange(n) * hop + nfft / 2) / sr
    mel_edges = 700 * (10 ** (np.linspace(2595 * np.log10(1 + 40 / 700), 2595 * np.log10(1 + 12000 / 700), 26) / 2595) - 1)
    mel = np.stack([F[:, (freqs >= mel_edges[i]) & (freqs < mel_edges[i + 2])].sum(1) for i in range(24)], 1)
    sel = (freqs > 60) & (freqs < 4000)
    pc = (np.round(69 + 12 * np.log2(np.maximum(freqs[sel], 1) / 440)) % 12).astype(int)
    chroma = np.stack([F[:, sel][:, pc == k].sum(1) for k in range(12)], 1)
    chroma /= chroma.max(1, keepdims=True) + 1e-12
    loud = 10 * np.log10(F.sum(1) + 1e-12)
    sub = 10 * np.log10(F[:, (freqs > 30) & (freqs < 120)].sum(1) + 1e-12)
    hi = 10 * np.log10(F[:, freqs > 6000].sum(1) + 1e-12)
    edges = list(beats) + [beats[-1] + (beats[-1] - beats[-2])]
    rows = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (t >= a) & (t < b)
        if not m.any():
            m = np.argmin(np.abs(t - a)) == np.arange(n)
        rows.append(np.concatenate([np.log10(mel[m].mean(0) + 1e-12), chroma[m].mean(0),
                                    [loud[m].mean(), sub[m].mean(), hi[m].mean()]]))
    return np.array(rows)


def sections_of(feat, beats, downbeats, kernel=16):
    """Foote novelty on the beat-synchronous self-similarity matrix, peaks snapped to downbeats"""
    X = feat[:, :36].copy()
    X = (X - X.mean(0)) / (X.std(0) + 1e-6)
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
    S = X @ X.T
    k = kernel
    g = np.outer(np.hanning(2 * k), np.hanning(2 * k))
    cb = np.block([[np.ones((k, k)), -np.ones((k, k))], [-np.ones((k, k)), np.ones((k, k))]]) * g
    n = len(S)
    Sp = np.pad(S, k, mode="edge")
    nov = np.array([np.sum(Sp[i:i + 2 * k, i:i + 2 * k] * cb) for i in range(n)])
    nov = np.maximum(nov, 0)
    nov /= nov.max() + 1e-9
    db_idx = [int(np.argmin(np.abs(np.array(beats) - d))) for d in downbeats]
    peaks = []
    thr = max(0.15, float(np.percentile(nov, 75)))
    for i in range(2, n - 2):
        if nov[i] >= thr and nov[i] == nov[max(0, i - 8):i + 9].max():
            j = min(db_idx, key=lambda d: abs(d - i)) if db_idx else i       # snap to the nearest bar line
            if abs(j - i) <= 2 and all(abs(j - p) >= 16 for p in peaks):
                peaks.append(j)
    bounds = [0] + sorted(peaks) + [n]
    loud = feat[:, -3]
    sub = feat[:, -2]
    ref = np.percentile(loud, 90)
    sref = np.percentile(sub, 90)
    secs = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a < 4:
            continue
        e = float(np.clip(1 + (loud[a:b].mean() - ref) / 12, 0, 1))
        bass = float(np.clip(1 + (sub[a:b].mean() - sref) / 12, 0, 1))
        secs.append({"start": round(beats[a], 3), "end": round(beats[b] if b < n else beats[-1] + 0.5, 3),
                     "i0": a, "i1": b, "energy": round(e, 2), "bass": round(bass, 2)})
    # labels: by energy within the song, plus repetition (the loudest repeated section = chorus)
    if secs:
        es = np.array([s["energy"] for s in secs])
        cent = [X[s["i0"]:s["i1"]].mean(0) for s in secs]
        for s, c in zip(secs, cent):
            rep = sum(1 for c2 in cent if c2 is not c and float(np.dot(c, c2) / (np.linalg.norm(c) * np.linalg.norm(c2) + 1e-9)) > 0.8)
            s["repeats"] = rep
        hi_e = np.percentile(es, 70)
        lo_e = np.percentile(es, 30)
        for i, s in enumerate(secs):
            if i == 0 and s["energy"] < hi_e:
                s["label"] = "intro"
            elif i == len(secs) - 1 and s["energy"] < hi_e:
                s["label"] = "outro"
            elif s["energy"] >= hi_e:
                s["label"] = "chorus" if s["repeats"] else "peak"
            elif s["energy"] <= lo_e:
                s["label"] = "break"
            else:
                s["label"] = "verse"
    # drops: a jump in sub-bass + loudness into a section, after a bass-light one
    drops, builds = [], []
    for p, s in zip(secs[:-1], secs[1:]):
        if s["bass"] - p["bass"] > 0.3 and s["energy"] - p["energy"] > 0.12 and s["energy"] > 0.7:
            drops.append(s["start"])
            hi = feat[p["i0"]:p["i1"], -1]
            if len(hi) >= 8 and np.polyfit(np.arange(len(hi)), hi, 1)[0] > 0.1:
                builds.append([p["start"], p["end"]])
    for s in secs:
        s.pop("i0"), s.pop("i1")
    return secs, drops, builds, nov


def main(path):
    x, sr = load_wav(path)
    beats, downbeats = beats_of(x, sr)
    if len(beats) < 16:
        raise SystemExit("too few beats")
    feat = beat_features(x, sr, beats)
    secs, drops, builds, _ = sections_of(feat, beats, downbeats)
    ibi = np.diff(beats)
    out = {"file": os.path.basename(path), "dur": round(len(x) / sr, 2), "bpm": round(60 / float(np.median(ibi)), 1),
           "beats": [round(b, 3) for b in beats], "downbeats": [round(d, 3) for d in downbeats],
           "sections": secs, "drops": drops, "builds": builds}
    dst = path[:-4] + ".truth.json"
    json.dump(out, open(dst, "w"), indent=1)
    print(f"{os.path.basename(path)}: {out['bpm']} bpm, {len(beats)} beats, {len(secs)} sections "
          f"({', '.join(s['label'] for s in secs)}), drops {drops}")


if __name__ == "__main__":
    import glob
    paths = sys.argv[1:] or [w for w in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings", "*.wav")))
                             if not os.path.exists(w[:-4] + ".truth.json")]
    for p in paths:
        try:
            main(p)
        except Exception as e:  # noqa
            print(f"{os.path.basename(p)}: failed: {e}")
