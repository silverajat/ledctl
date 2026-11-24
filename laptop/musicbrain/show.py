"""The light show: a director that picks looks like a club lighting operator,
and an effect engine that draws them on an addressable strip.

Director rules (what real LJs do):
  - know the genre first: it sets the colour family and how punchy the show may be
    (R&B/soul = deep blues and purples, smooth, no strobes; EDM = full punch, coloured strobes)
  - change looks on the downbeat, never mid-bar (except a drop: instantly)
  - the size of the look follows the size of the music (intensity tier)
  - drums and voice get separate jobs: kick = pulse, snare = burst, hats = sparkle,
    the voice = a soft glowing ribbon that sits where the singer is in the stereo mix
  - colours stay in one family per song; a new section moves to a neighbouring palette
  - movement is locked to the beat clock, so it moves "in time" even between hits
This file is also the reference model for the FPGA effect engine later.
"""
import math
import random
import numpy as np
import colorsys


def hsv(h, s=1.0, v=1.0):
    return np.array(colorsys.hsv_to_rgb(h % 1.0, s, v))


# colours as (hue, saturation)
PALETTES = {
    # R&B / soul / slow jams
    "velvet":   [(0.70, 1.0), (0.78, 1.0), (0.88, 0.9), (0.64, 1.0)],     # indigo, purple, magenta, deep blue
    "midnight": [(0.64, 1.0), (0.72, 1.0), (0.58, 0.85), (0.80, 0.8)],    # deep blue, violet, ocean, lilac
    "amethyst": [(0.76, 0.9), (0.66, 1.0), (0.85, 0.8), (0.92, 0.7)],
    "rose":     [(0.80, 0.9), (0.93, 0.75), (0.66, 1.0), (0.87, 0.85)],   # purple, rose, blue, pink
    # hip-hop
    "deep":     [(0.66, 1.0), (0.98, 1.0), (0.74, 1.0), (0.60, 0.8)],     # blue, red, violet
    "noir":     [(0.11, 0.9), (0.76, 1.0), (0.98, 1.0), (0.66, 1.0)],     # gold, purple, red, blue
    # EDM / club
    "club":     [(0.62, 1.0), (0.85, 1.0), (0.50, 1.0), (0.75, 1.0)],
    "acid":     [(0.30, 1.0), (0.80, 1.0), (0.40, 1.0), (0.88, 0.9)],
    "laser":    [(0.50, 1.0), (0.62, 1.0), (0.83, 1.0), (0.55, 0.5)],
    # pop / latin / funk
    "candy":    [(0.92, 0.8), (0.48, 0.9), (0.14, 0.85), (0.80, 0.7)],
    "sunset":   [(0.06, 1.0), (0.95, 0.85), (0.80, 0.9), (0.10, 0.9)],
    "tropical": [(0.47, 1.0), (0.14, 1.0), (0.02, 0.85), (0.90, 0.8)],
    # rock / warm / calm
    "fire":     [(0.00, 1.0), (0.06, 1.0), (0.11, 1.0), (0.95, 0.9)],
    "candle":   [(0.08, 0.95), (0.05, 1.0), (0.11, 0.7), (0.02, 0.9)],
    "forest":   [(0.38, 0.9), (0.47, 0.9), (0.30, 0.7), (0.13, 0.7)],
}

# how each kind of music is lit. palettes go from darker/moodier to brighter/happier.
FEEL = {
    "rnb":    dict(strobe=0.0, white=0.08, vocal=1.0, pulse=0.55, peak="swell", palettes=["midnight", "velvet", "amethyst", "rose"]),
    "hiphop": dict(strobe=0.0, white=0.08, vocal=0.7, pulse=0.85, peak="punch", palettes=["deep", "velvet", "noir", "club"]),
    "edm":    dict(strobe=0.3, white=0.08, vocal=0.35, pulse=1.0, peak="peak", palettes=["club", "laser", "acid", "fire"]),
    "pop":    dict(strobe=0.3, white=0.08, vocal=0.8, pulse=0.9, peak="peak", palettes=["rose", "sunset", "candy", "tropical"]),
    "rock":   dict(strobe=0.3, white=0.08, vocal=0.6, pulse=1.0, peak="peak", palettes=["deep", "fire", "candle", "sunset"]),
    "chill":  dict(strobe=0.0, white=0.08, vocal=0.9, pulse=0.5, peak="swell", palettes=["midnight", "forest", "candle", "sunset"]),
    None:     dict(strobe=0.0, white=0.08, vocal=0.8, pulse=0.8, peak="swell", palettes=["midnight", "velvet", "rose", "sunset"]),
}


