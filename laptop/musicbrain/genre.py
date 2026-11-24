"""Genre + mood + vocals from YAMNet (Google's AudioSet network, 521 sound classes), via onnxruntime.

Runs in the background once a second on the last 2 s of audio (16 kHz). Its job is the slow, big-picture
stuff a lighting designer decides before the song even gets going: what kind of music this is
(-> colour family and how punchy the show may be), the mood, whether someone is singing, and a tempo hint.
"""
import os
import threading
import time
import numpy as np

# genre class -> family
FAMILY_OF = {
    221: "rnb", 222: "rnb", 254: "rnb", 230: "rnb", 246: "rnb", 249: "rnb",
    212: "hiphop", 213: "hiphop",
    240: "edm", 235: "edm", 236: "edm", 237: "edm", 238: "edm", 242: "edm", 234: "edm", 239: "edm", 269: "edm",
    211: "pop", 243: "pop", 244: "pop", 252: "pop", 251: "pop", 227: "pop", 231: "pop", 223: "pop", 257: "pop",
    260: "pop", 258: "pop",
    214: "rock", 215: "rock", 216: "rock", 217: "rock", 218: "rock", 219: "rock", 220: "rock",
    241: "chill", 248: "chill", 232: "chill", 228: "chill", 266: "chill", 224: "chill", 265: "chill",
}
NAMES = {221: "R&B", 222: "Soul", 254: "Gospel", 230: "Jazz", 246: "Blues", 249: "Vocal", 212: "Hip hop",
         213: "Beatbox", 240: "EDM", 235: "House", 236: "Techno", 237: "Dubstep", 238: "DnB", 242: "Trance",
         234: "Electronic", 239: "Electronica", 269: "Dance", 211: "Pop", 243: "Latin", 244: "Salsa",
         252: "Afrobeat", 251: "African", 227: "Funk", 231: "Disco", 223: "Reggae", 257: "Bollywood",
         260: "Indie", 258: "Ska", 214: "Rock", 215: "Metal", 216: "Punk", 217: "Grunge", 218: "Prog rock",
         219: "Rock & roll", 220: "Psych rock", 241: "Ambient", 248: "New-age", 232: "Classical", 228: "Folk",
         266: "Lullaby", 224: "Country", 265: "Soundtrack"}
# mood classes: (arousal, valence)
MOODS = {271: ("happy", 0.4, 0.9), 272: ("sad", -0.6, -0.9), 273: ("tender", -0.6, 0.4),
         274: ("exciting", 0.9, 0.3), 275: ("angry", 0.8, -0.8), 276: ("scary", 0.2, -0.9)}
# typical tempo per family, as a beat tracker would count it
TEMPO_OF = {"rnb": 85.0, "hiphop": 90.0, "edm": 126.0, "pop": 110.0, "rock": 125.0, "chill": 90.0}
SINGING, MUSIC, SPEECH = 24, 132, 0


class GenreNet:
    def __init__(self, sr, model_path, background=True):
        self.sr = sr
        self.ok = False
        self.err = ""
        self.ring = np.zeros(32000, np.float32)
        self.lock = threading.Lock()
        self.avg = np.zeros(521)
        self.family = None
        self.family_conf = 0.0
        self.fam_scores = {}
        self.label = "listening…"
        self.mood = None
        self.arousal = 0.0
        self.valence = 0.0
        self.singing = 0.0
        self.tempo_prior = None
        self._acc = np.zeros(0, np.float32)
        self.runs = 0
        try:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.intra_op_num_threads = 1
            so.log_severity_level = 3
            self.sess = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])
            self.inp = self.sess.get_inputs()[0].name
            self.ok = True
        except Exception as e:  # noqa
            self.err = str(e)
            self.label = "genre net off"
            return
        # resampler to 16 kHz: low-pass (moving average) + linear interpolation
        self.ratio = 16000.0 / sr
        if background:
            threading.Thread(target=self._loop, daemon=True).start()

    def reset(self):
        """new song: forget the old genre quickly"""
        self.avg *= 0.2
        self.family_conf *= 0.2

    def push(self, mono):
        if not self.ok:
            return
        x = mono.astype(np.float32)
        k = max(1, int(round(self.sr / 16000.0)))
        if k > 1:
            x = np.convolve(x, np.ones(k) / k, mode="same")
        n = int(len(x) * self.ratio)
        if n <= 0:
            return
        xi = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
        with self.lock:
            self.ring = np.concatenate([self.ring[len(xi):], xi])

    def _loop(self):
        while True:
            time.sleep(1.0)
            self.step()

    def step(self):
        if True:
            try:
                with self.lock:
                    x = self.ring.copy()
                if float(np.mean(x * x)) < 1e-7:
                    self.label = "silence"
                    return
                pk = float(np.max(np.abs(x)))
                if pk > 0:
                    x = x / max(pk, 0.1) * 0.5          # level-independent
                sc = self.sess.run(None, {self.inp: x})[0].mean(axis=0)
                self.avg += (sc - self.avg) * 0.25
                self._interpret()
            except Exception as e:  # noqa
                self.label = "net error: " + str(e)[:60]

    def _interpret(self):
        a = self.avg
        fam = {}
        for idx, f in FAMILY_OF.items():
            fam[f] = fam.get(f, 0.0) + float(a[idx])
        tot = sum(fam.values())
        self.fam_scores = {k: round(v, 3) for k, v in fam.items()}
        best = max(fam, key=fam.get)
        conf = min(1.0, tot / 0.10)
        # hysteresis: switch family only when the new one clearly leads
        if self.family is None or (best != self.family and fam[best] > 1.4 * fam.get(self.family, 0) and conf > 0.3):
            self.family = best
        self.family_conf = conf
        g = sorted(NAMES, key=lambda i: -a[i])[:2]
        mi = max(MOODS, key=lambda i: a[i])
        self.mood = MOODS[mi][0] if a[mi] > 0.01 else None
        w = sum(a[i] for i in MOODS) + 1e-9
        self.arousal = float(sum(a[i] * MOODS[i][1] for i in MOODS) / w)
        self.valence = float(sum(a[i] * MOODS[i][2] for i in MOODS) / w)
        self.singing = float(np.clip(a[SINGING] / 0.25, 0, 1))
        self.label = " / ".join(NAMES[i] for i in g if a[i] > 0.005) or "music"
        if self.mood:
            self.label += " · " + self.mood
        self.tempo_prior = TEMPO_OF.get(self.family) if conf > 0.3 else None
        self.runs += 1

    def state(self):
        return {"family": self.family, "fconf": round(self.family_conf, 2), "glabel": self.label,
                "sing": round(self.singing, 2), "fam": self.fam_scores, "net": self.ok}
