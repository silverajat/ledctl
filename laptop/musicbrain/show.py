"""The light show: a director that runs the strip like a club lighting operator (LJ),
and an effect engine that draws looks on an addressable strip.

How real LJs work, and how that maps onto a strip:
  - the beat goes into MOVEMENT and COLOUR, not brightness. Brightness follows the size of the
    section (verse < chorus < drop) and changes slowly; within a section it stays steady.
  - the strip is treated as 8 virtual fixtures (like 8 lights on a truss), so looks read as bold shapes
    instead of noisy single pixels
  - layers: a base colour, one moving layer, and at most one accent at a time (snare wipe, ripple,
    drop hit). Most drum hits trigger nothing: restraint is what makes the accents land
  - phrases: a look is held for a section; it varies every 4 bars (direction, grouping) and changes on
    the next section. Builds speed up and close in, the beat before a drop goes dark, the drop hits
  - colours follow the harmony: the genre picks the colour family, the key picks the palette, the bass
    note / chord picks which colour leads. Same song = same colours, and they move when the chords move
  - contrast: two colours from opposite sides of the palette, moving against still, light against dark

This file is also the reference model for moving the effect engine into the FPGA.
"""
import math
import random
import numpy as np
import colorsys

from cinema import Cinema


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
# style: smooth = flowing, slow steps; punchy = hard swaps on the beat; club = full movement
FEEL = {
    "rnb":    dict(style="smooth", strobe=0.0, white=0.08, vocal=1.0, pulse=0.55, palettes=["midnight", "velvet", "amethyst", "rose"]),
    "hiphop": dict(style="punchy", strobe=0.0, white=0.08, vocal=0.7, pulse=0.85, palettes=["deep", "velvet", "noir", "club"]),
    "edm":    dict(style="club", strobe=0.3, white=0.08, vocal=0.35, pulse=1.0, palettes=["club", "laser", "acid", "fire"]),
    "pop":    dict(style="club", strobe=0.3, white=0.08, vocal=0.8, pulse=0.9, palettes=["rose", "sunset", "candy", "tropical"]),
    "rock":   dict(style="club", strobe=0.3, white=0.08, vocal=0.6, pulse=1.0, palettes=["deep", "fire", "candle", "sunset"]),
    "chill":  dict(style="smooth", strobe=0.0, white=0.08, vocal=0.9, pulse=0.5, palettes=["midnight", "forest", "candle", "sunset"]),
    None:     dict(style="smooth", strobe=0.0, white=0.08, vocal=0.8, pulse=0.8, palettes=["midnight", "velvet", "rose", "sunset"]),
}

# which looks each style uses at each size of music (tier 0 = quiet .. 3 = drop / peak)
POOLS = {
    "smooth": {0: ["wash", "bassfloor"], 1: ["bassfloor", "sweep", "stripes"], 2: ["sweep", "swell", "bounce"],
               3: ["swell", "sweep", "scanner"]},
    "punchy": {0: ["wash", "bassfloor"], 1: ["bassfloor", "bounce", "stripes"], 2: ["punch", "split", "bounce", "stripes"],
               3: ["punch", "scanner", "peak"]},
    "club":   {0: ["wash", "bassfloor"], 1: ["stripes", "bounce", "bassfloor"], 2: ["sweep", "chase", "stripes", "scanner"],
               3: ["peak", "scanner", "sweep"]},
}
# steps per beat for stepping looks
RATE = {"smooth": (0.25, 0.25, 0.5, 0.5), "punchy": (0.5, 0.5, 1.0, 1.0), "club": (0.5, 1.0, 1.0, 2.0)}
BLACK = 16 / 255             # below this a pixel is off (see _finish)
# brightness of each tier (the only thing that changes overall brightness, slowly)
TIER_LEVEL = (0.45, 0.62, 0.80, 1.0)

# which palette colour leads, by the bass note's place in the key (so colours move with the chords)
DEGREE_SLOT = {0: 0, 7: 1, 5: 3, 9: 2, 2: 2, 4: 1, 11: 1, 3: 2, 8: 3, 10: 3, 1: 2, 6: 3}