class Palette:
    def __init__(self, name):
        self.name = name
        self.cols = [hsv(h, s) for h, s in PALETTES[name]]
        self.arr = np.array(self.cols)

    def at(self, x):
        """smooth gradient through the palette, x wraps 0..1"""
        n = len(self.cols)
        x = (x % 1.0) * n
        i = np.floor(x).astype(int) % n
        f = (x - np.floor(x))[..., None]
        f = f * f * (3 - 2 * f)
        return self.arr[i] * (1 - f) + self.arr[(i + 1) % n] * f

    def c(self, i):
        return self.cols[int(i) % len(self.cols)]


# ---------------------------------------------------------------------------
class Strip:
    """Shared drawing helpers and persistent effect state for one LED strip."""

    def __init__(self, n):
        self.resize(n)

    def resize(self, n):
        self.n = n
        self.x = np.linspace(0, 1, n)
        self.dc = np.abs(self.x - 0.5) * 2          # 0 at the centre, 1 at the ends
        self.pulses = []                            # (birth, colour, speed, width, amp, origin)
        self.spark = np.zeros((n, 3))
        self.trail = np.zeros((n, 3))
        self.flash = np.zeros(3)
        self.flash_v = 0.0

    def dist(self, origin):
        return {"center": self.dc, "ends": 1 - self.dc, "left": self.x, "right": 1 - self.x}[origin]

    def add_pulse(self, t, col, speed=1.6, width=0.10, amp=1.0, origin="center"):
        self.pulses.append((t, np.array(col), speed, width, amp, origin))
        if len(self.pulses) > 10:
            self.pulses.pop(0)

    def draw_pulses(self, t):
        out = np.zeros((self.n, 3))
        keep = []
        for p in self.pulses:
            b, col, sp, w, amp, origin = p
            r = (t - b) * sp
            if r > 1.3:
                continue
            keep.append(p)
            d = self.dist(origin)
            g = np.exp(-0.5 * ((d - r) / w) ** 2) * amp * max(0.0, 1 - r / 1.3)
            out = np.maximum(out, g[:, None] * col)     # max, not sum: overlapping pulses don't go white
        self.pulses = keep
        return out

    def sparkle(self, k, col, amp=1.0):
        for _ in range(k):
            i = random.randrange(self.n)
            self.spark[i] = np.maximum(self.spark[i], np.array(col) * amp)

    def decay(self, dt, spark_tau=0.09, trail_tau=0.35):
        self.spark *= math.exp(-dt / spark_tau)
        self.trail *= math.exp(-dt / trail_tau)
        self.flash_v *= math.exp(-dt / 0.07)

    def blob(self, pos, width, col, amp=1.0):
        g = np.exp(-0.5 * ((self.x - pos) / width) ** 2) * amp
        return g[:, None] * np.array(col)


def lighten(col, w):
    """mix a colour toward white by w (0..1)"""
    return np.asarray(col) * (1 - w) + w


def beat_kick(A, c, F):
    """a kick worth a pulse: close to the beat (or very strong), at most one per beat"""
    if not A.hit_kick:
        return False
    near = min(A.phase, 1 - A.phase) < 0.2
    if not (near or A.kick.strength > 0.8):
        return False
    if c.get("kick_beat") == A.beat:
        return False
    if random.random() > F["pulse"] + 0.3:    # smooth genres skip some
        return False
    c["kick_beat"] = A.beat
    return True


# ---------------------------------------------------------------------------
# Looks. Each takes (strip, analysis, palette, ctx, feel) and returns an (n,3) float image.

def look_idle(S, A, P, c, F):
    return P.at(S.x * 0.6 + c["t"] * 0.01) * 0.06


