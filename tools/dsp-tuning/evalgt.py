import sys, numpy as np, sim
def evaluate(fr, a, play, gt, tol=0.08, verbose=True):
    host = {int(r[1]): r[0] for r in a}
    bt = np.array([(host[x["t"]] - play) / 1000 for x in fr if x["beat"]])
    best = None
    for off in np.arange(0, 0.4, 0.005):
        d = bt - off
        hits = sum(np.min(np.abs(d - g)) < tol for g in gt) if len(d) else 0
        if best is None or hits > best[0]: best = (hits, off)
    hits, off = best
    d = bt - off
    tp = [x for x in d if np.min(np.abs(gt - x)) < tol]
    fp = [x for x in d if np.min(np.abs(gt - x)) >= tol]
    lat = [x - gt[np.argmin(np.abs(gt - x))] for x in tp]
    if verbose:
        print(f"offset {off*1000:.0f} ms | kicks {len(gt)} caught {hits} ({100*hits/len(gt):.0f}%) | extra beats {len(fp)}"
              f" (silence/fan part: {sum(1 for x in fp if x < 2)}, breakdown: {sum(1 for x in fp if 14 <= x < 22)})"
              f" | jitter {np.std(lat)*1000 if lat else 0:.0f} ms")
    return hits, len(fp)
if __name__ == "__main__":
    a, play = sim.load(sys.argv[1]); gt = np.loadtxt("beats120_gt.txt")
    fr = sim.run(a); evaluate(fr, a, play, gt)
    res = sim.render_music2(fr); sim.plot(a, fr, res, "sim_test.png", "test track")
