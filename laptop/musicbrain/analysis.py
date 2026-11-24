"""Music analysis: turns raw audio into the things a lighting designer listens for.

Per hop (~10 ms) it produces:
  - 40 mel bands, each normalised to its own recent range
  - drum hits: kick, snare, hat (separate onset detectors, adaptive thresholds)
  - tempo, beat phase (PLL), beat number, bar position and downbeat
  - energy (how loud relative to this song), intensity (energy + bass + drum density)
  - chroma, key (major/minor), chord changes, lead pitch (for the melody layer)
  - section events: novelty (something changed), build-up progress, drop
  - drums vs. sustained sound (a cheap harmonic/percussive split), stereo balance,
    risers (the rising noise sweep before a drop) and impacts (big broadband hits, also in films)
All of it is causal (real time): nothing looks ahead.
"""
import math
import numpy as np


def hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def mel_to_hz(m):
    return 700.0 * (10 ** (m / 2595.0) - 1.0)


def melbank(sr, nfft, nb=40, fmin=30.0, fmax=16000.0):
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    fmax = min(fmax, sr / 2 - 100)
    pts = mel_to_hz(np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), nb + 2))
    fb = np.zeros((nb, len(freqs)), np.float32)
    for i in range(nb):
        lo, c, hi = pts[i], pts[i + 1], pts[i + 2]
        up = (freqs - lo) / max(c - lo, 1e-6)
        dn = (hi - freqs) / max(hi - c, 1e-6)
        fb[i] = np.clip(np.minimum(up, dn), 0, None)
        if fb[i].sum() == 0:                       # very narrow low bands: take the nearest bin
            fb[i, np.argmin(np.abs(freqs - c))] = 1.0
    centers = pts[1:-1]
    return fb, centers


# Krumhansl-Kessler key profiles
KK_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NOTE = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def ema(old, new, a):
    return old + (new - old) * a


def alpha(tau, fps):
    """smoothing coefficient for a time constant tau (seconds) at fps updates per second"""
    return 1.0 - math.exp(-1.0 / max(tau * fps, 1e-6))


class Onset:
    """Peak-picking onset detector on a flux signal with an adaptive threshold."""

    def __init__(self, fps, k=1.5, floor=0.6, gap=0.12, tau=0.6):
        self.a = alpha(tau, fps)
        self.k, self.floor, self.gap = k, floor, gap
        self.mean, self.var = 0.0, 1.0
        self.h = [0.0, 0.0, 0.0]
        self.last = -1e9
        self.strength = 0.0     # 0..1 of the last hit
        self.peak = 1.0         # slow max of hit sizes, for strength normalisation

    def push(self, x, t):
        h = self.h
        h.pop(0); h.append(x)
        thr = self.mean + max(self.k * math.sqrt(self.var), self.floor)
        hit = False
        # h[1] is a local maximum above threshold -> hit (one hop of delay)
        if h[1] > thr and h[1] >= h[0] and h[1] >= h[2] and t - self.last > self.gap:
            hit = True
            self.last = t
            self.peak = max(self.peak * 0.98, h[1])
            self.strength = min(1.0, (h[1] - self.mean) / max(self.peak - self.mean, 1e-6))
            self.strength = max(self.strength, 0.25)
        d = x - self.mean
        self.mean += d * self.a
        self.var += (d * d - self.var) * self.a
        return hit


# tunable settings; tools/tune.py searches these against the reference beats/sections of your songs
# and writes tuned.json next to this file, which is loaded at start
DEFAULTS = dict(kick_floor=6.0, snare_floor=3.0, pll_gain_free=0.5, pll_gain_locked=0.15, kick_pull=0.25,
                kick_window=0.12, tempo_center=115.0, tempo_width=0.75, genre_pull=0.5, nov_thr=1.6,
                lock_conf=0.35, sharp_thr=1.5)
PARAMS = dict(DEFAULTS)
try:
    import json as _json, os as _os
    _p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "tuned.json")
    if _os.path.exists(_p):
        PARAMS.update({k: v for k, v in _json.load(open(_p)).items() if k in DEFAULTS})
except Exception:
    pass