class Palette:
    def __init__(self, name):
        self.name = name
        self.hs = [(h, max(s, 0.78)) for h, s in PALETTES[name]]     # never washed out
        self.cols = [hsv(h, s) for h, s in self.hs]
        self.arr = np.array(self.cols)

    def far(self, i):
        """index of the colour whose hue is furthest from colour i (for two-colour contrast)"""
        n = len(self.hs)
        i %= n
        h0 = self.hs[i][0]
        d = [min(abs(h - h0), 1 - abs(h - h0)) for h, _ in self.hs]
        return int(np.argmax(d))

    def at(self, x):
        """smooth gradient through the palette, x wraps 0..1"""
        n = len(self.cols)
        x = (np.asarray(x) % 1.0) * n
        i = np.floor(x).astype(int) % n
        f = (x - np.floor(x))[..., None]
        f = f * f * (3 - 2 * f)
        return self.arr[i] * (1 - f) + self.arr[(i + 1) % n] * f

    def c(self, i):
        return self.cols[int(i) % len(self.cols)]


class Colours:
    """the four colour jobs of a look, from the palette and the harmony"""

    def __init__(self, P, root):
        self.pal = P
        self.root = root
        self.base = P.c(root)
        self.move = P.c(root + 1)
        fa = P.far(root + 1)                            # the contrast colour: furthest hue from `move`
        self.accent = P.c(fa)
        rest = [i for i in range(len(P.cols)) if i not in ((root + 1) % len(P.cols), fa)]
        self.voice = P.c(rest[-1] if rest else root + 3)

    def c(self, i):
        return self.pal.c(self.root + i)


def ease(f, d=0.12):
    """a snap: 0 -> 1 over the first d of the step, ease-out"""
    e = min(1.0, max(0.0, f / d))
    return 1 - (1 - e) ** 3


def rnd(k, seed=0.0):
    """stable pseudo-random 0..1 for an integer step (same step -> same value)"""
    return (math.sin(k * 12.9898 + seed * 78.233) * 43758.5453) % 1.0


def lighten(col, w):
    """mix a colour toward white by w (0..1)"""
    return np.asarray(col) * (1 - w) + w


