"""Replay recorded songs through the live (real-time) brain and score it against the reference answers.

  python tools/score.py                 # all recordings that have a .truth.json
  python tools/score.py --params x.json
"""
import glob, json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from analysis import Analyzer
import audio_in


def f1(est, ref, tol):
    est, ref = np.asarray(est), np.asarray(ref)
    if len(est) == 0 or len(ref) == 0:
        return 0.0
    used = np.zeros(len(ref), bool)
    hit = 0
    for e in est:
        j = np.argmin(np.abs(ref - e))
        if abs(ref[j] - e) <= tol and not used[j]:
            used[j] = True
            hit += 1
    p, r = hit / len(est), hit / len(ref)
    return 0.0 if hit == 0 else 2 * p * r / (p + r)


def run(wav, params=None, truth=None):
    truth = truth or json.load(open(wav[:-4] + ".truth.json"))
    src = audio_in.WavSource(512, wav, realtime=False)
    A = Analyzer(src.sr, 512, params=params)
    ticks, downs, secs, drops, bpms = [], [], [], [], []
    while True:
        b = src.read()
        if b is None:
            break
        A.process(b)
        if A.beat_tick and A.locked:
            ticks.append(A.t)
            if A.downbeat:
                downs.append(A.t)
        if A.section_change:
            secs.append(A.t)
        if A.drop:
            drops.append(A.t)
        if A.n % 10 == 0 and A.t > 10 and not A.silence:
            bpms.append(A.bpm)
    tb, td = np.array(truth["beats"]), np.array(truth["downbeats"])
    tbpm = truth["bpm"]
    bpms = np.array(bpms) if bpms else np.array([0.0])
    tempo_ok = float(np.mean(np.abs(bpms / tbpm - 1) < 0.04))
    tempo_oct = float(np.mean(np.min([np.abs(bpms / (tbpm * k) - 1) for k in (0.5, 1, 2)], axis=0) < 0.04))
    beat_f = f1(ticks, tb, 0.07)
    # downbeats: of our downbeats that land on a real beat, how many are real downbeats
    on = [d for d in downs if np.min(np.abs(tb - d)) <= 0.07]
    down_acc = float(np.mean([np.min(np.abs(td - d)) <= 0.07 for d in on])) if on else 0.0
    tbound = [s["start"] for s in truth["sections"][1:]]
    sec_f = f1(secs, tbound, 2.0)
    drop_f = f1(drops, truth["drops"], 1.0) if (truth["drops"] or drops) else 1.0
    score = 0.35 * beat_f + 0.2 * tempo_ok + 0.1 * tempo_oct + 0.15 * down_acc + 0.1 * sec_f + 0.1 * drop_f
    return {"song": os.path.basename(wav)[:-4], "score": round(score, 3), "beatF": round(beat_f, 3),
            "tempo": round(tempo_ok, 2), "tempo_any_octave": round(tempo_oct, 2), "true_bpm": tbpm,
            "our_bpm": round(float(np.median(bpms)), 1), "downbeat": round(down_acc, 2),
            "sectionF": round(sec_f, 2), "dropF": round(drop_f, 2), "drops": [round(d, 1) for d in drops],
            "true_drops": truth["drops"]}


def all_songs():
    return sorted(w for w in glob.glob(os.path.join(HERE, "recordings", "*.wav")) if os.path.exists(w[:-4] + ".truth.json"))


if __name__ == "__main__":
    params = json.load(open(sys.argv[sys.argv.index("--params") + 1])) if "--params" in sys.argv else None
    songs = [a for a in sys.argv[1:] if a.endswith(".wav")] or all_songs()
    rows = [run(w, params) for w in songs]
    for r in rows:
        print(json.dumps(r))
    if rows:
        print("MEAN score", round(float(np.mean([r["score"] for r in rows])), 3))
