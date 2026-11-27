"""The star string: a plain (non-addressable) light on J4's spare outputs, dimmed by the FPGA's PWM.
Used like the extra lamps in a club rig: a different job in each part of the song, mostly for contrast
with the strip (style "lj", the default):

  - verse / quiet parts: almost off, a slow low breathe (restraint makes the drop land)
  - mid parts: a hit on each strong kick with a warm afterglow, like a stage lamp cooling down
  - build-up: a strobe that speeds up with the build (quarter notes -> eighths -> sixteenths), getting brighter
  - the dark beat before the drop: only the stars, at full (a blinder)
  - the drop: one full hit, then "sidechain pump": on, and ducked by every kick
  - the 8 bars after a drop: pump; other big parts: kick hits with afterglow
  - cinema: lightning flicker on explosions and hits, candle flicker in warm dark scenes

Other styles: "kick" (one clean flash per strong on-beat kick) and "full" (the older twinkle show).
"""
import math


class Stars:
    def __init__(self):
        self.enabled = True
        self.cap = 1.0                 # the 30 ohm resistor already limits the string's current
        self.tw = [0.0, 0.0]           # twinkle envelopes per group
        self.hb = 0.0                  # heartbeat envelope
        self.drop = 0.0
        self.grp = 0
        self.last_tw = -9.0
        self.bar_tw = 0
        self.bar = None
        self.out = (0.0, 0.0)          # (group 1 = B, group 2 = G), 0..1 perceived
        self.test_until = -1.0
        self.last_kick = -9.0
        self.always_full = False       # True: simply on at 100% while the brain runs
        self.style = "lj"              # "lj" (roles per section), "kick" or "full"
        self.strobe = True             # False: no build-up strobe (pulses on the beat instead)
        self.glow = 0.0                # kick afterglow envelope
        self.duck = 0.0                # sidechain duck envelope (1 = fully ducked)
        self.after_drop = -99.0        # time of the last drop
        self.sub = None                # last strobe subdivision index
        self.strobe_on = 0.0           # how long the strobe has run without a break
        self.flick = []                # cinema lightning: times of the next flickers
        self.prev_flash = 0.0
        import random
        self.rnd = random.Random(7)

    def test(self, t, secs=3.0):
        self.test_until = t + secs

    def _bass(self, D, A, t, dt):
        """Stars follow the kick only: one clean, full flash per strong kick, never more than one per beat."""
        if D.look == "idle":
            return 0.0, 0.0
        per = max(0.3, getattr(A, "period", 0.5))
        on_beat = min(A.phase, 1.0 - A.phase) < 0.25           # near the beat grid, not a stray hit
        if (A.hit_kick and A.kick.strength > 0.4 and on_beat
                and t - self.last_kick > 0.7 * per):
            self.drop = 1.0
            self.last_kick = t
        return 0.0, 0.0

    def _kick(self, A, t, per, min_strength):
        """a strong kick near the beat grid, at most one per beat"""
        on_beat = min(A.phase, 1.0 - A.phase) < 0.25
        if A.hit_kick and A.kick.strength > min_strength and on_beat and t - self.last_kick > 0.7 * per:
            self.last_kick = t
            return True
        return False

    def _lj(self, D, A, t, dt):
        per = max(0.3, getattr(A, "period", 0.5))
        bp = A.beat + A.phase
        if A.drop:
            self.after_drop = t
            self.drop = 1.0                                       # the drop: one full hit
        if D.look == "idle":
            self.strobe_on = 0.0
            return 0.0
        # the dark beat before the drop: a blinder
        if D.blackout > 0.5:
            self.strobe_on = 0.0
            return 1.0
        # build-up: a strobe that speeds up with the build
        if D.look == "build":
            b = max(A.build, getattr(A, "plan_build", 0.0))
            div = 1 if b < 0.4 else 2 if b < 0.75 else 4          # hits per beat
            if not self.strobe:
                div = 1
            self.strobe_on += dt
            if div == 4 and self.strobe_on > 4.0:
                div = 2                                           # sixteenths in bursts of at most 4 s
            idx = int(bp * div)
            if idx != self.sub:
                self.sub = idx
                self.drop = max(self.drop, 0.3 + 0.7 * b)
            return 0.06 + 0.2 * b
        self.strobe_on = 0.0
        kick = self._kick(A, t, per, 0.4)
        # the 8 bars after a drop: sidechain pump
        if t - self.after_drop < 32 * per:
            if kick:
                self.duck = 1.0
            return 0.85 * (1.0 - 0.9 * self.duck)
        # quiet parts: almost off, a slow breathe
        if D.tier == 0 or D.look in ("wash", "bassfloor"):
            return 0.06 + 0.08 * (0.5 + 0.5 * math.sin(2 * math.pi * bp / 8.0))
        # mid and big parts: kick hits with a warm afterglow
        if kick:
            self.glow = max(self.glow, (0.6 if D.tier <= 1 else 0.8 if D.tier == 2 else 1.0)
                            * (0.6 + 0.4 * min(1.0, A.kick.strength)))
        return 0.0

    def _music(self, D, A, t, dt):
        bp = A.beat + A.phase
        base1 = base2 = 0.0
        quiet = D.look in ("wash", "bassfloor") or D.tier == 0
        if D.look == "idle":
            pass
        elif D.look == "build":
            b = max(A.build, getattr(A, "plan_build", 0.0))
            base1 = base2 = 0.15 + 0.85 * b
        elif quiet:
            ph = 2 * math.pi * bp / 8.0                    # one breath every 2 bars
            base1 = 0.45 + 0.3 * math.sin(ph)
            base2 = 0.45 + 0.3 * math.sin(ph + math.pi)  # the second group breathes the other way
        if D.blackout > 0.5:
            base1 = base2 = 0.9                            # the dark beat: only the stars glow
        if A.bar != self.bar:
            if self.bar is not None and D.tier >= 2 and D.look not in ("build", "idle"):
                self.hb = max(self.hb, 0.9)                # big parts: a flash on every bar's downbeat
            self.bar, self.bar_tw = A.bar, 0
        if A.hit_hat and D.tier <= 2 and D.look not in ("build", "idle") and self.bar_tw < 4 and t - self.last_tw > 0.15:
            self.tw[self.grp] = 0.9
            self.grp ^= 1
            self.bar_tw += 1
            self.last_tw = t
        if D.feel["style"] == "smooth" and A.hit_kick and A.kick.strength > 0.5 and 1 <= D.tier <= 2:
            self.hb = max(self.hb, 0.6)
        if A.drop:
            self.drop = 1.0
        return base1, base2

    def _cinema(self, D, A, t, dt):
        C = D.cine
        base = 0.0
        last = C.last_screen
        if last is not None and last[1] < 0.35 and not C.music_on:
            base = 0.12
            try:
                import numpy as np
                m = np.asarray(last[0], float).reshape(-1, 3).mean(axis=0)
                warm = m[0] > 1.3 * m[2] + 0.02
            except Exception:
                warm = False
            if warm:   # a warm, dark scene: candle flicker (two slow waves and a little noise)
                base = 0.22 + 0.06 * math.sin(2 * math.pi * t * 1.7) + 0.04 * math.sin(2 * math.pi * t * 4.3 + 1.0) \
                       + 0.04 * (self.rnd.random() - 0.5)
            else:      # a cold, dark scene: a slow glow
                base = 0.12 + 0.06 * math.sin(2 * math.pi * t / 6.0)
        # an impact: lightning, 2-4 quick flickers over ~0.4 s
        if C.flash > 0.3 and self.prev_flash <= 0.3:
            n = self.rnd.randint(2, 4)
            self.flick = sorted(t + self.rnd.uniform(0.0, 0.4) for _ in range(n))
            self.flick[0] = t
        self.prev_flash = C.flash
        while self.flick and t >= self.flick[0]:
            self.flick.pop(0)
            self.drop = max(self.drop, self.rnd.uniform(0.6, 1.0))
        return base, base

    def update(self, D, A, t, dt):
        per = max(0.3, getattr(A, "period", 0.5))
        cinema = D.mode == "cinema"
        if cinema:
            b1, b2 = self._cinema(D, A, t, dt)
        elif self.style == "lj":
            b1 = b2 = self._lj(D, A, t, dt)
        elif self.style == "kick":
            b1, b2 = self._bass(D, A, t, dt)
        else:
            b1, b2 = self._music(D, A, t, dt)
        self.tw = [v * math.exp(-dt / 0.12) for v in self.tw]
        self.hb *= math.exp(-dt / 0.25)
        self.glow *= math.exp(-dt / 0.25)                       # warm afterglow, like a lamp cooling down
        self.duck *= math.exp(-dt / (0.35 * per))               # sidechain release
        if cinema:
            self.drop *= math.exp(-dt / 0.05)                   # lightning: very short
        elif self.style == "kick":
            self.drop *= math.exp(-dt / 0.07)
            if self.drop < 0.08:
                self.drop = 0.0
        elif self.style == "lj":
            self.drop *= math.exp(-dt / (0.06 if D.look == "build" else 0.5))   # strobe hits / the drop's hit
        else:
            self.drop *= math.exp(-dt / (0.8 * per))
        g1 = max(b1, self.tw[0], self.hb, self.drop, self.glow)
        g2 = max(b2, self.tw[1], self.hb, self.drop, self.glow)
        if g1 < 0.03:
            g1 = 0.0
        if g2 < 0.03:
            g2 = 0.0
        if not self.enabled:
            g1 = g2 = 0.0
        k = self.cap * D.brightness
        self.out = (min(1.0, g1) * k, min(1.0, g2) * k)
        if t < self.test_until or (self.always_full and self.enabled):
            self.out = (1.0, 1.0)      # test: full power, whatever the brightness slider says
        return self.out
