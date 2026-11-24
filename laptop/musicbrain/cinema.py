"""Cinema mode: lights for films, YouTube and games, plus sound.

  - the screen: the laptop screen is grabbed ~25 times a second, shrunk to 64x36 by the graphics driver
    (cheap, no extra installs), black bars cut off
  - two layouts:
      room (default)  - the strip is somewhere in the room (around a board, behind a bed, along a shelf),
                        so it takes the scene's 2-3 main colours, the way Hue Sync lights a room: the
                        strongest colour in the middle, the second towards the ends, changing slowly
      screen          - the strip is around the screen: each part takes the colour of the screen edge
                        next to it (Ambilight)
    white and grey never become white light: they turn into a dim tint
  - the sound: dialogue keeps the lights calm, a film score lifts them slowly, quiet scenes dim, big hits
    (explosions, gunshots, slams) send one accent in the scene's own colour; music with a clear beat sends
    a soft wave from the centre on each beat. The sound decisions are smoothed over a few seconds so the
    behaviour doesn't flip back and forth
  - intensity: subtle (films), normal, intense (games), like Hue Sync's settings

Without screen capture (not Windows, or turned off) it runs a calm palette wash with the same sound
reactions. DRM-protected video (Netflix and other streaming sites) can show up black to screen capture:
when the screen stays black while the film is clearly playing, it switches to mood colours that follow the
sound until the picture can be seen again.
"""
import math
import os
import threading
import time

import numpy as np

INTENSITY = {
    # tau: how fast colours follow the picture (screen layout); room: the same for the room layout
    "subtle": dict(tau=0.9, room=3.0, accent=0.35, sat=1.25, motion=0.0),
    "normal": dict(tau=0.40, room=1.5, accent=0.65, sat=1.45, motion=0.3),
    "intense": dict(tau=0.15, room=0.6, accent=1.0, sat=1.65, motion=0.6),
}


def hue_of(rgb):
    mx, mn = rgb.max(axis=-1), rgb.min(axis=-1)
    c = mx - mn + 1e-9
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    h = np.where(mx == r, ((g - b) / c) % 6, np.where(mx == g, (b - r) / c + 2, (r - g) / c + 4)) / 6
    return h % 1.0


def dominant(img, k=3):
    """the main colours of a picture: a hue histogram where colourful, bright pixels count most.
    returns [(rgb normalised to max 1, share), ...] strongest first, and the picture's brightness"""
    px = img.reshape(-1, 3)
    mx, mn = px.max(axis=1), px.min(axis=1)
    sat = (mx - mn) / np.maximum(mx, 1e-3)
    w = sat ** 1.5 * mx ** 1.5
    bright = float(mx.mean())
    if w.sum() < 0.5:
        return [], bright
    h = hue_of(px)
    nb = 24
    hb = (h * nb).astype(int) % nb
    hist = np.bincount(hb, weights=w, minlength=nb)
    hist = 0.5 * hist + 0.25 * (np.roll(hist, 1) + np.roll(hist, -1))
    out = []
    hh = hist.copy()
    total = hist.sum()
    for _ in range(k):
        j = int(np.argmax(hh))
        if hh[j] <= 0 or (out and hh[j] < 0.2 * out[0][2]):
            break
        sel = (np.abs((hb - j + nb // 2) % nb - nb // 2) <= 1)
        ww = w * sel
        col = (px * ww[:, None]).sum(axis=0) / max(ww.sum(), 1e-9)
        col = col / max(float(col.max()), 1e-3)
        out.append((col, float(hh[j] / total), hh[j]))
        for d in range(-3, 4):                         # the next colour must be at least ~45 degrees away
            hh[(j + d) % nb] = 0
    return [(c, sh) for c, sh, _ in out], bright


class Screen:
    """Primary screen, shrunk to w x h by GDI StretchBlt (HALFTONE), in a background thread."""

    def __init__(self, w=64, h=36, fps=25):
        self.w, self.h, self.fps = w, h, fps
        self.ok = False
        self.err = ""
        self.img = None
        self.t = 0.0
        self.on = True
        if os.name != "nt":
            self.err = "screen colours need Windows"
            return
        self.ok = True
        threading.Thread(target=self._loop, daemon=True).start()

    def get(self):
        if not self.on or self.img is None or time.time() - self.t > 1.0:
            return None
        return self.img

    def _loop(self):
        try:
            grab = self._gdi()
        except Exception as e:  # noqa
            self.ok, self.err = False, f"screen capture failed: {e}"[:80]
            return
        while True:
            t0 = time.time()
            if self.on:
                try:
                    self.img = grab()
                    self.t = time.time()
                except Exception as e:  # noqa
                    self.err = f"grab: {e}"[:80]
                    time.sleep(1.0)
            time.sleep(max(0.0, 1.0 / self.fps - (time.time() - t0)))

    def _gdi(self):
        import ctypes
        from ctypes import wintypes
        u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
        try:
            u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))     # real pixels, not scaled ones
        except Exception:
            try:
                u32.SetProcessDPIAware()
            except Exception:
                pass
        u32.GetDC.restype = wintypes.HDC
        u32.GetDC.argtypes = [wintypes.HWND]
        g32.CreateCompatibleDC.restype = wintypes.HDC
        g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        g32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        g32.SelectObject.restype = wintypes.HGDIOBJ
        g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
        g32.SetBrushOrgEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        g32.StretchBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        g32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                  ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]
        w, h = self.w, self.h
        scr = u32.GetDC(None)
        mem = g32.CreateCompatibleDC(scr)
        bmp = g32.CreateCompatibleBitmap(scr, w, h)
        g32.SelectObject(mem, bmp)
        g32.SetStretchBltMode(mem, 4)                  # HALFTONE: proper averaging, not pixel skipping
        g32.SetBrushOrgEx(mem, 0, 0, None)
        bih = BIH()
        bih.biSize = ctypes.sizeof(BIH)
        bih.biWidth, bih.biHeight = w, -h              # top-down
        bih.biPlanes, bih.biBitCount, bih.biCompression = 1, 32, 0
        buf = (ctypes.c_ubyte * (w * h * 4))()

        def grab():
            sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
            g32.StretchBlt(mem, 0, 0, w, h, scr, 0, 0, sw, sh, 0x00CC0020)   # SRCCOPY
            g32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bih), 0)
            a = np.frombuffer(buf, np.uint8).reshape(h, w, 4)
            return a[:, :, 2::-1].astype(np.float32) / 255.0
        return grab


