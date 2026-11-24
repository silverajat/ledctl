import math, numpy as np
class BeatTracker:
    """tempo (autocorrelation of onset strength over 4 s) + phase-locked beat clock, 100 Hz frames"""
    N = 400
    def __init__(s, lead=0, gain=0.5, nper=4, smooth=0):
        s.gain=gain; s.nper=nper; s.smooth=smooth
        s.buf = np.zeros(s.N); s.i = 0; s.n = 0
        s.L = 50.0; s.conf = 0.0; s.next = None; s.frame = 0; s.lead = lead
        lags = np.arange(30, 101); s.lags = lags
        s.w = np.exp(-0.5 * (np.log2(lags / 52.0) / 0.8) ** 2)
    def push(s, o):
        s.buf[s.i] = o; s.i = (s.i + 1) % s.N; s.n += 1; s.frame += 1
        fired = False
        if s.n >= 200 and s.frame % 25 == 0: s.analyse()
        if s.next is not None and s.conf > 0 and s.frame >= s.next - s.lead:
            fired = True; s.next += s.L
        return fired
    def series(s):
        return np.roll(s.buf, -s.i)       # oldest .. newest
    def analyse(s):
        x = s.series(); m = min(s.n, s.N); x = x[-m:]
        x = x - x.mean()
        if np.std(x) < 1e-6: s.conf = 0; return
        r = np.array([np.dot(x[:-L], x[L:]) for L in s.lags]) / np.dot(x, x)
        sc = r * s.w
        k = int(np.argmax(sc)); L = float(s.lags[k])
        # parabolic refine
        if 0 < k < len(sc) - 1:
            a, b, c = sc[k - 1], sc[k], sc[k + 1]; den = a - 2 * b + c
            if den != 0: L += 0.5 * (a - c) / den
        conf = (r[k] - np.mean(r)) / (np.std(r) + 1e-9)
        if abs(L - s.L) / s.L < 0.08: s.L = 0.8 * s.L + 0.2 * L
        else: s.L = L
        s.conf = conf if conf > 1.5 else 0.0
        # phase: comb over the last 4 periods, find where beats sit (frames back from newest)
        Li = s.L; best = None
        xs = x
        if s.smooth: xs = np.convolve(x, np.ones(2*s.smooth+1), 'same')
        nper = max(1, min(s.nper, int((len(x) - 1) // Li)))
        for ph in range(int(Li)):
            idx = [len(x) - 1 - int(round(ph + q * Li)) for q in range(nper)]
            if idx[-1] < 0: break
            v = sum(xs[j] for j in idx)
            if best is None or v > best[0]: best = (v, ph)
        last_beat = s.frame - 1 - best[1]
        pred = last_beat + Li
        while pred < s.frame: pred += Li
        if s.next is None or s.conf == 0: s.next = pred
        else:
            # PLL: pull the running clock half way towards the measured phase
            e = (pred - s.next + Li / 2) % Li - Li / 2
            s.next += s.gain * e