# ---------------------------------------------------------------------------
class Rig:
    """The strip as a row of virtual fixtures, plus drawing helpers."""

    def __init__(self, n):
        self.resize(n)

    def resize(self, n):
        self.n = n
        self.x = np.linspace(0, 1, n)
        self.dc = np.abs(self.x - 0.5) * 2          # 0 at the centre, 1 at the ends
        self.nf = 8 if n >= 40 else 4
        self.fi = np.minimum((self.x * self.nf).astype(int), self.nf - 1)
        fu = self.x * self.nf - self.fi
        pxf = n / self.nf
        self.gap = np.clip(np.minimum(fu, 1 - fu) * pxf * 1.2, 0, 1)   # ~1 dark pixel between fixtures
        # mirrored fixture index: 0 at the centre, nf/2-1 at the ends
        self.fm = np.minimum((self.dc * self.nf / 2).astype(int), self.nf // 2 - 1)
        self.acc = []

    def dist(self, origin):
        return {"center": self.dc, "ends": 1 - self.dc, "left": self.x, "right": 1 - self.x}[origin]

    def beam(self, pos, width, col, amp=1.0):
        g = np.clip(1 - np.abs(self.x - pos) / width, 0, 1) ** 1.5 * amp
        return g[:, None] * np.asarray(col)


# ---------------------------------------------------------------------------
# Looks. Each takes (rig, analysis, colours, ctx) and returns an (n,3) float image, peak about 1.
# ctx: t, bp (continuous beat position incl. sync offset), bar, var (variation), rate, tier, origin, dir

def look_idle(R, A, C, x):
    """silence: a faint glow for a few seconds (a pause between songs), then fully off"""
    fade = float(np.clip(1 - (A.silent_for - 4.0) / 2.0, 0, 1))
    return C.pal.at(R.x * 0.6 + x["t"] * 0.01) * 0.10 * fade


def _clouds(R, t, sp):
    n1 = np.sin(2 * math.pi * (R.x * 1.3 + t * sp))
    n2 = np.sin(2 * math.pi * (R.x * 2.7 - t * sp * 1.7 + 0.3))
    n3 = np.sin(2 * math.pi * (R.x * 0.6 + t * sp * 0.6 + 0.7))
    return (n1 * 0.5 + n2 * 0.3 + n3 * 0.2) * 0.5 + 0.5


def look_wash(R, A, C, x):
    """Quiet parts: slow colour clouds drifting across the strip. Chords move the colours."""
    m = _clouds(R, x["t"], 0.03 + 0.04 * A.energy)
    if x["var"] % 2:
        img = C.pal.at(R.x * 0.5 + m * 0.3 + C.root / 4)
    else:
        img = C.base * (1 - m)[:, None] + C.move * m[:, None]
    return img * (0.55 + 0.45 * m)[:, None]


def look_bassfloor(R, A, C, x):
    """The bass is a pool of colour: it widens with the bass, the kick pushes its edge out."""
    lvl = x.setdefault("bw", 0.0)
    target = A.bass_rel ** 1.2
    lvl += (target - lvl) * (0.35 if target > lvl else 0.06)
    x["bw"] = lvl
    kb = x.get("kb", 0.0) * 0.86
    if A.hit_kick and A.kick.strength > 0.4:
        kb = max(kb, 0.10 * A.kick.strength)
    x["kb"] = kb
    w = 0.08 + 0.80 * lvl + kb
    d = R.dist("ends" if x["var"] % 2 else "center")
    core = np.clip((w - d) / 0.10, 0, 1)
    edge = np.exp(-0.5 * ((d - w) / 0.035) ** 2)
    m = _clouds(R, x["t"], 0.03)
    img = (C.move * (1 - m)[:, None] + C.base * m[:, None]) * 0.22
    img = np.maximum(img, core[:, None] * C.base * 0.95)
    return np.maximum(img, edge[:, None] * C.accent)


def look_bounce(R, A, C, x):
    """Colour bounce: neighbouring fixtures in two opposite colours that swap on the beat."""
    bp = x["bp"] * x["rate"]
    k = math.floor(bp)
    e = ease(bp - k, 0.10)
    v = x["var"] % 3
    grp = R.fi if v == 0 else (R.fi // 2 if v == 1 else R.fm)
    cA, cB = C.move, C.accent
    now = ((grp + k) % 2 == 0)[:, None]
    was = ((grp + k - 1) % 2 == 0)[:, None]
    img = np.where(now, cA, cB) * e + np.where(was, cA, cB) * (1 - e)
    return img * (R.gap * 0.95)[:, None]


def look_scanner(R, A, C, x):
    """Scanners: bars of light that snap to new positions on each step, mirrored. Colour flips each bar."""
    bp = x["bp"] * x["rate"]
    k = math.floor(bp)
    e = ease(bp - k, 0.14)
    nb = 2 if x["tier"] < 3 else 3
    img = C.base * 0.10 * np.ones((R.n, 1))
    col = C.move if x["bar"] % 2 == 0 else C.accent
    for j in range(nb):
        p0 = 0.04 + 0.42 * rnd(k - 1, j + x["var"])
        p1 = 0.04 + 0.42 * rnd(k, j + x["var"])
        p = p0 + (p1 - p0) * e
        img = np.maximum(img, R.beam(p, 0.05, col))
        img = np.maximum(img, R.beam(1 - p, 0.05, col))
    return img


def look_sweep(R, A, C, x):
    """Mirrored bars sweeping from the centre to the ends and back, one sweep per bar (or half bar)."""
    per = {0: 8, 1: 4, 2: 4, 3: 2}[x["tier"]] * (2 if x["style"] == "smooth" else 1)
    u = (x["bp"] % per) / per
    k = math.floor(x["bp"] / per)
    out = (k + x["var"]) % 2 == 0
    pos = u if out else 1 - u
    d = R.dc
    head = np.exp(-0.5 * ((d - pos) / 0.045) ** 2)
    behind = (pos - d) if out else (d - pos)
    trail = np.where(behind > 0, np.exp(-behind / 0.25), 0.0)
    col = C.move if k % 2 == 0 else C.accent
    img = C.base * 0.14 * np.ones((R.n, 1))
    img = np.maximum(img, trail[:, None] * col * 0.45)
    return np.maximum(img, head[:, None] * col)


def look_stripes(R, A, C, x):
    """Stripes (colour, colour, dark) that step along one fixture per step, so the direction reads;
    snares can reverse them."""
    bp = x["bp"] * x["rate"]
    k = math.floor(bp)
    s = k + ease(bp - k, 0.12)
    pos = R.x * R.nf - x["dir"] * s                  # in fixtures
    cols = np.array([C.move, C.accent, C.base * 0.12])
    i = np.floor(pos).astype(int) % 3
    f = pos - np.floor(pos)
    edge = np.clip(np.minimum(f, 1 - f) * R.n / R.nf * 1.2, 0, 1)
    return cols[i] * (0.92 * edge)[:, None]


def look_chase(R, A, C, x):
    """Step chase: one fixture lit, marching and bouncing off the ends (or a dark chase: one fixture off)."""
    bp = x["bp"] * x["rate"] * 2
    k = math.floor(bp)
    e = ease(bp - k, 0.15)
    cyc = 2 * (R.nf - 1)

    def fix(s):
        s %= cyc
        return s if s < R.nf else cyc - s
    j, jp = fix(k), fix(k - 1)
    on = (R.fi == j) * e + (R.fi == jp) * (1 - e) * 0.5
    if x["var"] % 2:                       # dark chase: everything lit, one fixture off
        img = C.accent * (0.85 * (1 - on))[:, None] + C.move * (on * 0.15)[:, None]
    else:
        img = np.maximum(C.base * 0.12 * np.ones((R.n, 1)), C.move * on[:, None])
    return img * R.gap[:, None]


def look_split(R, A, C, x):
    """Stereo: the left half follows the left speaker, the right half the right; the border moves with the mix."""
    b = 0.5 + 0.30 * A.lr
    xb = x.setdefault("xb", 0.5)
    xb += (b - xb) * 0.15
    x["xb"] = xb
    lL = 0.35 + 0.65 * A.lvl_l
    lR = 0.35 + 0.65 * A.lvl_r
    m = np.clip((R.x - xb) / 0.04 + 0.5, 0, 1)
    img = C.move * ((1 - m) * lL)[:, None] + C.accent * (m * lR)[:, None]
    seam = np.exp(-0.5 * ((R.x - xb) / 0.02) ** 2)
    return np.maximum(img, seam[:, None] * C.voice * 0.8)


def look_build(R, A, C, x):
    """Build-up: lights close in from the ends, steps speed up (1, 2, 4 per beat), colours spin."""
    b = max(A.build, x.get("plan_build", 0.0))
    rate = 2 ** min(2, int(b * 3))
    bp = x["bp"] * rate
    k = math.floor(bp)
    s = k + ease(bp - k, 0.2)
    fill = 0.15 + b * 0.9
    inside = np.clip((fill - (1 - R.dc)) / 0.06, 0, 1)
    u = (R.x * R.nf / 2 + s * 0.5) % 1.0
    on = (u < 0.5).astype(float) * 0.6 + 0.4
    spin = C.pal.at(R.dc * 0.25 + x["t"] * (0.05 + 0.5 * b))
    return spin * (inside * on * (0.45 + 0.55 * b))[:, None]


def look_peak(R, A, C, x):
    """Peak / drop: mirrored blocks of colour that change on every beat, plus scanners sweeping the bar."""
    bp = x["bp"] * x["rate"]
    k = math.floor(bp)
    e = ease(bp - k, 0.06)
    cols = np.array([C.c(i) for i in range(4)])
    now = cols[(R.fm + k) % 4]
    was = cols[(R.fm + k - 1) % 4]
    img = (now * e + was * (1 - e)) * (R.gap * 0.85)[:, None]
    u = (x["bp"] % 4) / 4
    pos = 0.5 + 0.5 * (u if x["bar"] % 2 == 0 else 1 - u)
    img = np.maximum(img, R.beam(pos, 0.04, C.accent))
    return np.maximum(img, R.beam(1 - pos, 0.04, C.accent))


def look_punch(R, A, C, x):
    """Hip-hop: a two-colour split that swaps sides on the beat; the 808 widens the seam."""
    bp = x["bp"] * x["rate"]
    k = math.floor(bp)
    e = ease(bp - k, 0.08)
    a, b2 = C.move, C.accent
    sw = 0.04 + 0.12 * A.bass_rel
    m = np.clip((R.x - 0.5) / sw + 0.5, 0, 1)
    side = k % 2 == 0
    cur = (a * (1 - m)[:, None] + b2 * m[:, None]) if side else (b2 * (1 - m)[:, None] + a * m[:, None])
    prv = (b2 * (1 - m)[:, None] + a * m[:, None]) if side else (a * (1 - m)[:, None] + b2 * m[:, None])
    return (cur * e + prv * (1 - e)) * 0.9


def look_swell(R, A, C, x):
    """Big moment in a smooth song (R&B, soul): a new colour swells out from the centre over each 2 bars."""
    per = 8.0
    u = (x["bp"] % per) / per
    k = math.floor(x["bp"] / per)
    e = 1 - (1 - u) ** 2
    cur, nxt = C.c(k), C.c(k + 1)
    d = R.dist("ends" if x["var"] % 2 else "center")
    m = np.clip((e * 1.15 - d) / 0.25, 0, 1)[:, None]
    return cur * (1 - m) + nxt * m


LOOKS = {
    "idle": look_idle, "wash": look_wash, "bassfloor": look_bassfloor, "bounce": look_bounce,
    "scanner": look_scanner, "sweep": look_sweep, "stripes": look_stripes, "chase": look_chase,
    "split": look_split, "build": look_build, "peak": look_peak, "punch": look_punch, "swell": look_swell,
}
STEPPED = {"bounce", "scanner", "stripes", "chase", "peak", "punch"}
ORIGINS = ["center", "ends"]


# ---------------------------------------------------------------------------
class Director:
    def __init__(self, n=60):
        self.S = Rig(n)
        self.look = "idle"
        self.prev_look = None
        self.fade = 1.0
        self.fade_time = 0.4
        self.ctx = {}
        self.prev_ctx = {}
        self.palette = Palette("midnight")
        self.prev_palette = self.palette
        self.pal_idx = 0
        self.root = 0                 # which palette colour leads (follows the chords)
        self.root_cand, self.root_n = None, 0
        self.tier = 0
        self.had_peak_t = -1e9
        self.variant = 0
        self.lock = "auto"            # or a look name to force it
        self.brightness = 1.0
        self.budget_a = 2.0           # amps for the strip
        self.out = np.zeros((n, 3))
        self.last_change_bar = -99
        self.last_tier_bar = -99
        self.sec_seq = 0
        self.song_t0 = None
        self.events = []
        self.lookahead = 0.04         # s: compensate capture + display latency on beat-locked moves
        self.current_a = 0.0
        self.unlocked_beats = 0
        self.family = None            # from the genre net
        self.valence = 0.0
        self.vox_amp = 0.0
        self.last_change_beat = -99
        self.gain = 1.0
        self.level = TIER_LEVEL[0]
        self._t = 0.0
        self.dir = 1
        self.last_wipe_bar = -99
        self.last_ripple_t = -1e9
        self.blackout = 0.0
        self.prefs = None             # your taste (prefs.Prefs), learned from A/B picks
        self.over = {}                # knob overrides (used by the B strip in A/B mode)
        self.look_map = {}            # look overrides (B strip)
        self.force_palette = None     # palette override (B strip)
        self.mode = "music"           # or "cinema" (films, YouTube, games)
        self.cine = Cinema()
        self.scene = {}               # what the sound is (speech / music / effects), from YAMNet
        self.screen_img = None        # the shrunk screen, in cinema mode
        self.screen_on = False

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
        if name not in LOOKS:
            name = "wash"
        if name == self.look:
            return
        self.prev_look, self.prev_ctx = self.look, self.ctx
        self.look = name
        self.ctx = {}
        self.fade = 0.0
        self.fade_time = fade if fade is not None else max(0.12, min(0.5, A.period * 0.5))
        self.events.append(("look", name))

    def _set_palette(self, name):
        if self.force_palette:
            name = self.force_palette
        if name != self.palette.name:
            self.prev_palette = self.palette
            self.palette = Palette(name)
            self.events.append(("palette", name))

    def _new_palette(self, A):
        self._pick_palette(A, step=1)

    def _pick_palette(self, A, step=0):
        """first pick: from the mood and the key (same song -> same colours); later: a neighbour"""
        pals = list(self.feel["palettes"])
        sc = self.prefs.pal_scores(self.family) if self.prefs else {}
        if sc:
            extra = [p for p, v in sc.items() if v > 0 and p not in pals and p in PALETTES]
            pals = [p for p in pals + extra if sc.get(p, 0) > -2] or pals
            pals.sort(key=lambda p: -sc.get(p, 0))
        n = len(pals)
        if step == 0:
            v = self.valence if self.family else (0.3 if not A.minor else -0.3)
            mood = (v + 0.6) / 1.4                         # 0 dark .. 1 bright
            # the key nudges the choice: songs in different keys get different palettes of the same family
            i = int(np.clip(round(mood * (n - 1) + ((A.key * 7) % 12 - 5.5) / 12.0), 0, n - 1))
            self.pal_idx = i
        else:
            self.pal_idx = (self.pal_idx + (1 if A.intensity > 0.6 else -1)) % n
        self._set_palette(pals[self.pal_idx % n])

    def sane_family(self, scores, bpm, perc, tonal, locked):
        """the genre net hears instruments, not style: an R&B song with guitar can come out as "rock".
        Check its answer against tempo and how much drums there are."""
        if not scores:
            return None
        sc = dict(scores)
        top = sc.pop("_top", None)
        if top in sc:
            sc[top] *= 1.5                      # the single most likely genre counts extra
        if locked:
            if bpm < 100 or perc < 0.22:
                sc["rock"] = sc.get("rock", 0) * 0.35
            if bpm < 105:
                sc["edm"] = sc.get("edm", 0) * 0.4
            if perc < 0.3 and tonal > 0.55:     # smooth, sustained, light drums: not a club track
                sc["edm"] = sc.get("edm", 0) * 0.5
                sc["rock"] = sc.get("rock", 0) * 0.5
            if bpm < 100 and tonal > 0.45:
                sc["rnb"] = sc.get("rnb", 0) * 1.6
        return max(sc, key=sc.get)

    def set_genre(self, family, conf, valence, scores=None, A=None):
        if scores and A is not None:
            family = self.sane_family(scores, A.bpm, A.perc, A.tonal, A.locked and A.tconf > 0.3) or family
        self.valence = valence
        if self.family and self.song_t0 is not None and self._t - self.song_t0 > 15:
            # decided for this song; change our mind once in the first minute if the net keeps disagreeing
            # (intros often sound different from the song), and switch colours at the next section
            if family and family != self.family and conf > 0.3 and self._t - self.song_t0 < 60 \
                    and not getattr(self, "_revised", False):
                self._disagree = getattr(self, "_disagree", 0) + 1
                if self._disagree >= 8:
                    self.family, self._revised, self._disagree = family, True, 0
                    self.events.append(("genre", family))
                    self._pick_on_section = True
            else:
                self._disagree = 0
            return
        if family and conf > 0.3 and family != self.family:
            self.family = family
            self.events.append(("genre", family))
            self._pick_palette_now = True

    def _harmony(self, A):
        """on each beat: which palette colour leads, from the bass note's place in the key"""
        if not A.beat_tick or not A.beat_feat:
            return
        bpc = A.beat_feat[-1]["bpc"]
        if bpc < 0 or A.bass_rel < 0.25:
            return
        slot = DEGREE_SLOT[(bpc - A.key) % 12]
        if slot == self.root:
            self.root_cand, self.root_n = None, 0
            return
        if slot == self.root_cand:
            self.root_n += 1
        else:
            self.root_cand, self.root_n = slot, 1
        if self.root_n >= 2 or (A.chord_change > 0.35 and A.downbeat):
            self.root = slot
            self.root_cand, self.root_n = None, 0

    def choose(self, A):
        """called every hop; only acts on downbeats / drops"""
        if A.silent_for > 1.5:
            self._set_look("idle", A, fade=1.0)
            self.song_t0 = None
            return
        if self.song_t0 is None:
            self.song_t0 = A.t
            self.root = 0
            self._revised, self._disagree, self._pick_on_section = False, 0, False
            self._pick_palette(A)
        if getattr(self, "_pick_palette_now", False):
            self._pick_palette_now = False
            self._pick_palette(A)
        self._harmony(A)
        if self.lock != "auto":
            self._set_look(self.lock, A, fade=0.3)
            return

        F = self.feel
        style = F["style"]
        if A.drop:
            self.had_peak_t = A.t
            self.tier = 3
            self.last_tier_bar = A.bar
            self._pick_palette(A, step=1)
            self.sec_seq += 1
            self.variant += 1
            self._set_look(self._pool(style, 3), A, fade=0.0)
            self._accent("drop", A, self.cols.c(2))
            self.last_change_bar = A.bar
            return

        it = A.intensity
        pe = getattr(A, "plan_energy", None)            # known song: the section's real size
        if pe is not None:
            it = 0.5 * it + 0.5 * (0.15 + 0.8 * pe)
        up = [0.33, 0.52, 0.70]
        dn = [0.27, 0.45, 0.62]
        want = self.tier
        while want < 3 and it > up[want]:
            want += 1
        while want > 0 and it < dn[want - 1]:
            want -= 1
        if want == 3 and A.t - self.song_t0 < 30 and A.t - A.last_drop_t > 60 and pe is None:
            want = 2

        if not A.beat_tick:
            return
        if not A.downbeat:
            if A.locked or A.beat - self.last_change_beat < 8:
                return
        bars = A.bar - self.last_change_bar
        new_section = A.section_change
        if new_section:
            # new colours only for a big moment (into a chorus / peak, or a big jump), like an LJ would;
            # otherwise the chords keep moving the colours inside the song's palette
            label = getattr(A, "plan_label", None)
            big = (label in ("chorus", "peak") and getattr(self, "_last_label", None) not in ("chorus", "peak")) \
                if label is not None else (want > self.tier)
            self._last_label = label
            if big or getattr(self, "_pick_on_section", False):
                self._pick_on_section = False
                self._pick_palette(A, step=1)
            self.sec_seq += 1
            self.variant += 1
        elif bars > 0 and bars % 4 == 0:
            self.variant += 1                            # a variation every 4 bars: direction, grouping
            self.dir = -self.dir if random.random() < 0.5 else self.dir
            if bars % 16 == 0:
                self.sec_seq += 1                        # long section: rotate the look anyway
        # tier changes land on phrase lines: up on 4-bar lines, down on 8-bar lines (or a new section)
        tb = A.bar - self.last_tier_bar
        if want != self.tier and (new_section or (want > self.tier and tb >= 4 and A.bar % 4 == 0)
                                  or (want < self.tier and tb >= 8 and A.bar % 8 == 0) or not A.locked):
            self.tier = want
            self.last_tier_bar = A.bar
            self.sec_seq += 1

        self.unlocked_beats = 0 if A.locked else self.unlocked_beats + 1
        hold = min(8 * 4 * A.period, 16.0)
        if A.t - A.last_drop_t < hold and (it > 0.5 or A.t - A.last_drop_t < 4 * A.period):
            name = self._pool(style, 3)
            self.had_peak_t = A.t
        elif self.unlocked_beats >= 4 or A.tconf < 0.25:
            name = "bassfloor" if A.bass_slow > 0.45 and A.kick_rate < 0.4 else "wash"
        elif (A.building and A.build > 0.25) or getattr(A, "plan_build", 0) > 0.25:
            name = "build"
        else:
            name = self._pool(style, self.tier)
            if self.tier == 3:
                self.had_peak_t = A.t
            if A.perc < 0.08 and A.tonal > 0.6 and self.tier < 3 and name in STEPPED:
                name = "sweep" if self.tier >= 1 else "wash"      # no drums to step to: keep it flowing
        if name != self.look:
            self.last_change_bar = A.bar
            self.last_change_beat = A.beat
        self._set_look(name, A)

    def _pool(self, style, tier):
        pool = POOLS[style][tier]
        return pool[self.sec_seq % len(pool)]

    # ---- accents: one at a time, rate-limited
    def _accent(self, kind, A, col, pos=0.5):
        self.S.acc = [(kind, A.t, np.asarray(col), pos, self.dir, A.period)]
        self.events.append(("accent", kind))

    def _accents(self, A, F):
        """decide accents (only on clear hits, never on every drum)"""
        if self.look in ("idle", "wash", "build") or self.lock != "auto":
            return
        if A.hit_snare and A.snare.strength > 0.7 and self.tier >= 2 and A.bar - self.last_wipe_bar >= 2 \
                and A.bar_beat in (1, 3) and not self.S.acc:
            self.last_wipe_bar = A.bar
            if self.look == "stripes":
                self.dir = -self.dir                     # stripes: the snare flips the direction instead
            else:
                self.dir = -self.dir
                self._accent("wipe", A, self.cols.c(2))
        if A.impact and A.impact_strength > 0.85 and A.t - self.last_ripple_t > 16 * A.period and self.tier >= 2 \
                and not self.S.acc:
            self.last_ripple_t = A.t
            self._accent("ripple", A, self.cols.c(3), pos=0.5)

    def _draw_accents(self, img, t):
        R = self.S
        keep = []
        for kind, t0, col, pos, dr, per in R.acc:
            age = t - t0
            if kind == "drop":
                dur = per * 1.0
                if age < dur:
                    a = (1 - age / dur) ** 2
                    img = np.maximum(img, col * a)
                    keep.append((kind, t0, col, pos, dr, per))
            elif kind == "wipe":
                dur = per * 0.6
                if age < dur:
                    f = age / dur * 1.3
                    xx = R.x if dr > 0 else 1 - R.x
                    band = np.clip(1 - np.abs(xx - f + 0.08) / 0.12, 0, 1)
                    img = np.maximum(img, band[:, None] * col)
                    keep.append((kind, t0, col, pos, dr, per))
            elif kind == "ripple":
                dur = 0.6
                if age < dur:
                    r = age / dur * 0.6
                    ring = np.exp(-0.5 * ((np.abs(R.x - pos) - r) / 0.035) ** 2) * (1 - age / dur)
                    img = np.maximum(img, ring[:, None] * col)
                    keep.append((kind, t0, col, pos, dr, per))
        R.acc = keep
        return img

    def _vocal_layer(self, A, F):
        """the voice: a soft glow that follows the melody (pitch) and where the singer sits in the mix"""
        R = self.S
        v = A.vocal * F["vocal"]
        if A.singing_known:
            v *= 0.25 + 0.75 * A.singing
        if A.vocal_hit:
            self.vox_amp = max(self.vox_amp, 0.3)
        self.vox_amp *= 0.92
        amp = min(0.6, 0.55 * v + self.vox_amp * F["vocal"])
        if amp < 0.04:
            return None
        pos = A.vocal_pan
        if A.lead_conf > 0.3:
            pos = 0.4 * A.vocal_pan + 0.6 * (0.15 + 0.7 * A.lead_pos)
        width = 0.05 + 0.08 * v
        col = lighten(self.cols.voice, 0.08)
        return R.beam(pos, width, col, amp)

    @property
    def cols(self):
        return Colours(self.palette, self.root)

    def render(self, A, t, dt):
        self._t = A.t
        if self.mode == "cinema":
            img = self.cine.render(self.S, A, self, t, dt) * self.brightness
            self.out = img
            return self._finish(img.copy())
        self.choose(A)
        R = self.S
        F = self.feel
        style = F["style"]
        bp = A.beat + A.phase + self.lookahead / max(A.period, 0.2)
        if A.phase + self.lookahead / max(A.period, 0.2) < 0:
            bp = A.beat + A.phase
        rate = RATE[style][self.tier]
        if A.bpm > 140 and rate > 1:
            rate = 1.0
        for c in (self.ctx, self.prev_ctx):
            c.update(t=t, bp=bp, bar=A.bar, var=self.variant, rate=rate, tier=self.tier, style=style,
                     dir=self.dir, origin=ORIGINS[self.variant % 2],
                     plan_build=getattr(A, "plan_build", 0.0))
        C = self.cols
        img = LOOKS[self.look](R, A, C, self.ctx)
        if self.fade < 1.0 and self.prev_look:
            self.fade = min(1.0, self.fade + dt / max(self.fade_time, 1e-3))
            old = LOOKS[self.prev_look](R, A, Colours(self.prev_palette, self.root), self.prev_ctx)
            f = self.fade
            img = img * f + old * (1 - f)
        if self.look != "idle":
            self._accents(A, F)
            img = self._draw_accents(img, A.t)
            if self.look not in ("build", "peak"):
                vox = self._vocal_layer(A, F)
                if vox is not None:
                    img = np.maximum(img, vox)
        # the beat before a drop goes dark (known song: exactly; otherwise when the music drops out)
        drop_in = getattr(A, "drop_in", None)
        dark = (drop_in is not None and 0 < drop_in < 0.9 * A.period) or \
               (self.look == "build" and A.build > 0.6 and A.energy_fast < 0.45)
        self.blackout += ((1.0 if dark else 0.0) - self.blackout) * (0.5 if dark else 0.35)
        img = img * (1 - self.blackout)                  # dark means dark: fully off
        # hue-preserving clip: a pixel that goes over is scaled down instead of turning white
        m = img.max(axis=1, keepdims=True)
        img = np.where(m > 1, img / np.maximum(m, 1e-6), img)
        # brightness follows the size of the section (slowly), never the individual beat
        if self.look != "idle":
            tgt = TIER_LEVEL[self.tier] * F["exposure"]
            self.level += (tgt - self.level) * (1 - math.exp(-dt / 1.5))
            lvl = float(img.max(axis=1).mean())
            want = float(np.clip(self.level * 0.55 / max(lvl, 0.05), 0.35, 1.8))
            self.gain += (want - self.gain) * (1 - math.exp(-dt / 3.0))
            img = img * self.gain * (0.75 + 0.25 * self.level)
            m = img.max(axis=1, keepdims=True)
            img = np.where(m > 1, img / np.maximum(m, 1e-6), img)
        img = np.clip(img, 0, 1) * self.brightness
        # temporal smoothing: instant attack, short release (kills flicker, keeps the snaps crisp)
        rel = math.exp(-dt / F["release"])
        self.out = np.where(img > self.out, img, self.out * rel + img * (1 - rel))
        return self._finish(self.out.copy())

    def _finish(self, out):
        # anything dimmer than ~6 % is switched off: on real LEDs the lowest steps still glow visibly in a dark
        # room while they look black on a screen, so cutting them keeps the strip and the bench the same
        out = np.where(out.max(axis=1, keepdims=True) < BLACK, 0.0, out)
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
                "family": self.family, "amps": round(self.current_a, 2), "lock": self.lock, "root": self.root,
                "style": self.feel["style"], "mode": self.mode, "cine": self.cine.status,
                "intensity": self.cine.intensity, "bright": round(self.brightness, 2),
                "blackout": round(self.blackout, 2),
                "pal_cols": [list((c * 255).astype(int)) for c in self.palette.cols]}
