"""The star string: a plain (non-addressable) light on J4's spare outputs, dimmed by the FPGA's PWM.
All stars do the same thing, so it is used like one extra light in a club rig: mostly off, and used for
contrast. Group 1 = J4 pin 4 (B), group 2 = J4 pin 2 (G) if the string is split in two.

  - quiet parts: a slow breathe, like stars at night
  - hi-hats in quiet parts: a quick twinkle (at most 4 a bar, alternating groups)
  - slow songs (smooth style): a soft heartbeat on the kick
  - build-up: rising with the build
  - the dark beat before a drop: only the stars glow, then the drop hits them with everything else
  - big parts: off (restraint)
  - cinema: a slow glow in dark, calm scenes; impacts flash them with the strip
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
        self.always_full = True        # stars simply on at 100% while the brain runs (False: follow the music)

    def test(self, t, secs=3.0):
        self.test_until = t + secs

    def _music(self, D, A, t, dt):
        bp = A.beat + A.phase
        base1 = base2 = 0.0
        quiet = D.look in ("wash", "bassfloor") or D.tier == 0
        if D.look == "idle":
            pass
        elif D.look == "build":
            b = max(A.build, getattr(A, "plan_build", 0.0))
            base1 = base2 = 0.08 + 0.5 * b
        elif quiet:
            ph = 2 * math.pi * bp / 8.0                    # one breath every 2 bars
            base1 = 0.16 + 0.12 * math.sin(ph)
            base2 = 0.16 + 0.12 * math.sin(ph + math.pi)  # the second group breathes the other way
        if D.blackout > 0.5:
            base1 = base2 = 0.55                           # the dark beat: only the stars glow
        if A.bar != self.bar:
            self.bar, self.bar_tw = A.bar, 0
        if A.hit_hat and D.tier <= 1 and D.look not in ("build", "idle") and self.bar_tw < 4 and t - self.last_tw > 0.15:
            self.tw[self.grp] = 0.45
            self.grp ^= 1
            self.bar_tw += 1
            self.last_tw = t
        if D.feel["style"] == "smooth" and A.hit_kick and A.kick.strength > 0.5 and 1 <= D.tier <= 2:
            self.hb = max(self.hb, 0.3)
        if A.drop:
            self.drop = 1.0
        return base1, base2

    def _cinema(self, D, A, t, dt):
        C = D.cine
        base = 0.0
        last = C.last_screen
        dark_scene = last is not None and last[1] < 0.35
        if dark_scene and not C.music_on:
            base = 0.12 + 0.06 * math.sin(2 * math.pi * t / 6.0)
        if C.flash > 0.3:
            self.drop = max(self.drop, C.flash)
        return base, base

    def update(self, D, A, t, dt):
        if D.mode == "cinema":
            b1, b2 = self._cinema(D, A, t, dt)
        else:
            b1, b2 = self._music(D, A, t, dt)
        per = max(0.3, getattr(A, "period", 0.5))
        self.tw = [v * math.exp(-dt / 0.12) for v in self.tw]
        self.hb *= math.exp(-dt / 0.2)
        self.drop *= math.exp(-dt / (0.8 * per))
        g1 = max(b1, self.tw[0], self.hb, self.drop)
        g2 = max(b2, self.tw[1], self.hb, self.drop)
        if not self.enabled:
            g1 = g2 = 0.0
        k = self.cap * D.brightness
        self.out = (min(1.0, g1) * k, min(1.0, g2) * k)
        if t < self.test_until or (self.always_full and self.enabled):
            self.out = (1.0, 1.0)      # test: full power, whatever the brightness slider says
        return self.out
