"""Known songs: a pre-planned show, the way festival LJs program a show to the track (timecode)
instead of reacting to it (busking).

Every song you play is recorded on the laptop and truth.py works out its beats, bar lines, sections and
drops from the whole song. When that song plays again, this module finds where in the song we are by
matching the live audio against the recording (level-independent, so the volume doesn't matter, and it
follows seeks), and then hands the director the real beat grid, the real section changes and the drops
*before* they happen: the beat before a drop goes dark and the drop lands exactly.

Unknown songs, films and anything else keep the live (busked) show.
"""
import bisect
import glob
import json
import os
import re
import threading

import numpy as np

from analysis import melbank

NFFT = 2048
WIN_S = 6.0          # seconds of live audio compared against the song


def fname(title):
    return re.sub(r'[<>:"/\\|?*]', "_", title).strip()[:120]


def read_wav(path):
    import wave
    w = wave.open(path, "rb")
    sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
    a = np.frombuffer(w.readframes(w.getnframes()), {2: np.int16, 4: np.int32}[sw]).astype(np.float32)
    a /= float(2 ** (8 * sw - 1))
    a = a.reshape(-1, ch)
    return a.mean(axis=1), sr


def logmel_frames(x, sr, hop):
    """the same log-mel the live analyser computes: frame j ends at sample (j+1)*hop"""
    mel, _ = melbank(sr, NFFT)
    x = np.concatenate([np.zeros(NFFT - hop, np.float32), x.astype(np.float32)])
    n = 1 + (len(x) - NFFT) // hop
    win = np.hanning(NFFT).astype(np.float32)
    out = np.empty((n, mel.shape[0]), np.float32)
    step = 2048
    for i in range(0, n, step):
        k = min(step, n - i)
        idx = (np.arange(NFFT)[None, :] + hop * np.arange(i, i + k)[:, None])
        spec = np.fft.rfft(x[idx] * win, axis=1)
        p = (spec.real ** 2 + spec.imag ** 2) / (NFFT * 0.25)
        out[i:i + k] = 10.0 * np.log10(p @ mel.T + 1e-12)
    return out


def flux(lm):
    """onset strength per frame from log-mel: level-independent (a volume change cancels out)"""
    d = np.maximum(0.0, np.diff(lm, axis=0, prepend=lm[:1]))
    return np.minimum(d, 20.0).mean(axis=1)


def shape(lm):
    """spectral shape per frame (each frame minus its own mean): also level-independent"""
    return lm - lm.mean(axis=1, keepdims=True)


class View:
    """the analysis as the director sees it, with the known song's answers laid over it"""

    def __init__(self, A):
        self.__dict__["_A"] = A

    def __getattr__(self, k):
        return getattr(self._A, k)