def crop_bars(img, thr=0.035):
    """cut off black letterbox / pillarbox bars"""
    lum = img.max(axis=2)
    rows = np.where(lum.mean(axis=1) > thr)[0]
    cols = np.where(lum.mean(axis=0) > thr)[0]
    if len(rows) < 3 or len(cols) < 3:
        return img
    return img[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def de_white(c, sat=1.4):
    """more colour, less white: pull the grey part down and push the colour part up, keep the brightness"""
    mx = c.max(axis=-1, keepdims=True)
    mn = c.min(axis=-1, keepdims=True)
    out = mn * 0.35 + (c - mn) * sat
    m2 = out.max(axis=-1, keepdims=True)
    return np.where(m2 > 1e-4, out * (mx / np.maximum(m2, 1e-4)), out)


def hsv_step(cur, tgt, k):
    """move colour `cur` toward `tgt` by fraction k, along the shorter way round the hue circle"""
    import colorsys
    h0, s0, v0 = colorsys.rgb_to_hsv(*np.clip(cur, 0, 1))
    h1, s1, v1 = colorsys.rgb_to_hsv(*np.clip(tgt, 0, 1))
    if s1 < 0.05:
        h1 = h0
    if s0 < 0.05:
        h0 = h1
    dh = ((h1 - h0 + 0.5) % 1.0) - 0.5
    return np.array(colorsys.hsv_to_rgb((h0 + dh * k) % 1.0, s0 + (s1 - s0) * k, v0 + (v1 - v0) * k))


class Cinema:
    def __init__(self):
        self.intensity = "normal"
        self.layout = "room"           # or "screen" (strip around the screen)
        self.flip = False
        self.cur = None
        self.prev_target = None
        self.prev_small = None
        self.flash = 0.0
        self.flash_col = None
        self.last_hit = -9.0
        self.ripple_t = -9.0
        self.level = 0.6
        self.calm = 0.0
        self.status = ""
        self.last_screen = None
        self.black_for = 0.0           # how long the screen has been black while sound plays
        self.protected = False
        self.waves = []                # beat waves (start time, strength)
        self.sa = None                 # room layout: main colour and second colour (smoothed)
        self.sb = None
        # the sound, smoothed over a few seconds so the behaviour doesn't flip back and forth
        self.sp_s = self.mu_s = 0.0
        self.dialogue = False
        self.music_on = False
        self.mu_since = None

    def _screen_target(self, R, img):
        """strip around the screen: each part takes the colour of the screen edge next to it"""
        img = crop_bars(img)
        lum = img.max(axis=2)
        wgt = lum ** 2 + 0.02                        # the lit things in the picture, not the dark background
        colm = (img * wgt[:, :, None]).sum(axis=0) / wgt.sum(axis=0)[:, None]      # (w, 3)
        glob = (img * wgt[:, :, None]).sum(axis=(0, 1)) / wgt.sum()
        xs = np.linspace(0, 1, colm.shape[0])
        xr = 1 - R.x if self.flip else R.x
        tgt = np.stack([np.interp(xr, xs, colm[:, k]) for k in range(3)], axis=1)
        tgt = 0.7 * tgt + 0.3 * glob
        bright = float(np.sqrt(np.clip(lum.mean() * 1.6, 0, 1)))
        return tgt, bright

    def _room_target(self, R, img, D, t, dt, P):
        """strip in the room: the scene's main colour in the middle, the second towards the ends, slowly"""
        img = crop_bars(img)
        small = img[::4, ::4]
        cut = self.prev_small is not None and self.prev_small.shape == small.shape and \
            float(np.abs(small - self.prev_small).mean()) > 0.2
        self.prev_small = small
        cols, bright = dominant(img)
        tint = D.palette.at(0.5 + t * 0.004) * 0.35
        if not cols:
            a = b = tint                                   # grey / white / dark scene: a dim tint, never white
        else:
            a = cols[0][0]
            b = cols[1][0] if len(cols) > 1 and cols[1][1] > 0.12 else a * 0.55
        if self.sa is None:
            self.sa, self.sb = np.array(a, float), np.array(b, float)
        # keep each colour in its place: if the two swapped ranks, don't swap them on the strip
        if np.abs(a - self.sb).sum() + np.abs(b - self.sa).sum() < np.abs(a - self.sa).sum() + np.abs(b - self.sb).sum():
            a, b = b, a
        tau = P["room"] / (3 if cut else 1)
        k = 1 - math.exp(-dt / tau)
        self.sa = hsv_step(self.sa, a, k)              # around the colour wheel, never through grey
        self.sb = hsv_step(self.sb, b, k)
        d = 1 - R.dc if self.flip else R.dc
        # two zones with a short soft border (a long blend between opposite colours would turn grey)
        m = np.clip((d - 0.42 + 0.06 * math.sin(2 * math.pi * t * 0.03)) / 0.18, 0, 1)
        m = m * m * (3 - 2 * m)
        tgt = self.sa * (1 - m)[:, None] + self.sb * m[:, None]
        want = self.sa.max() * (1 - m) + self.sb.max() * m        # keep the brightness through the border
        tgt = tgt * (want / np.maximum(tgt.max(axis=1), 1e-3))[:, None]
        return tgt, float(np.sqrt(np.clip(bright * 1.6, 0, 1)))

    def _sound(self, scene, A, t, dt):
        k = 1 - math.exp(-dt / 3.0)
        self.sp_s += (scene.get("speech", 0.0) - self.sp_s) * k
        self.mu_s += (scene.get("music", 0.0) - self.mu_s) * k
        if not self.dialogue and self.sp_s > 0.35 and self.mu_s < 0.3:
            self.dialogue = True
        elif self.dialogue and (self.sp_s < 0.2 or self.mu_s > 0.45):
            self.dialogue = False
        if self.mu_s > 0.45:
            self.mu_since = self.mu_since if self.mu_since is not None else t
        else:
            self.mu_since = None
        if not self.music_on and self.mu_since is not None and t - self.mu_since > 3.0:
            self.music_on = True
        elif self.music_on and self.mu_s < 0.3:
            self.music_on = False

    def render(self, R, A, D, t, dt):
        P = INTENSITY.get(self.intensity, INTENSITY["normal"])
        scene = getattr(D, "scene", None) or {}
        self._sound(scene, A, t, dt)
        scr = getattr(D, "screen_img", None)
        if scr is not None:
            # a black screen while the film is clearly playing = the picture is protected (DRM)
            black = float(scr.max()) < 0.04
            if black and not A.silence and A.energy > 0.2:
                self.black_for += dt
            elif not black:
                self.black_for, self.protected = 0.0, False
            if self.black_for > 1.5:
                self.protected = True
        room = self.layout != "screen"
        if scr is not None and not self.protected:
            tgt, bright = self._room_target(R, scr, D, t, dt, P) if room else self._screen_target(R, scr)
            self.last_screen = (tgt, bright)
            src = "screen"
        elif self.last_screen is not None and getattr(D, "screen_on", False) and not self.protected:
            tgt, bright = self.last_screen
            src = "last screen colours"
        else:
            # mood colours that follow the sound
            m = 0.5 + 0.5 * np.sin(2 * math.pi * (R.x * 0.8 + t * 0.02))
            tgt = D.palette.at(R.x * 0.4 + t * 0.005) * (0.6 + 0.4 * m)[:, None]
            bright = 0.45 + 0.4 * A.energy
            src = "screen is protected (DRM): following the sound" if self.protected else "palette"
        tgt = np.clip(tgt, 0, 1)
        # grey / white parts of the picture (bright rooms, web pages, subtitles) never become white light:
        # they turn into a dim tint of the palette instead
        mx0 = tgt.max(axis=1, keepdims=True)
        chroma = (mx0 - tgt.min(axis=1, keepdims=True)) / np.maximum(mx0, 1e-3)
        neutral = np.clip((0.3 - chroma) / 0.2, 0, 1)
        tint = D.palette.at(R.x * 0.3 + t * 0.004) * 0.35
        tgt = de_white(tgt, P["sat"])
        tgt = tgt * (1 - neutral) + tint * neutral
        mx = tgt.max(axis=1, keepdims=True)
        tgt = np.where(mx > 1e-4, tgt / np.maximum(mx, 1e-4), tgt) * np.clip(mx * 1.3, 0, 1)    # lift dim colours
        # scene cuts: change faster, but never flash (the room layout already smooths its colours)
        tau = 0.1 if (room and src == "screen") else P["tau"]
        if self.prev_target is not None and float(np.abs(tgt - self.prev_target).mean()) > 0.2:
            tau = tau / 3
        self.prev_target = tgt
        if self.cur is None or self.cur.shape != tgt.shape:
            self.cur = tgt.copy()
        a = 1 - math.exp(-dt / tau)
        self.cur += (tgt - self.cur) * a
        # the sound
        self.calm += ((1.0 if self.dialogue else 0.0) - self.calm) * (1 - math.exp(-dt / 1.5))
        lvl = 0.3 + 0.7 * bright
        lvl *= 1.0 - 0.12 * self.calm                              # dialogue: steady and a little lower
        if self.mu_s > 0.35:
            lvl *= 0.92 + 0.25 * A.energy                          # the score swells the room
        if A.energy < 0.15 and not A.silence:
            lvl *= 0.75                                            # quiet scene
        self.level += (lvl - self.level) * (1 - math.exp(-dt / 1.2))
        img = self.cur * self.level
        # music in the film: waves on the beat (only with a clear, lasting beat, never over dialogue)
        beat_ok = self.music_on and not self.dialogue and A.locked and A.tconf > 0.3
        if beat_ok and A.beat_tick:
            amp = (0.2 + 0.4 * P["accent"]) * (1.0 if A.downbeat else 0.5) * min(1.0, self.mu_s * 1.5)
            self.waves.append((t, amp))
            self.waves = self.waves[-4:]
        if self.waves:
            per = max(0.3, A.period)
            boost = np.zeros(R.n)
            keep = []
            for t0, amp in self.waves:
                age = (t - t0) / per
                if age < 1.0:
                    keep.append((t0, amp))
                    boost = np.maximum(boost, np.exp(-0.5 * ((R.dc - age) / 0.12) ** 2) * amp * (1 - age))
            self.waves = keep
            img = img * (1 + boost[:, None]) + self.cur * (boost * 0.25)[:, None]
        # impacts: an explosion / gunshot / slam = one accent in the scene's own colour
        fx = scene.get("fx", 0.0)
        hit = A.impact and A.impact_strength > 0.6 and (fx > 0.05 or A.impact_strength > 0.85) and not self.music_on
        if hit and t - self.last_hit > 0.6:
            self.last_hit = t
            self.flash = P["accent"] * min(1.0, 0.5 + 0.6 * A.impact_strength)
            c = self.cur.mean(axis=0)
            c = c / max(float(c.max()), 1e-3)
            self.flash_col = de_white(c[None, :], 1.6)[0]
            self.ripple_t = t
        if self.flash > 0.01 and self.flash_col is not None:
            img = img + (self.flash_col - img) * self.flash
            age = t - self.ripple_t
            if age < 0.7:
                ring = np.exp(-0.5 * ((R.dc - age / 0.7) / 0.06) ** 2) * (1 - age / 0.7) * self.flash
                img = np.maximum(img, ring[:, None] * self.flash_col)
            self.flash *= math.exp(-dt / 0.22)
        what = "dialogue" if self.dialogue else ("music on the beat" if beat_ok else ("score" if self.mu_s > 0.35 else ""))
        self.status = f"{src}{' (' + ('room' if room else 'around the screen') + ')' if src == 'screen' else ''}" \
                      f"{' · ' + what if what else ''}"
        return np.clip(img, 0, 1)
