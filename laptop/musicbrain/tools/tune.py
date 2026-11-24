"""Search the live brain's settings against the reference answers of all recorded songs.
Writes tuned.json (loaded by the brain at start) if it beats the current settings.

  python tools/tune.py [trials]
"""
import json, os, random, sys, time
import numpy as np
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
import analysis
from score import run, all_songs

RANGES = dict(kick_floor=(3, 10), snare_floor=(1.5, 6), pll_gain_free=(0.2, 0.9), pll_gain_locked=(0.05, 0.4),
              kick_pull=(0.0, 0.5), kick_window=(0.05, 0.2), tempo_center=(85, 130), tempo_width=(0.4, 1.2),
              genre_pull=(0.0, 1.0), nov_thr=(1.0, 2.6), lock_conf=(0.2, 0.6), sharp_thr=(1.0, 2.5))


def evaluate(params, songs, truths):
    rows = [run(w, params, t) for w, t in zip(songs, truths)]
    return float(np.mean([r["score"] for r in rows])), rows


def main():
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    songs = all_songs()
    if not songs:
        print("no recordings with reference answers yet"); return
    truths = [json.load(open(w[:-4] + ".truth.json")) for w in songs]
    base = dict(analysis.PARAMS)
    best_s, rows = evaluate(base, songs, truths)
    start_s = best_s
    best = dict(base)
    print(f"current settings: {best_s:.3f}  ({len(songs)} songs)")
    t0 = time.time()
    for i in range(trials):
        cand = dict(best)
        for k in random.sample(list(RANGES), k=random.randint(1, 3)):     # small steps around the best so far
            lo, hi = RANGES[k]
            cand[k] = float(np.clip(cand[k] + random.gauss(0, (hi - lo) * 0.18), lo, hi))
        s, _ = evaluate(cand, songs, truths)
        if s > best_s:
            best_s, best = s, cand
            print(f"  trial {i}: {s:.3f}  <- better", {k: round(v, 3) for k, v in cand.items() if cand[k] != base[k]})
    print(f"done in {time.time() - t0:.0f}s: {start_s:.3f} -> {best_s:.3f}")
    if best_s > start_s + 0.005:
        json.dump({k: round(v, 4) for k, v in best.items()}, open(os.path.join(HERE, "tuned.json"), "w"), indent=1)
        print("saved tuned.json")
    _, rows = evaluate(best, songs, truths)
    for r in rows:
        print(json.dumps(r))


if __name__ == "__main__":
    main()