class SongMap:
    def __init__(self, recs_dir, sr, hop):
        self.dir = recs_dir
        self.sr, self.hop = sr, hop
        self.dt = hop / sr
        self.title = None
        self.truth = None
        self.feat = None              # (flux, shape) of the recording
        self.status = "no known song"
        self.off = None               # song time = live time + off
        self.cand = None
        self.misses = 0
        self.ring_t = []
        self.ring = []
        self.t_title = 0.0
        self.last_i = None
        self.last_sec = None
        self.last_ts = None
        self.busy = False
        self.dbg = {}
        self.n_known = len(self.known())
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- song
    def known(self):
        return sorted(os.path.basename(p)[:-11] for p in glob.glob(os.path.join(self.dir, "*.truth.json")))

    def set_song(self, title, now_t, sync=False):
        if title == self.title:
            return
        self.title = title
        self.n_known = len(self.known())
        self.truth, self.feat, self.off, self.cand = None, None, None, None
        self.last_i = self.last_sec = self.last_ts = None
        self.t_title = now_t
        self.ring_t, self.ring = [], []
        if not title or title.startswith("("):
            self.status = "no known song"
            return
        base = os.path.join(self.dir, fname(title))
        if not (os.path.exists(base + ".truth.json") and os.path.exists(base + ".wav")):
            self.status = "new song (live show)"
            return
        try:
            self.truth = json.load(open(base + ".truth.json", encoding="utf-8"))
        except Exception as e:  # noqa
            self.status = f"truth unreadable: {e}"[:60]
            return
        self.status = "known song: loading…"
        if sync:
            self._load_feat(base, title)
        else:
            threading.Thread(target=self._load_feat, args=(base, title), daemon=True).start()

    def _load_feat(self, base, title):
        cache = base + f".feat{self.sr}_{self.hop}.npz"
        try:
            if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(base + ".wav"):
                z = np.load(cache)
                lm = z["lm"]
            else:
                x, sr = read_wav(base + ".wav")
                hop = int(round(self.hop * sr / self.sr))         # same hop duration as live
                lm = logmel_frames(x, sr, hop)
                np.savez_compressed(cache, lm=lm.astype(np.float16))
            S = shape(lm.astype(np.float32))
            n = int(WIN_S / self.dt)
            Lfft = 1 << int(np.ceil(np.log2(len(S) + n)))
            FS = np.fft.rfft(S, Lfft, axis=0).astype(np.complex64)
            csum = np.concatenate([[0.0], np.cumsum((S.astype(np.float64) ** 2).sum(axis=1))])
            if self.title == title:
                self.feat = (S, FS, csum, Lfft)
                self.status = "known song: listening for where we are…"
        except Exception as e:  # noqa
            self.status = f"known song, features failed: {e}"[:70]

    # ---------------------------------------------------------------- matching
    def feed(self, A):
        if self.truth is None:
            return
        self.ring_t.append(A.t)
        self.ring.append(A.lm.astype(np.float32))
        nmax = int(WIN_S * 1.5 / self.dt)
        if len(self.ring) > nmax:
            del self.ring_t[:-nmax], self.ring[:-nmax]

    def match_async(self):
        if self.busy or self.feat is None:
            return
        self.busy = True
        threading.Thread(target=self._match_bg, daemon=True).start()

    def _match_bg(self):
        try:
            self.match_now()
        finally:
            self.busy = False

    def match_now(self):
        if self.feat is None:
            return
        n = int(WIN_S / self.dt)
        if len(self.ring) < n:
            return
        lm = np.array(self.ring[-n:])
        t_end = self.ring_t[-1]
        if np.mean(lm) < -95:                          # silence: nothing to match
            return
        S, FS, csum, Lfft = self.feat
        N = len(S)
        if N <= n:
            return
        # cosine similarity of the spectral shape at every position in the song, in one FFT
        W = shape(lm)
        FW = np.fft.rfft(W, Lfft, axis=0)
        num = np.fft.irfft((FS * np.conj(FW)).sum(axis=1), Lfft)[:N - n + 1]
        seg_norm = np.sqrt(np.maximum(csum[n:] - csum[:-n], 1e-9))[:N - n + 1]
        sim_all = num / (seg_norm * (np.linalg.norm(W) + 1e-9))
        ts_end = (np.arange(N - n + 1) + n) * self.dt
        prior_t = t_end - self.t_title                  # songs usually play from the start after the title changes
        score = sim_all + 0.03 * np.exp(-0.5 * ((ts_end - prior_t) / 20.0) ** 2)
        if self.off is not None:                        # stay where we are unless clearly elsewhere (a seek)
            score = score + 0.05 * np.exp(-0.5 * ((ts_end - (t_end + self.off)) / 0.3) ** 2)
        j = int(np.argmax(score))
        sim = float(sim_all[j])
        far = np.abs(np.arange(N - n + 1) - j) > 1.0 / self.dt
        second = float(score[far].max()) if far.any() else 0.0
        margin = float(score[j]) - second
        cc = sim
        off = (j + n) * self.dt - t_end
        good = sim > 0.8 and margin > 0.02
        cur_ok = True
        if self.off is not None:                        # does where we think we are still match?
            jc = int(round((t_end + self.off) / self.dt)) - n
            cur_ok = 0 <= jc < len(sim_all) and float(sim_all[max(0, jc - 2):jc + 3].max()) > 0.7
        with self.lock:
            if not cur_ok:                              # a seek or a different version: back to the live show
                self.off, self.misses = None, 0
                self.last_i = self.last_sec = self.last_ts = None
            if good:
                if self.off is not None and abs(off - self.off) < 0.08:
                    self.misses = 0
                    self.off = 0.8 * self.off + 0.2 * off
                elif self.cand is not None and abs(off - self.cand) < 0.08:
                    self.off, self.cand, self.misses = off, None, 0    # two agreeing matches: lock / jump
                    self.last_i = self.last_sec = self.last_ts = None
                else:
                    self.cand = off
            else:
                self.misses += 1
                if self.misses >= 3:
                    self.off = None
            if self.off is not None:
                self.status = f"known song, synced at {self._mmss(t_end + self.off)}"
            else:
                self.status = "known song: listening for where we are…"
            self.dbg = dict(corr=round(cc, 2), sim=round(sim, 2), margin=round(margin, 2), off=round(off, 2))

    @staticmethod
    def _mmss(t):
        return f"{int(t // 60)}:{int(t % 60):02d}"

    # ---------------------------------------------------------------- the plan
    def view(self, A):
        V = View(A)
        if self.off is None or self.truth is None:
            return V
        T = self.truth
        beats = T["beats"]
        ts = A.t + self.off
        i = bisect.bisect_right(beats, ts) - 1
        if i < 0 or i >= len(beats) - 1 or ts > T["dur"]:
            return V
        b0, b1 = beats[i], beats[i + 1]
        per = b1 - b0
        V.period, V.bpm = per, 60.0 / per
        V.phase = (ts - b0) / per
        V.beat = i
        V.beat_tick = self.last_i is not None and i != self.last_i
        V.locked, V.tconf = True, 1.0
        dbs = T["downbeats"]
        k = bisect.bisect_right(dbs, ts + 0.03) - 1
        if k >= 0:
            j0 = bisect.bisect_left(beats, dbs[k] - 0.03)
            V.bar_beat = (i - j0) % 4
            V.bar = k
        else:
            V.bar_beat, V.bar = i % 4, i // 4
        V.downbeat = V.beat_tick and V.bar_beat == 0
        secs = T["sections"]
        si = max(0, bisect.bisect_right([s["start"] for s in secs], ts + 0.05) - 1)
        V.section_change = V.beat_tick and self.last_sec is not None and si != self.last_sec
        if V.beat_tick or self.last_sec is None:
            self.last_sec = si                     # compare section only from beat to beat
        V.section_id = si
        V.plan_energy = float(secs[si]["energy"])
        V.plan_label = secs[si]["label"]
        drops = T.get("drops", [])
        prev_ts = self.last_ts if self.last_ts is not None else ts
        V.drop = any(prev_ts < d <= ts + 1e-3 for d in drops) and 0 <= ts - prev_ts < 0.5
        nxt = [d for d in drops if d > ts]
        V.drop_in = (nxt[0] - ts) if nxt else None
        past = [d for d in drops if d <= ts]
        V.last_drop_t = A.t - (ts - past[-1]) if past else -1e9
        # build-up: the 8 bars before a drop, if that part is quieter than what's coming
        V.plan_build = 0.0
        if V.drop_in is not None:
            span = 32 * per
            nsec = secs[min(len(secs) - 1, bisect.bisect_right([s["start"] for s in secs], nxt[0] + 0.05) - 1)]
            if V.drop_in < span and secs[si]["energy"] < nsec["energy"] - 0.1:
                V.plan_build = float(np.clip(1 - V.drop_in / span, 0, 1))
        V.build, V.building = V.plan_build, V.plan_build > 0.2
        V.planned = True
        V.song_t = ts
        self.last_i, self.last_ts = i, ts
        return V

    def state(self):
        s = {"status": self.status}
        if self.off is not None and self.truth is not None and self.ring_t:
            ts = self.ring_t[-1] + self.off
            nxt = [d for d in self.truth.get("drops", []) if d > ts]
            s["at"] = round(ts, 1)
            s["next_drop"] = round(nxt[0] - ts, 1) if nxt else None
        return s