def look_ambient(S, A, P, c, F):
    """No clear beat (intros, ballads): slow flowing colour that breathes with the music."""
    t = c["t"]
    br = 0.25 + 0.50 * A.energy_fast
    wave = 0.75 + 0.25 * np.sin(2 * math.pi * (S.x * 1.5 - t * 0.07))
    img = P.at(S.x * 0.8 + t * 0.015) * (br * wave)[:, None]
    if A.hit_hat:
        S.sparkle(1, lighten(P.c(2), 0.3 * F["white"] + 0.2), 0.35)
    return img + S.spark


def look_bassline(S, A, P, c, F):
    """The bass is the main voice: a glow from the centre that follows the bass line."""
    t = c["t"]
    base = P.at(S.x * 0.5 + t * 0.01) * 0.20
    lvl = c.setdefault("bl", 0.0)
    target = A.bass ** 1.3
    lvl += (target - lvl) * (0.5 if target > lvl else 0.12)
    c["bl"] = lvl
    width = 0.08 + 0.85 * lvl
    edge = np.clip((width - S.dc) / 0.12, 0, 1)
    col = P.c(A.bass_pc)
    img = np.maximum(base, (edge * (0.25 + 0.7 * lvl))[:, None] * col)
    if beat_kick(A, c, F):
        S.add_pulse(t, P.c(A.bass_pc + 1) * 0.8, speed=1.2, width=0.07, amp=0.6, origin=c["origin"])
    return np.maximum(img, S.draw_pulses(t)) + S.spark


def look_groove(S, A, P, c, F):
    """Verse groove: kick = a pulse (direction changes per section), snare = twin bursts, hats = sparkle."""
    t = c["t"]
    bar_col = c["bar"] % 4
    drift = (A.beat + c["phase"]) * 0.03
    img = P.at(S.x * 0.7 + drift) * (0.22 + 0.16 * A.energy)
    if beat_kick(A, c, F):
        S.add_pulse(t, P.c(bar_col), speed=0.9 / max(A.period, 0.3), width=0.09,
                    amp=0.9 * (0.6 + 0.4 * A.kick.strength), origin=c["origin"])
    if A.hit_snare:
        p = random.uniform(0.1, 0.4)
        col = lighten(P.c(bar_col + 2), 0.15 + 0.4 * F["white"])
        S.spark = np.maximum(S.spark, S.blob(0.5 - p, 0.035, col, 0.9) + S.blob(0.5 + p, 0.035, col, 0.9))
    if A.hit_hat:
        S.sparkle(1 + int(A.high), lighten(P.c(bar_col + 1), 0.2 + 0.5 * F["white"]), 0.45)
    return np.maximum(img, S.draw_pulses(t)) + S.spark


def look_chase(S, A, P, c, F):
    """Chorus: segments that march one step per beat (eased), colours swap each bar."""
    beatpos = A.beat + c["phase"]
    f = beatpos % 1.0
    step = math.floor(beatpos) + 1 - (1 - f) ** 3          # ease-out step per beat
    nseg = 4
    direction = -1 if c["origin"] in ("right", "ends") else 1
    u = (S.x * nseg - direction * step * 0.5) % 1.0
    seg = np.floor(S.x * nseg - direction * step * 0.5).astype(int)
    on = np.clip(np.minimum(u, 1 - u) * 6, 0, 1)
    cols = np.array([P.c(s + c["bar"]) for s in range(4)])
    img = cols[seg % 4] * (on * (0.30 + 0.55 * A.kick_env + 0.15))[:, None]
    if c["origin"] in ("center", "ends"):
        img = np.maximum(img, img[::-1])
    if A.hit_snare:
        S.sparkle(4, lighten(P.c(c["bar"] + 2), 0.2 + 0.6 * F["white"]), 0.8)
    if A.hit_hat:
        S.sparkle(1, lighten(P.c(c["bar"] + 1), 0.2 + 0.5 * F["white"]), 0.4)
    return img + S.spark