class Analyzer:
    REF0 = -21.0      # typical loud-section level (dB) of loudness-normalised streaming audio

    def __init__(self, sr, hop=512, nfft=2048, params=None):
        self.P = dict(PARAMS)
        if params:
            self.P.update(params)
        P = self.P
        self.sr, self.hop, self.nfft = sr, hop, nfft
        self.fps = sr / hop
        self.dt = hop / sr
        self.buf = np.zeros(nfft, np.float32)
        self.bufL = np.zeros(nfft, np.float32)
        self.bufR = np.zeros(nfft, np.float32)
        fr = np.fft.rfftfreq(nfft, 1.0 / sr)
        self.vsel = (fr >= 300) & (fr <= 3400)       # where voices live
        self.vocal = 0.0                             # vocal-band level 0..1 (fast attack, smooth release)
        self.vocal_pan = 0.5                         # 0 = left, 1 = right
        self.v_floor, self.v_peak = -60.0, -20.0
        self.v_prev = -90.0
        self.vocal_hit = False                       # a new syllable / ad-lib ("pa pa")
        self.singing = 0.0                           # from the genre net (0..1), set by the engine
        self.singing_known = False
        self.tempo_prior = None                      # from the genre net
        self.win = np.hanning(nfft).astype(np.float32)
        self.freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
        self.mel, self.mel_hz = melbank(sr, nfft)
        nb = self.mel.shape[0]
        self.nb = nb
        fps = self.fps
        self.t = 0.0
        self.n = 0

        # band groups (indices into the mel bands)
        def bands(lo, hi):
            return np.where((self.mel_hz >= lo) & (self.mel_hz < hi))[0]
        self.g_kick = bands(35, 140)
        self.g_bass = bands(35, 250)
        self.g_sub = bands(30, 150)
        self.g_lowmid = bands(250, 1000)
        self.g_snare = bands(1000, 6000)
        self.g_snbody = bands(150, 400)
        self.g_hat = bands(8000, 16500)

        # per-band normalisation: floor rises slowly, peak decays slowly
        self.lm_prev = np.zeros(nb)
        self.lm_prev2 = np.zeros(nb)
        self.floor = np.full(nb, -60.0)
        self.peakb = np.full(nb, -20.0)
        self.norm = np.zeros(nb)          # 0..1 per band
        self.a_floor_up = alpha(8.0, fps)
        self.a_floor_dn = alpha(0.5, fps)
        self.a_peak_up = alpha(0.05, fps)
        self.a_peak_dn = alpha(12.0, fps)

        # loudness
        self.db = -90.0
        self.lv_fast, self.lv_mid, self.lv_long = -60.0, -60.0, -60.0
        self.lv_ref = self.REF0          # "loud part of this song" reference
        self.a_fast, self.a_mid, self.a_long = alpha(0.06, fps), alpha(1.2, fps), alpha(25.0, fps)
        self.energy = 0.0                # 0..1, relative to this song
        self.energy_fast = 0.0
        self.silence = True
        self.silent_for = 10.0

        # band levels 0..1 (smoothed)
        self.bass = 0.0
        self.bass_slow = 0.0
        self.mid = 0.0
        self.high = 0.0
        self.centroid = 0.0              # 0..1 brightness of the sound
        self.bdb_mid, self.bdb_ref = -80.0, -40.0
        self.bass_rel = 0.0              # bass now vs the song's full bass (0..1)

        # drums
        self.kick = Onset(fps, k=1.4, floor=P['kick_floor'], gap=0.14)
        self.snare = Onset(fps, k=1.8, floor=P['snare_floor'], gap=0.12)
        self.hat = Onset(fps, k=1.4, floor=1.2, gap=0.07)
        self.hit_kick = self.hit_snare = self.hit_hat = False
        self.kick_env = self.snare_env = self.hat_env = 0.0
        self.kick_rate = 0.0             # kicks per beat (smoothed)
        self.hat_rate = 0.0
        self.snare_rate = 0.0
        self.onset_rate = 0.0            # all onsets per second (smoothed)

        # tempo / beat
        self.HN = int(8.0 * fps)          # 8 s onset history
        self.ohist = np.zeros(self.HN, np.float32)
        self.oi = 0
        self.bpm = 120.0
        self.period = 60.0 / 120.0        # s per beat
        self.tconf = 0.0                  # tempo confidence 0..1
        self.phase = 0.0                  # 0..1 within the beat
        self.bp = 0.0                     # continuous beat position (beats since start)
        self.big_err = 0
        self.beat = 0                     # beat counter
        self.beat_tick = False            # True on the hop where a beat starts
        self.locked = False
        self.last_tempo = 0.0
        self.pending_bpm = None
        self.bar_acc = np.zeros(4)        # downbeat evidence per beat position
        self.bar_off = 0
        self.bar_beat = 0                 # 0..3
        self.bar = 0
        self.downbeat = False
        self.beat_accent = 0.0
        self.beat_feat = []               # per-beat descriptors for sections
        self.cur_beat_sum = np.zeros(nb + 12 + 1)
        self.cur_beat_n = 0

        # pitch
        self.pc_of_bin = np.full(len(self.freqs), -1)
        sel = (self.freqs >= 60) & (self.freqs <= 5000)
        midi = 69 + 12 * np.log2(np.maximum(self.freqs, 1) / 440.0)
        self.pc_of_bin[sel] = np.round(midi[sel]).astype(int) % 12
        self.chroma = np.zeros(12)
        self.chroma_key = np.ones(12) / 12
        self.chroma_beat_prev = np.zeros(12)
        self.chord_change = 0.0
        self.key = 0
        self.minor = False
        self.key_conf = 0.0
        self.a_chroma = alpha(0.25, fps)
        self.a_key = alpha(20.0, fps)
        self.lead_pc = 0
        self.lead_pos = 0.5
        self.lead_conf = 0.0
        self.bass_pc = 0

        # sections
        self.intensity = 0.0
        self.novelty = 0.0
        self.section_change = False
        self.section_id = 0
        self.last_section_beat = -999
        self.build = 0.0                  # 0..1 build-up progress
        self.building = False
        self.drop = False                 # True on the hop a drop lands
        self.last_drop_t = -1e9
        self.bass_absent_beats = 0
        self.beat_t_prev = 0.0
        self.kick_hist = []
        self.snare_hist = []
        self.beat_bass_pc = np.zeros(12)
        self.beat_onsets = 0
        self.beat_brel_sum = self.beat_hdb_sum = 0.0
        self.beat_brel_n = 0
        self.bass_fast_rel = 0.0
        self.bdb_fast = -80.0
        self.build_dbg = None

        # ---- deeper breakdown ----
        self.lm_ring = np.zeros((9, nb))          # last 9 frames of log-mel, for the harmonic/percussive split
        self.perc = 0.0                           # 0..1 how much of the sound right now is drums / transients
        self.tonal = 0.0                          # 0..1 how steady (sustained notes, pads, voice) it is
        self.lr = 0.0                             # stereo balance -1 (left) .. +1 (right), exaggerated
        self.lvl_l = self.lvl_r = 0.0             # per-side level 0..1
        self.flat = 0.0                           # spectral flatness of the top end (noise sweeps are flat)
        self.cen_fast = 0.0
        self.cen_ring = np.zeros(48)              # centroid every 4 hops (~2 s)
        self.flat_ring = np.zeros(48)
        self.hi_ring = np.zeros(48)
        self.riser = 0.0                          # 0..1 a rising sweep is happening
        self.impact = False                       # True on the hop a big broadband hit lands
        self.impact_strength = 0.0
        self.impact_env = 0.0
        self.boom = Onset(fps, k=2.6, floor=2.2, gap=0.35, tau=1.5)

    # ------------------------------------------------------------------
    def process(self, x):
        """x: float32 block of `hop` samples, mono (hop,) or stereo (hop, 2)"""
        hop = self.hop
        if x.ndim == 2:
            L, R = x[:, 0], x[:, 1]
        else:
            L = R = x
        self.bufL[:-hop] = self.bufL[hop:]
        self.bufL[-hop:] = L
        self.bufR[:-hop] = self.bufR[hop:]
        self.bufR[-hop:] = R
        self.buf = 0.5 * (self.bufL + self.bufR)
        self.t += self.dt
        self.n += 1
        sL = np.fft.rfft(self.bufL * self.win)
        sR = np.fft.rfft(self.bufR * self.win)
        spec = 0.5 * (sL + sR)
        p = (spec.real ** 2 + spec.imag ** 2) / (self.nfft * 0.25)
        self._vocal(sL, sR)
        mag = np.sqrt(p)
        em = self.mel @ p
        lm = 10.0 * np.log10(em + 1e-12)
        self.lm = lm                                  # raw log-mel (the known-song matcher uses it)

        # ---- loudness ----
        tot = float(np.mean(self.buf[-hop * 2:] ** 2))
        self.db = 10 * math.log10(tot + 1e-12)
        self.silence = self.db < -65
        if self.silence:
            self.silent_for += self.dt
        else:
            self.silent_for = 0.0
        if not self.silence:
            self.lv_fast = ema(self.lv_fast, self.db, self.a_fast)
            self.lv_mid = ema(self.lv_mid, self.db, self.a_mid)
            self.lv_long = ema(self.lv_long, self.db, self.a_long)
            # reference = loud parts of the song: rises fast to louder mid level, falls slowly
            if self.lv_mid > self.lv_ref:
                self.lv_ref = ema(self.lv_ref, self.lv_mid, alpha(1.0, self.fps))
            else:
                self.lv_ref = ema(self.lv_ref, self.lv_mid, alpha(40.0, self.fps))
        if self.silent_for > 3.0:            # new song after a gap: back to a typical loud level
            self.lv_ref = self.REF0
        self.energy = float(np.clip(1.0 + (self.lv_mid - self.lv_ref) / 10.0, 0, 1))
        self.energy_fast = float(np.clip(1.0 + (self.lv_fast - self.lv_ref) / 10.0, 0, 1))

        # ---- per-band normalisation ----
        if not self.silence:
            up = lm > self.floor
            self.floor = np.where(up, self.floor + (lm - self.floor) * self.a_floor_up,
                                  self.floor + (lm - self.floor) * self.a_floor_dn)
            pk_up = lm > self.peakb
            self.peakb = np.where(pk_up, self.peakb + (lm - self.peakb) * self.a_peak_up,
                                  self.peakb + (lm - self.peakb) * self.a_peak_dn)
            self.peakb = np.maximum(self.peakb, self.floor + 12)
        self.norm = np.clip((lm - self.floor) / (self.peakb - self.floor), 0, 1)
        if self.silence:
            self.norm *= 0

        def lev(g):
            return float(np.mean(self.norm[g])) if len(g) else 0.0
        self.bass = ema(self.bass, lev(self.g_bass), 0.5)
        self.bass_slow = ema(self.bass_slow, self.bass, alpha(2.0, self.fps))
        bdb = 10 * math.log10(float(em[self.g_sub].sum()) + 1e-12)
        if not self.silence:
            self.bdb_mid = ema(self.bdb_mid, bdb, alpha(0.4, self.fps))
            a_up, a_dn = alpha(1.5, self.fps), alpha(60.0, self.fps)
            self.bdb_ref = ema(self.bdb_ref, self.bdb_mid, a_up if self.bdb_mid > self.bdb_ref else a_dn)
        if self.silent_for > 3.0:
            self.bdb_ref = -40.0
        self.bass_rel = float(np.clip(1.0 + (self.bdb_mid - self.bdb_ref) / 12.0, 0, 1))
        self.bdb_fast = ema(self.bdb_fast, bdb, alpha(0.06, self.fps))
        self.bass_fast_rel = float(np.clip(1.0 + (self.bdb_fast - self.bdb_ref) / 12.0, 0, 1))
        self.mid = ema(self.mid, lev(self.g_lowmid), 0.4)
        self.high = ema(self.high, lev(self.g_hat), 0.4)
        w = em / (em.sum() + 1e-12)
        cen = float(np.sum(w * np.arange(self.nb)) / self.nb)
        self.centroid = ema(self.centroid, cen, alpha(0.5, self.fps))
        self._breakdown(lm, em, p, sL, sR, cen)

        # ---- drum onsets: log-flux (dB rise) per group, lag 2 hops ----
        d = np.maximum(0.0, lm - self.lm_prev2)
        d = np.minimum(d, 20.0)
        if self.silence:
            d *= 0
        fhi = float(np.mean(d[self.g_snare]))
        fk = float(np.mean(d[self.g_kick])) if len(self.g_kick) else 0.0
        fk = max(0.0, fk - 0.7 * fhi)            # a kick rises in the lows much more than in the highs
        fs = fhi + 0.4 * float(np.mean(d[self.g_snbody]))
        fh = float(np.mean(d[self.g_hat])) if len(self.g_hat) else 0.0
        fall = float(np.mean(d))
        self.lm_prev2 = self.lm_prev
        self.lm_prev = lm
        t = self.t
        self.hit_kick = self.kick.push(fk, t)
        if self.hit_kick and float(np.mean(self.norm[self.g_kick])) < 0.45:
            self.hit_kick = False          # a tiny rise in a quiet low end is not a kick
        self.hit_snare = self.snare.push(fs, t)
        self.hit_hat = self.hat.push(fh, t)
        if self.hit_snare:
            body = float(np.mean(d[self.g_snbody]))
            if body < 1.5 and fhi < 1.3 * fh:
                self.hit_snare = False   # all top end, no body: that's a hat/cymbal, not a snare
                self.hit_hat = True
                self.hat.strength = max(self.hat.strength, 0.5)
            else:
                self.hit_hat = False     # a snare is also bright: don't double count
        dec = math.exp(-self.dt / 0.12)
        self.kick_env = max(self.kick_env * dec, self.kick.strength if self.hit_kick else 0)
        self.snare_env = max(self.snare_env * dec, self.snare.strength if self.hit_snare else 0)
        self.hat_env = max(self.hat_env * math.exp(-self.dt / 0.06), self.hat.strength if self.hit_hat else 0)
        self.impact = self.boom.push(fall, t) and self.energy_fast > 0.45 and (self.bass_fast_rel > 0.55 or fall > 6.0)
        if self.impact:
            self.impact_strength = self.boom.strength
        self.impact_env = max(self.impact_env * math.exp(-self.dt / 0.25), self.impact_strength if self.impact else 0.0)
        a_r = alpha(3.0, self.fps)
        self.onset_rate = ema(self.onset_rate, (self.hit_kick + self.hit_snare + self.hit_hat) * self.fps, a_r)

        # ---- tempo ----
        o = fall + 1.5 * fk
        self.ohist[self.oi] = o
        self.oi = (self.oi + 1) % self.HN
        if self.t - self.last_tempo > 0.5 and self.n > self.fps * 3:
            self.last_tempo = self.t
            self._tempo()
        self._advance_beat()

        # ---- pitch / chroma ----
        pcw = np.bincount(self.pc_of_bin[self.pc_of_bin >= 0], weights=mag[self.pc_of_bin >= 0], minlength=12)
        if pcw.sum() > 0 and not self.silence:
            c = pcw / (pcw.max() + 1e-12)
            c = c ** 2
            self.chroma = ema(self.chroma, c, self.a_chroma)
            self.chroma_key = ema(self.chroma_key, c, self.a_key)
        self._key()
        self._lead(mag)

        # per-beat accumulation for section analysis
        fv = np.concatenate([self.norm, self.chroma * 0.6, [self.energy]])
        self.cur_beat_sum += fv
        self.cur_beat_n += 1
        self.beat_onsets += int(self.hit_kick) + int(self.hit_snare) + int(self.hit_hat)
        self.beat_brel_sum += self.bass_rel
        self.beat_hdb_sum += float(em[self.g_hat].sum()) if len(self.g_hat) else 0.0
        self.beat_brel_n += 1

        if self.beat_tick:
            self._on_beat()
        else:
            self.downbeat = False
            self.section_change = False
        self.drop = False
        self._check_drop()

    # ------------------------------------------------------------------
    def _tempo(self):
        h = np.roll(self.ohist, -self.oi).astype(np.float64)
        h = h - h.mean()
        e = float(np.dot(h, h))
        if e < 1e-9:
            self.tconf *= 0.8
            return
        n = len(h)
        F = np.fft.rfft(h, 2 * n)
        r = np.fft.irfft(F * np.conj(F))[:n] / e
        fps = self.fps
        lags = np.arange(int(fps * 60 / 190), int(fps * 60 / 58) + 1)
        bpms = 60.0 * fps / lags
        # harmonic-enhanced score: a true beat period also shows at 2x and the half-beat
        def R(L):
            L = np.asarray(L)
            i = np.floor(L).astype(int)
            f = L - i
            i = np.clip(i, 0, n - 2)
            return r[i] * (1 - f) + r[i + 1] * f
        sc = R(lags) + 0.5 * R(lags * 2) + 0.25 * R(lags / 2)
        # prior: favour 90-140 bpm, and stay near the current tempo
        c0 = self.P['tempo_center']
        center = c0 * ((self.tempo_prior or c0) / c0) ** self.P['genre_pull']   # genre nudges it: R&B lower, house higher
        lp = np.log2(bpms / center)
        prior = np.exp(-0.5 * (lp / self.P['tempo_width']) ** 2)
        if self.tconf > 0.3:
            ls = np.log2(bpms / self.bpm)
            prior *= 0.6 + 0.8 * np.exp(-0.5 * (ls / 0.04) ** 2)
        s = sc * prior
        k = int(np.argmax(s))
        L = float(lags[k])
        if 0 < k < len(s) - 1:
            a, b, c = s[k - 1], s[k], s[k + 1]
            den = a - 2 * b + c
            if den != 0:
                L += 0.5 * (a - c) / den
        raw = sc[np.isfinite(sc)]
        z = (sc[k] - raw.mean()) / (raw.std() + 1e-9)
        conf = float(np.clip((z - 1.5) / 2.5, 0, 1))
        bpm = 60.0 * fps / L
        # tempo changes need two agreeing estimates (avoid jumping on a fill)
        if abs(bpm - self.bpm) / self.bpm < 0.04:
            self.bpm = 0.7 * self.bpm + 0.3 * bpm
            self.pending_bpm = None
        elif self.pending_bpm and abs(bpm - self.pending_bpm) / bpm < 0.04 or self.tconf < 0.2:
            self.bpm = bpm
            self.pending_bpm = None
        else:
            self.pending_bpm = bpm
        self.tconf = ema(self.tconf, conf, 0.4)
        self.period = 60.0 / self.bpm
        self._phase_correct()

    def _phase_correct(self):
        """comb filter over the last 6 s to find where the beats fall; nudge the PLL"""
        h = np.roll(self.ohist, -self.oi)
        fps = self.fps
        Lf = self.period * fps
        nper = int(min(6.0 / self.period, (len(h) - 3) / Lf))
        if nper < 2:
            return
        steps = max(8, int(Lf))
        hs = h.astype(np.float64)
        hs = hs + 0.5 * (np.roll(hs, 1) + np.roll(hs, -1))
        ph = np.arange(steps) / steps            # candidate: the last beat was ph*period ago
        q = np.arange(nper)
        j = len(h) - 2 - np.round((ph[:, None] + q[None, :]) * Lf).astype(int)
        w = 0.85 ** q                            # recent beats count more
        vals = (hs[np.clip(j, 1, len(h) - 2)] * (j >= 1) * w[None, :]).sum(axis=1)
        k = int(np.argmax(vals))
        best, bestv = float(ph[k]) + 1.0 / self.fps / self.period, float(vals[k])
        sharp = (bestv - vals.mean()) / (vals.std() + 1e-9)
        # measured phase now = best (fraction of a beat since the last beat)
        err = (best - self.phase + 0.5) % 1.0 - 0.5
        if sharp > self.P['sharp_thr']:
            if abs(err) > 0.2:
                self.big_err += 1               # consistently far off (e.g. locked to the off-beat)
            else:
                self.big_err = 0
            gain = self.P['pll_gain_free'] if not self.locked else self.P['pll_gain_locked']
            if self.big_err >= 3:
                gain, self.big_err = 1.0, 0
            self.bp += gain * err                # shift the continuous beat position (no lost/extra ticks)
        self.locked = self.tconf > self.P['lock_conf'] and sharp > self.P['sharp_thr']

    def _advance_beat(self):
        self.beat_tick = False
        if self.silent_for > 1.0:
            self.locked = False
        self.bp += self.dt / self.period
        # fast correction: a kick close to the beat pulls the phase a little
        if self.hit_kick:
            e = (self.bp + 0.5) % 1.0 - 0.5
            if abs(e) < self.P['kick_window']:
                self.bp -= self.P['kick_pull'] * e
        fb = math.floor(self.bp)
        if fb > self.beat:
            self.beat = fb
            self.beat_tick = True
        self.phase = self.bp - math.floor(self.bp)
        if self.bp < self.beat:                  # pulled back across a beat we already ticked
            self.phase = 0.0

    def _key(self):
        if self.n % 25:
            return
        c = self.chroma_key
        if c.sum() <= 0:
            return
        best, bv, scores = 0, -9, []
        for k in range(12):
            for mi, prof in ((0, KK_MAJ), (1, KK_MIN)):
                v = np.corrcoef(c, np.roll(prof, k))[0, 1]
                scores.append(v)
                if v > bv:
                    bv, best = v, k * 2 + mi
        self.key = best // 2
        self.minor = bool(best % 2)
        self.key_conf = float(bv)

    def _breakdown(self, lm, em, p, sL, sR, cen):
        fps = self.fps
        # harmonic / percussive: a sustained sound looks like the median of the last frames; drums stick out of it
        self.lm_ring = np.roll(self.lm_ring, -1, axis=0)
        self.lm_ring[-1] = lm
        med = np.median(self.lm_ring, axis=0)
        rng = np.maximum(self.peakb - self.floor, 6.0)
        pos = np.maximum(0.0, lm - med) / rng
        dev = np.abs(lm - med) / rng
        loud = self.norm > 0.15
        if self.silence or not loud.any():
            pr, tn = 0.0, 0.0
        else:
            pr = float(np.clip(np.mean(pos[loud]) * 4.0, 0, 1))
            tn = float(np.clip(1.0 - np.mean(dev[loud]) * 4.0, 0, 1))
        self.perc = ema(self.perc, pr, alpha(0.4, fps))
        self.tonal = ema(self.tonal, tn, alpha(0.8, fps))
        # stereo balance (whole band, 60 Hz - 8 kHz)
        sel = (self.freqs > 60) & (self.freqs < 8000)
        eL = float(np.sum(np.abs(sL[sel]) ** 2))
        eR = float(np.sum(np.abs(sR[sel]) ** 2))
        if eL + eR > 1e-9 and not self.silence:
            b = (eR - eL) / (eR + eL)
            self.lr = ema(self.lr, float(np.clip(b * 3.0, -1, 1)), alpha(0.15, fps))
            dl = 10 * math.log10(eL / (self.nfft * 0.25) + 1e-12)
            dr = 10 * math.log10(eR / (self.nfft * 0.25) + 1e-12)
            self.lvl_l = ema(self.lvl_l, float(np.clip(1 + (dl - self.lv_ref - 3) / 12, 0, 1)), alpha(0.08, fps))
            self.lvl_r = ema(self.lvl_r, float(np.clip(1 + (dr - self.lv_ref - 3) / 12, 0, 1)), alpha(0.08, fps))
        else:
            self.lvl_l *= 0.9
            self.lvl_r *= 0.9
        # flatness of 2-12 kHz (1 = noise, 0 = tones)
        hs = (self.freqs > 2000) & (self.freqs < 12000)
        ph = p[hs] + 1e-12
        fl = float(np.exp(np.mean(np.log(ph))) / np.mean(ph))
        self.flat = ema(self.flat, fl, alpha(0.3, fps))
        self.cen_fast = ema(self.cen_fast, cen, alpha(0.1, fps))
        # riser: over the last ~2 s the sound gets steadily brighter / noisier / louder up top
        if self.n % 4 == 0:
            hi = float(np.mean(self.norm[self.g_hat])) if len(self.g_hat) else 0.0
            for name, v in (("cen_ring", self.cen_fast), ("flat_ring", self.flat), ("hi_ring", hi)):
                r = getattr(self, name)
                r[:-1] = r[1:]
                r[-1] = v
            if self.n > 48 * 4 and not self.silence:
                x = np.arange(48, dtype=float)
                def slope(v):
                    k, c = np.polyfit(x, v, 1)
                    res = v - (k * x + c)
                    return k * 48, 1 - res.var() / (v.var() + 1e-12)
                kc, rc = slope(self.cen_ring)
                kf, rf = slope(self.flat_ring)
                kh, rh = slope(self.hi_ring)
                score = (kc > 0.06 and rc > 0.6) + (kf > 0.05 and rf > 0.6) + (kh > 0.2 and rh > 0.6)
                target = min(1.0, score / 2.0)
                self.riser = ema(self.riser, target, 0.3 if target > self.riser else 0.05)
            elif self.silence:
                self.riser *= 0.9

    def _vocal(self, sL, sR):
        """level and stereo position of the voice band; syllable onsets"""
        eL = float(np.sum(np.abs(sL[self.vsel]) ** 2))
        eR = float(np.sum(np.abs(sR[self.vsel]) ** 2))
        e = 0.5 * (eL + eR) / (self.nfft * 0.25)
        db = 10 * math.log10(e + 1e-12)
        fps = self.fps
        if db > -70:
            self.v_floor = ema(self.v_floor, db, alpha(6.0, fps) if db > self.v_floor else alpha(0.4, fps))
            self.v_peak = ema(self.v_peak, db, alpha(0.1, fps) if db > self.v_peak else alpha(10.0, fps))
            self.v_peak = max(self.v_peak, self.v_floor + 10)
        lvl = float(np.clip((db - self.v_floor) / (self.v_peak - self.v_floor), 0, 1))
        self.vocal = ema(self.vocal, lvl, 0.35 if lvl > self.vocal else 0.04)
        self.vocal_hit = (db - self.v_prev > 6.0) and lvl > 0.5
        self.v_prev = ema(self.v_prev, db, 0.5)
        if eL + eR > 1e-9:
            pan = 0.5 + 0.5 * (eR - eL) / (eR + eL)
            pan = 0.5 + (pan - 0.5) * 1.8              # exaggerate a little: mixes rarely pan hard
            self.vocal_pan = ema(self.vocal_pan, float(np.clip(pan, 0.03, 0.97)), alpha(0.25, fps))

    def _lead(self, mag):
        sel = (self.freqs > 180) & (self.freqs < 2200)
        m = mag[sel]
        if m.size == 0 or self.silence:
            self.lead_conf *= 0.95
            return
        i = int(np.argmax(m))
        prom = m[i] / (np.mean(m) + 1e-12)
        f = self.freqs[sel][i]
        midi = 69 + 12 * math.log2(f / 440.0)
        self.lead_conf = ema(self.lead_conf, float(np.clip((prom - 4) / 10, 0, 1)), 0.2)
        if prom > 5:
            self.lead_pc = int(round(midi)) % 12
            pos = float(np.clip((midi - 52) / 36.0, 0, 1))
            self.lead_pos = ema(self.lead_pos, pos, 0.25)
        sb = (self.freqs > 40) & (self.freqs < 250)
        mb = mag[sb]
        if mb.size:
            fb = self.freqs[sb][int(np.argmax(mb))]
            self.bass_pc = int(round(69 + 12 * math.log2(fb / 440.0))) % 12
            if self.bass_rel > 0.3:
                self.beat_bass_pc[self.bass_pc] += 1

    def _on_beat(self):
        """runs once per beat (when beat k starts): looks back at beat k-1 to judge accents,
        then updates bars, sections, build-ups"""
        P = self.period
        now = self.t
        t_prev = self.beat_t_prev                 # when beat k-1 started
        self.beat_t_prev = now
        # --- what happened on the previous beat
        kicks = [s for (tt, s) in self.kick_hist if t_prev - 0.15 * P <= tt <= t_prev + 0.3 * P]
        kev = max(kicks) if kicks else 0.0
        snares = [s for (tt, s) in self.snare_hist if t_prev - 0.15 * P <= tt <= t_prev + 0.3 * P]
        sev = max(snares) if snares else 0.0
        bpc = int(np.argmax(self.beat_bass_pc)) if self.beat_bass_pc.sum() else -1
        self.beat_bass_pc[:] = 0
        self.kick_rate = ema(self.kick_rate, 1.0 if kev > 0 else 0.0, 0.2)
        fv = self.cur_beat_sum / max(self.cur_beat_n, 1)
        self.cur_beat_sum[:] = 0
        self.cur_beat_n = 0
        ons = self.beat_onsets
        self.beat_onsets = 0
        bf = {"bpc": bpc, "fv": fv, "chroma": fv[self.nb:self.nb + 12] / 0.6, "e": fv[-1], "kick": kev,
              "brel": self.beat_brel_sum / max(self.beat_brel_n, 1),
              "hdb": 10 * math.log10(self.beat_hdb_sum / max(self.beat_brel_n, 1) + 1e-12), "ons": ons}
        self.beat_brel_sum = self.beat_hdb_sum = 0.0
        self.beat_brel_n = 0
        self.beat_feat.append(bf)
        if len(self.beat_feat) > 64:
            self.beat_feat.pop(0)
        F = self.beat_feat
        # chord change into beat k-1
        if len(F) >= 2:
            a, b = F[-1]["chroma"], F[-2]["chroma"]
            ch = 1.0 - float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        else:
            ch = 0.0
        self.chord_change = ch
        # downbeat evidence for the position of beat k-1
        bass_jump = max(0.0, F[-1]["brel"] - (F[-2]["brel"] if len(F) >= 2 else 0))
        bass_move = 1.0 if (len(F) >= 2 and bpc >= 0 and F[-2]["bpc"] >= 0 and bpc != F[-2]["bpc"]) else 0.0
        acc = 0.3 * kev + 4.0 * ch + 1.0 * bass_jump + 0.6 * bass_move - 0.6 * sev
        pos_prev = (self.beat - 1) % 4
        self.bar_acc *= 0.96
        self.bar_acc[pos_prev] += acc
        if self.beat % 4 == 0 and np.ptp(self.bar_acc) > 0.3:
            self.bar_off = int(np.argmax(self.bar_acc))
        self.bar_beat = (self.beat - self.bar_off) % 4
        self.downbeat = self.bar_beat == 0
        if self.downbeat:
            self.bar += 1

        # intensity: what a lighting operator calls "how big is this part"
        dens = float(np.clip(self.onset_rate / 8.0, 0, 1))
        it = 0.45 * self.energy + 0.25 * self.bass_rel + 0.15 * self.kick_rate + 0.15 * dens
        self.intensity = ema(self.intensity, it, 0.35)

        # bass presence (for drops)
        if F[-1]["brel"] < 0.45:
            self.bass_absent_beats += 1
        else:
            self.bass_absent_beats = 0

        # novelty: last 2 beats vs the 8 before
        self.section_change = False
        if len(F) >= 10:
            A_ = np.array([f["fv"] for f in F[-10:]])
            a = A_[-2:].mean(axis=0)
            b = A_[:-2].mean(axis=0)
            sd = A_[:-2].std(axis=0) + 0.05
            nov = float(np.mean(np.abs(a - b) / sd))
            self.novelty = ema(self.novelty, nov, 0.6)
            if self.downbeat and nov > self.P['nov_thr'] and self.beat - self.last_section_beat >= 8:
                self.section_change = True
                self.section_id += 1
                self.last_section_beat = self.beat

        # build-up: things rise steadily (highs, level, hit density) while the bass is held back
        W = 12
        if len(F) >= W:
            x = np.arange(W)

            def trend(v):
                v = np.asarray(v, float)
                k, c = np.polyfit(x, v, 1)
                res = v - (k * x + c)
                r2 = 1 - res.var() / (v.var() + 1e-9)
                return k, r2
            sh, rh = trend([f["hdb"] for f in F[-W:]])
            se, re = trend([f["e"] for f in F[-W:]])
            so, ro = trend([f["ons"] for f in F[-W:]])
            rising = (sh > 0.25 and rh > 0.6) + (se > 0.015 and re > 0.6) + (so > 0.15 and ro > 0.5)
            lowbass = np.mean([f["brel"] for f in F[-4:]]) < 0.35
            self.build_dbg = (round(float(sh), 2), round(float(rh), 2), round(float(se), 3), round(float(re), 2),
                              round(float(so), 2), round(float(ro), 2), bool(lowbass))
            if self.riser > 0.5:
                rising += 1                       # a noise sweep / rising synth is the classic build signal
            if rising >= 2 or (rising >= 1 and lowbass):
                self.build = min(1.0, self.build + 1.0 / 20.0)
            elif lowbass and self.build > 0.3:
                self.build = max(0.0, self.build - 1.0 / 32.0)   # plateau before the drop: hold
            else:
                self.build = max(0.0, self.build - 1.0 / 6.0)
            self.building = self.build > 0.2

    def _check_drop(self):
        """runs every hop: a drop is a hard kick with the full bass back, after a build or a bass-less stretch"""
        if self.hit_snare:
            self.snare_hist.append((self.t, self.snare.strength))
            if len(self.snare_hist) > 16:
                self.snare_hist.pop(0)
        if self.hit_kick:
            self.kick_hist.append((self.t, self.kick.strength))
            if len(self.kick_hist) > 16:
                self.kick_hist.pop(0)
        recent_kick = self.kick_hist and self.t - self.kick_hist[-1][0] < 0.12
        if (recent_kick and self.kick_hist[-1][1] > 0.5 and self.bass_fast_rel > 0.7 and
                (self.build > 0.45 or self.bass_absent_beats >= 8) and self.t - self.last_drop_t > 8):
            self.drop = True
            self.last_drop_t = self.t
            self.build = 0.0
            self.building = False
            self.bass_absent_beats = 0
            self.section_change = True
            self.section_id += 1
            self.last_section_beat = self.beat

    bass_absent_beats_prev = 0

    def key_name(self):
        return NOTE[self.key] + ("m" if self.minor else "")

    def state(self):
        return {
            "t": round(self.t, 2), "bpm": round(self.bpm, 1), "conf": round(self.tconf, 2),
            "locked": self.locked, "phase": round(self.phase, 3), "beat": self.beat,
            "bb": self.bar_beat, "bar": self.bar, "energy": round(self.energy, 2),
            "int": round(self.intensity, 2), "bass": round(self.bass, 2), "mid": round(self.mid, 2),
            "high": round(self.high, 2), "kick": round(self.kick_env, 2), "snare": round(self.snare_env, 2),
            "hat": round(self.hat_env, 2), "key": self.key_name(), "nov": round(self.novelty, 2),
            "build": round(self.build, 2), "sec": self.section_id, "lead": round(self.lead_pos, 2),
            "silent": self.silent_for > 1.0, "db": round(self.db, 1),
            "vox": round(self.vocal, 2), "pan": round(self.vocal_pan, 2),
            "perc": round(self.perc, 2), "tonal": round(self.tonal, 2), "lr": round(self.lr, 2),
            "riser": round(self.riser, 2), "impact": round(self.impact_env, 2),
        }