def look_peak(S, A, P, c, F):
    """Drop / peak: big blocks of colour that switch on the beat, kick slams, snare strobes (EDM/rock only)."""
    t = c["t"]
    nb = 6
    blk = np.floor(S.x * nb).astype(int)
    b = A.beat
    cols = np.array([P.c(i // 2 + A.bar // 2) for i in range(nb)])
    v = 0.35 + 0.55 * A.kick_env
    img = cols[blk % nb] * v
    img *= (0.85 + 0.15 * np.sin(2 * math.pi * (S.x * 8 + t * 3)) * A.bass)[:, None]
    if A.hit_snare and t - c.get("last_strobe", 0) > 0.11:
        c["last_strobe"] = t
        S.flash = lighten(P.c(b + 1), F["white"])
        S.flash_v = (0.3 + 0.6 * F["strobe"]) * A.snare.strength
    if A.hit_hat:
        S.sparkle(3, lighten(P.c(b + 2), 0.3 + 0.6 * F["white"]), 0.7)
    if beat_kick(A, c, F):
        S.add_pulse(t, lighten(P.c(b + 2), 0.15), speed=2.5, width=0.05, amp=0.5, origin=c["origin"])
    return np.maximum(img, S.draw_pulses(t)) + S.spark * 0.8 + S.flash * S.flash_v


def look_punch(S, A, P, c, F):
    """Hip-hop chorus: two-colour split that swaps sides on the beat, the 808 swells the glow,
    kick punches the brightness. No strobes."""
    t = c["t"]
    b = A.beat
    a, bcol = P.c(b // 2), P.c(b // 2 + 1)
    side = (S.x > 0.5) if (b % 2 == 0) else (S.x <= 0.5)
    soft = np.clip((np.abs(S.x - 0.5)) / 0.08, 0, 1)
    img = np.where(side[:, None], a, bcol) * soft[:, None] + (a + bcol) * 0.5 * (1 - soft)[:, None]
    lvl = 0.30 + 0.35 * A.bass + 0.35 * A.kick_env
    img = img * lvl
    if A.hit_snare:
        S.flash = lighten(P.c(b + 2), 0.2)
        S.flash_v = 0.3
    if A.hit_hat:
        S.sparkle(1, lighten(P.c(b + 3), 0.3), 0.4)
    return img + S.flash * S.flash_v * 0.7 + S.spark


def look_build(S, A, P, c, F):
    """Build-up: the chase doubles in speed, the strip fills in from the ends and brightens."""
    b = A.build
    rate = 2 ** min(3, int(b * 4))                # 1, 2, 4, 8 steps per beat
    pos = (A.beat + c["phase"]) * rate
    nseg = 6
    u = (S.x * nseg + pos * 0.25) % 1.0
    on = (u < 0.5).astype(float)
    col = lighten(P.c(c["bar"]), 0.75 * b * F["white"])
    fill = np.clip(((b * 1.05) - (1 - S.dc)) / 0.08, 0, 1)
    img = col * ((0.15 + 0.8 * b) * (0.3 + 0.7 * on) * (0.4 + 0.6 * fill))[:, None]
    if A.hit_snare:
        S.flash = col
        S.flash_v = 0.2 + 0.4 * b
    if b > 0.6 and A.energy_fast < 0.45:          # the music drops out right before the drop: go dark
        img *= 0.15
    return img + S.flash * S.flash_v * 0.6 + S.spark


def look_breakdown(S, A, P, c, F):
    """After a big part, when it goes quiet: deep slow colour that moves to the next colour on chord changes."""
    t = c["t"]
    ci = c.setdefault("ci", 0.0)
    if A.beat_tick and A.chord_change > 0.25:
        c["ci_target"] = c.get("ci_target", 0) + 1
    ci += (c.get("ci_target", 0) - ci) * 0.03
    c["ci"] = ci
    return P.at(S.x * 0.4 + ci / 4 + t * 0.005) * (0.24 + 0.3 * A.energy_fast) + S.spark


def look_swell(S, A, P, c, F):
    """Big moment in a smooth song (R&B, soul): a new colour swells out on every beat,
    eased over the whole beat. Snare = soft bloom, no strobe."""
    ph = c["phase"]
    e = 1 - (1 - ph) ** 2
    cur, nxt = P.c(A.bar // 2), P.c(A.bar // 2 + 1)
    if A.bar_beat != 3:                 # only the last beat of every 2nd bar actually moves to the next colour
        nxt = cur
    front = e * 1.1
    d = S.dist("ends" if c["origin"] == "ends" else "center")
    m = np.clip((front - d) / 0.25, 0, 1)[:, None]
    img = cur * (1 - m) + nxt * m
    img = img * (0.50 + 0.30 * A.energy_fast + 0.2 * A.kick_env)
    if A.hit_snare:
        S.flash = lighten(P.c(A.beat + 2), 0.15)
        S.flash_v = 0.25
    if A.hit_hat:
        S.sparkle(1, lighten(P.c(A.beat + 3), 0.25), 0.35)
    return img + S.flash * S.flash_v + S.spark


LOOKS = {
    "idle": look_idle, "ambient": look_ambient, "bassline": look_bassline, "groove": look_groove,
    "chase": look_chase, "peak": look_peak, "punch": look_punch, "build": look_build,
    "breakdown": look_breakdown, "swell": look_swell,
}
ORIGINS = ["center", "ends", "left", "right"]


# ---------------------------------------------------------------------------
class Director:
    def __init__(self, n=60):
        self.S = Strip(n)
        self.look = "idle"
        self.prev_look = None
        self.fade = 1.0
        self.fade_time = 0.4
        self.ctx = {}
        self.prev_ctx = {}
        self.palette = Palette("midnight")
        self.prev_palette = self.palette
        self.pal_idx = 0
        self.tier = 0
        self.had_peak_t = -1e9
        self.variant = 0
        self.lock = "auto"            # or a look name to force it
        self.brightness = 1.0
        self.budget_a = 2.0           # amps for the strip
        self.out = np.zeros((n, 3))
        self.last_change_bar = -99
        self.song_t0 = None
        self.events = []
        self.lookahead = 0.04         # s: compensate capture + display latency on beat-locked moves
        self.current_a = 0.0
        self.unlocked_beats = 0
        self.family = None            # from the genre net
        self.fam_set_t = None
        self.valence = 0.0
        self.origin_i = 0
        self.vox_amp = 0.0
        self.last_change_beat = -99
        self.gain = 1.0
        self._t = 0.0
        self.prefs = None             # your taste (prefs.Prefs), learned from A/B picks
        self.over = {}                # knob overrides (used by the B strip in A/B mode)
        self.look_map = {}            # look overrides (B strip)
        self.force_palette = None     # palette override (B strip)

    @property
    def feel(self):
        F = dict(FEEL.get(self.family, FEEL[None]))
        P = self.prefs
        for k in ("white", "vocal", "pulse"):
            F[k] = F[k] * (P.knob(self.family, k) if P else 1.0) * self.over.get(k, 1.0)
        F["white"] = min(F["white"], 1.0)
        F["exposure"] = (P.knob(self.family, "exposure") if P else 1.0) * self.over.get("exposure", 1.0)
        F["release"] = 0.07 * (P.knob(self.family, "release") if P else 1.0) * self.over.get("release", 1.0)
        return F

    def resize(self, n):
        self.S.resize(n)
        self.out = np.zeros((n, 3))

    def _set_look(self, name, A, fade=None):
        if self.prefs and name != "idle":
            name = self.prefs.look_sub(self.family, name) or name
        name = self.look_map.get(name, name)
        if name == self.look:
            return
        self.prev_look, self.prev_ctx = self.look, self.ctx
        self.look = name
        self.ctx = {}
        self.fade = 0.0
        self.fade_time = fade if fade is not None else max(0.15, min(0.6, A.period))
        self.events.append(("look", name))

    def _set_palette(self, name):
        if self.force_palette:
            name = self.force_palette
        if name != self.palette.name:
            self.prev_palette = self.palette
            self.palette = Palette(name)
            self.events.append(("palette", name))

    def _pick_palette(self, A, step=0):
        """first pick: by mood within the genre's family; later: move to a neighbour"""
        pals = list(self.feel["palettes"])
        sc = self.prefs.pal_scores(self.family) if self.prefs else {}
        if sc:
            # your picks for this kind of music: liked palettes first, disliked ones dropped
            extra = [p for p, v in sc.items() if v > 0 and p not in pals and p in PALETTES]
            pals = [p for p in pals + extra if sc.get(p, 0) > -2] or pals
            pals.sort(key=lambda p: -sc.get(p, 0))
            if step == 0:
                self.pal_idx = 0
            else:
                top = min(3, len(pals))
                self.pal_idx = (self.pal_idx + 1) % top
            self._set_palette(pals[self.pal_idx % len(pals)])
            return
        if step == 0:
            v = self.valence if self.family else (0.3 if not A.minor else -0.3)
            self.pal_idx = int(np.clip(round((v + 0.6) / 1.4 * (len(pals) - 1)), 0, len(pals) - 1))
        else:
            # stay in the family; step to a neighbour (bigger sections lean brighter)
            n = len(pals)
            cand = [(self.pal_idx + d) % n for d in (1, -1)]
            self.pal_idx = cand[0] if A.intensity > 0.6 else random.choice(cand)
        self._set_palette(pals[self.pal_idx])

    def set_genre(self, family, conf, valence):
        self.valence = valence
        if self.family and self.song_t0 is not None and self._t - self.song_t0 > 15:
            return                       # decided for this song: no palette swaps mid-song
        if family and conf > 0.3 and family != self.family:
            self.family = family
            self.events.append(("genre", family))
            self._pick_palette_now = True

    def choose(self, A):
        """called every hop; only acts on downbeats / drops"""
        if A.silent_for > 1.5:
            self._set_look("idle", A, fade=1.0)
            self.song_t0 = None
            return
        if self.song_t0 is None:
            self.song_t0 = A.t
            self._pick_palette(A)
        if getattr(self, "_pick_palette_now", False):
            self._pick_palette_now = False
            self._pick_palette(A)
        if self.lock != "auto":
            self._set_look(self.lock, A, fade=0.3)
            return

        F = self.feel
        if A.drop:
            self.had_peak_t = A.t
            self.tier = 3
            self._pick_palette(A, step=1)
            self.origin_i += 1
            big = F["peak"] if F["peak"] != "peak" or not (A.bpm < 100 and A.tconf > 0.3) else "swell"
            self._set_look(big, A, fade=0.0)
            self.S.flash, self.S.flash_v = lighten(self.palette.c(0), F["white"]), 0.6 + 0.4 * F["strobe"]
            self.last_change_bar = A.bar
            return

        it = A.intensity
        up = [0.33, 0.52, 0.70]
        dn = [0.27, 0.45, 0.62]
        want_tier = self.tier
        while want_tier < 3 and it > up[want_tier]:
            want_tier += 1
        while want_tier > 0 and it < dn[want_tier - 1]:
            want_tier -= 1
        if want_tier == 3 and A.t - self.song_t0 < 30 and A.t - A.last_drop_t > 60:
            want_tier = 2

        if not A.beat_tick:
            return
        if not A.downbeat:
            # without a solid beat grid, still don't change more than every 2 bars
            if A.locked or A.beat - self.last_change_beat < 8:
                return
        if A.section_change:
            self._pick_palette(A, step=1)
            self.variant ^= 1
            self.origin_i += 1
        elif A.bar - self.last_change_bar >= 16 and A.bar % 8 == 0:
            self.variant ^= 1
            self.origin_i += 1
        self.tier = want_tier

        self.unlocked_beats = 0 if A.locked else self.unlocked_beats + 1
        hold = min(8 * 4 * A.period, 16.0)
        if A.t - A.last_drop_t < hold and (it > 0.5 or A.t - A.last_drop_t < 4 * A.period):
            name = "peak"
            self.had_peak_t = A.t
        elif self.unlocked_beats >= 4 or A.tconf < 0.25:
            if A.t - self.had_peak_t < 90:
                name = "breakdown"
            else:
                name = "bassline" if A.bass_slow > 0.45 and A.kick_rate < 0.4 else "ambient"
        elif A.building and A.build > 0.25:
            name = "build"
        elif self.tier == 0:
            if A.t - self.had_peak_t < 90:
                name = "breakdown"
            else:
                name = "bassline" if A.bass_slow > 0.4 else "ambient"
        elif self.tier == 1:
            name = "groove" if (self.variant == 0 or A.bass_slow < 0.35) else "bassline"
        elif self.tier == 2:
            if F["peak"] == "swell":
                name = "groove" if self.variant == 0 else "swell"
            else:
                name = "chase" if self.variant == 0 else "groove"
        else:
            name = "peak"
            self.had_peak_t = A.t
        if name == "peak":
            name = F["peak"]
            if name == "peak" and A.bpm < 100 and A.tconf > 0.3:
                name = "swell"
        if name != self.look:
            self.last_change_bar = A.bar
            self.last_change_beat = A.beat
        self._set_look(name, A)

    def _vocal_layer(self, A, F):
        """the voice: a soft ribbon where the singer sits in the stereo mix, brightness follows the singing"""
        S = self.S
        v = A.vocal * F["vocal"]
        if A.singing_known:
            v *= 0.35 + 0.65 * A.singing
        if A.vocal_hit:
            self.vox_amp = max(self.vox_amp, 0.35)          # a new syllable / ad-lib pops
        self.vox_amp *= 0.92
        amp = min(1.0, 0.7 * v + self.vox_amp * F["vocal"])
        if amp < 0.02:
            return None
        width = 0.07 + 0.12 * v
        col = lighten(self.palette.c(3), 0.08 + 0.2 * F["white"])
        return S.blob(A.vocal_pan, width, col, amp)

    def render(self, A, t, dt):
        self._t = A.t
        self.choose(A)
        S = self.S
        F = self.feel
        S.decay(dt)
        ph = (A.phase + self.lookahead / max(A.period, 0.2))
        origin = ORIGINS[self.origin_i % 4] if F["pulse"] >= 0.8 else ORIGINS[self.origin_i % 2]
        for ctx in (self.ctx, self.prev_ctx):
            ctx["t"] = t
            ctx["phase"] = ph % 1.0
            ctx["bar"] = A.bar
            ctx["origin"] = origin
        img = LOOKS[self.look](S, A, self.palette, self.ctx, F)
        if self.fade < 1.0 and self.prev_look:
            self.fade = min(1.0, self.fade + dt / max(self.fade_time, 1e-3))
            old = LOOKS[self.prev_look](S, A, self.prev_palette, self.prev_ctx, F)
            f = self.fade
            img = img * f + old * (1 - f)
        if self.look != "idle":
            vox = self._vocal_layer(A, F)
            if vox is not None:
                img = np.maximum(img, vox)
        # hue-preserving clip: if a pixel goes over, scale it down instead of letting it turn white
        m = img.max(axis=1, keepdims=True)
        img = np.where(m > 1, img / np.maximum(m, 1e-6), img)
        # auto-exposure: the strip should never look dead while music plays. Aim for an average
        # brightness that follows the song's intensity, adjust slowly so the dynamics stay
        if self.look != "idle":
            lvl = float(img.max(axis=1).mean())
            target = F["exposure"] * (0.32 + 0.33 * A.intensity)
            want = float(np.clip(target / max(lvl, 0.02), 0.8, 3.0))
            self.gain += (want - self.gain) * (1 - math.exp(-dt / 1.5))
            img = img * self.gain
            m = img.max(axis=1, keepdims=True)
            img = np.where(m > 1, img / np.maximum(m, 1e-6), img)
        img = np.clip(img, 0, 1) * self.brightness
        # temporal smoothing: instant attack, ~35 ms release (kills flicker, keeps punch)
        rel = math.exp(-dt / F["release"])
        self.out = np.where(img > self.out, img, self.out * rel + img * (1 - rel))
        out = self.out.copy()
        # power limit for a real WS2812B strip: ~20 mA per channel at full, gamma 2.2
        lin = out ** 2.2
        amps = float(lin.sum()) * 0.020
        if amps > self.budget_a:
            out *= (self.budget_a / amps) ** (1 / 2.2)
            amps = self.budget_a
        self.current_a = amps
        return (out * 255 + 0.5).astype(np.uint8)

    def state(self):
        return {"look": self.look, "palette": self.palette.name, "tier": self.tier, "gain": round(self.gain, 2),
                "family": self.family, "amps": round(self.current_a, 2), "lock": self.lock,
                "pal_cols": [list((c * 255).astype(int)) for c in self.palette.cols]}
