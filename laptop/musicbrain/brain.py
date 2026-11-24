"""ledctl music brain: listens to what the laptop plays and runs a light show
on the board's addressable strip, with a virtual copy at http://localhost:8766.

  python brain.py                 # live: whatever Spotify / the laptop is playing
  python brain.py --wav song.wav  # test with a file
  python brain.py --leds 115     # strip length (default 115)
"""
import argparse
import copy
import random
import re
import subprocess
import wave
import base64
import collections
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from analysis import Analyzer
from show import Director, LOOKS
from genre import GenreNet
from board import Board
from prefs import Prefs, KNOBS
from show import PALETTES
import audio_in

HERE = os.path.dirname(os.path.abspath(__file__))
SESS = os.path.join(HERE, "sessions")
RECS = os.path.join(HERE, "recordings")

# A/B: which looks to try instead of the current one
LOOK_ALTS = {"groove": ["chase", "bassline", "swell", "punch"], "chase": ["groove", "peak", "punch", "swell"],
             "peak": ["punch", "swell", "chase"], "swell": ["peak", "punch", "groove"], "punch": ["peak", "swell", "chase"],
             "ambient": ["breakdown", "bassline", "swell"], "breakdown": ["ambient", "swell", "bassline"],
             "bassline": ["groove", "ambient", "swell"], "build": ["chase", "groove"]}
KNOB_TRIES = {"exposure": (1.4, 0.7), "white": (2.5, 0.4), "vocal": (1.8, 0.5), "pulse": (1.35, 0.6), "release": (3.0, 0.4)}
KNOB_NAMES = {"exposure": "brightness", "white": "how much white", "vocal": "voice light", "pulse": "kick pulses",
              "release": "smoothness"}


class Recorder:
    """Saves each Spotify song you play (audio only, on this laptop) so the brain can be scored and
    tuned against reference answers worked out from the whole song."""

    def __init__(self, sr):
        self.sr = sr
        self.w = None
        self.path = None
        self.title = None
        self.frames = 0
        os.makedirs(RECS, exist_ok=True)

    @staticmethod
    def fname(title):
        return re.sub(r'[<>:"/\\|?*]', "_", title).strip()[:120]

    def feed(self, title, blk, silent_for):
        playing = title and " - " in title and not title.startswith("(")
        if self.w and (title != self.title or silent_for > 4.0):
            self.close()
        if not playing or silent_for > 0.5:
            return
        if self.w is None:
            p = os.path.join(RECS, self.fname(title) + ".wav")
            if os.path.exists(p):           # already have this song
                self.title = title
                return
            self.w = wave.open(p + ".part", "wb")
            self.w.setnchannels(2); self.w.setsampwidth(2); self.w.setframerate(self.sr)
            self.path, self.title, self.frames = p, title, 0
        self.w.writeframes((np.clip(blk, -1, 1) * 32767).astype(np.int16).tobytes())
        self.frames += len(blk)

    def close(self):
        if not self.w:
            return
        self.w.close()
        part = self.path + ".part"
        secs = self.frames / self.sr
        self.w = None
        if secs < 45:                      # skipped early: not worth keeping
            try: os.remove(part)
            except OSError: pass
            return
        os.replace(part, self.path)
        print(f"recorded {os.path.basename(self.path)} ({secs:.0f} s), working out the reference answers...")
        try:                               # runs in the background, needs torch + beat_this
            subprocess.Popen([sys.executable, os.path.join(HERE, "truth.py"), self.path], cwd=HERE,
                             stdout=open(self.path[:-4] + ".truth.log", "w"), stderr=subprocess.STDOUT)
        except Exception as e:  # noqa
            print("could not start truth.py:", e)
HOP = 512


def _np(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o).__name__)


def jdump(o):
    return json.dumps(o, default=_np)


# ---------------------------------------------------------------- now playing (Windows)
def now_playing():
    """Spotify desktop shows 'Artist - Song' as its window title while playing."""
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    titles = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lp):
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(0x1000, False, pid.value)
        if not h:
            return True
        buf = ctypes.create_unicode_buffer(260)
        sz = wintypes.DWORD(260)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz))
        k32.CloseHandle(h)
        if ok and buf.value.lower().endswith("spotify.exe"):
            t = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, t, n + 1)
            titles.append(t.value)
        return True
    try:
        u32.EnumWindows(cb, 0)
    except Exception:
        return ""
    for t in titles:
        if " - " in t:
            return t
    return "(Spotify paused)" if titles else ""


# ---------------------------------------------------------------- engine
class Engine:
    def __init__(self, src, leds, board="auto"):
        self.src = src
        self.board = Board(board)
        self.A = Analyzer(src.sr, hop=HOP)
        self.D = Director(leds)
        model = os.path.join(HERE, "ml", "yamnet.onnx")
        model = model if os.path.exists(model) else None
        self.G = GenreNet(src.sr, model) if model else None
        print("genre net:", "on" if self.G and self.G.ok else f"off ({self.G.err if self.G else 'yamnet.onnx not found'})")
        self.last_song = None
        self.prefs = Prefs(os.path.join(HERE, "prefs.json"))
        self.D.prefs = self.prefs
        self.recorder = None if isinstance(src, audio_in.WavSource) else Recorder(src.sr)
        self.ab = False
        self.D2 = None
        self.q = None
        self.q_t = 0.0
        self.frame2 = None
        self.frame = np.zeros((leds, 3), np.uint8)
        self.lock = threading.Lock()
        self.song = ""
        self.hist = collections.deque(maxlen=200)       # ~10 s of states at 20 Hz
        self.events = collections.deque(maxlen=50)
        self.timeline = collections.deque(maxlen=600)  # last ~10 s at ~60 Hz for the page graph
        self.running = True
        self.t = 0.0
        self.cpu = 0.0
        os.makedirs(SESS, exist_ok=True)
        self.rec = open(os.path.join(SESS, time.strftime("session_%Y%m%d_%H%M%S.jsonl")), "a")
        self.last_rec = 0.0
        self.song_pos0 = time.time()

    def run(self):
        import traceback
        while self.running:
            try:
                self._run()
                return
            except Exception:
                traceback.print_exc()
                time.sleep(0.5)

    def _run(self):
        silence = np.zeros((HOP, 2), np.float32)
        while self.running:
            blk = self.src.read()
            if blk is None:
                if getattr(self.src, "done", False):
                    break
                blk = silence
            t0 = time.perf_counter()
            A = self.A
            A.process(blk)
            G = self.G
            if G is not None and G.ok:
                if not A.silence:
                    G.push(blk.mean(axis=1))
                if A.n % 25 == 0:
                    new_song = (self.song != self.last_song and self.last_song is not None) or A.silent_for > 3.0
                    if new_song and G.runs:
                        G.reset()
                        self.D.family = None
                    self.last_song = self.song
                    A.tempo_prior = G.tempo_prior
                    A.singing = G.singing
                    A.singing_known = G.runs > 2
                    self.D.set_genre(G.family, G.family_conf, G.valence)
            if self.recorder is not None:
                self.recorder.feed(self.song, blk, A.silent_for)
            fr = self.D.render(A, A.t, A.dt)
            fr2 = None
            if self.ab:
                if self.D2 is None or A.t - self.q_t > 45 or self._question_moot():
                    self.new_question()
                if self.D2 is not None:
                    self.D2.family = self.D.family
                    fr2 = self.D2.render(A, A.t, A.dt)
                    self.D2.events.clear()
            if self.board.mode != "off":
                self.board.push(fr)
            with self.lock:
                self.frame = fr
                self.frame2 = fr2
                self.t = A.t
                for e in self.D.events:
                    self.events.append({"t": round(A.t, 2), "k": e[0], "v": e[1]})
                self.D.events.clear()
                if A.drop:
                    self.events.append({"t": round(A.t, 2), "k": "drop", "v": ""})
                if A.section_change:
                    self.events.append({"t": round(A.t, 2), "k": "section", "v": A.section_id})
                self.timeline.append((round(A.t, 3), round(A.energy_fast, 2), round(A.intensity, 2),
                                      int(A.hit_kick), int(A.hit_snare), int(A.hit_hat),
                                      int(A.beat_tick), int(A.beat_tick and A.downbeat), round(A.build, 2)))
            if A.t - self.last_rec >= 0.05:
                self.last_rec = A.t
                st = self.snapshot_state()
                self.hist.append(st)
                if A.n % 10 == 0 or A.beat_tick:
                    self.rec.write(jdump(st) + "\n")
            self.cpu = 0.98 * self.cpu + 0.02 * (time.perf_counter() - t0) / A.dt

    # ---------------- A/B taste learning
    def _question_moot(self):
        q = self.q
        if not q or q["type"] != "look":
            return False
        return self.D.look != q["key"]           # the director moved on: ask something new

    def new_question(self):
        D = self.D
        if D.look == "idle":
            self.D2 = None
            self.q = None
            return
        kinds = ["palette"] * 35 + ["look"] * 25 + ["knob"] * 40
        kind = random.choice(kinds)
        D2 = copy.copy(D)
        D2.S = copy.deepcopy(D.S)
        D2.ctx, D2.prev_ctx = dict(D.ctx), dict(D.prev_ctx)
        D2.out = D.out.copy()
        D2.over, D2.look_map, D2.events = dict(D.over), dict(D.look_map), []
        fam = D.family
        if kind == "look" and D.look in LOOK_ALTS:
            alt = random.choice(LOOK_ALTS[D.look])
            D2.look_map = {D.look: alt}
            D2._set_look(alt, self.A, fade=0.2)
            q = {"type": "look", "fam": fam, "key": D.look, "a": D.look, "b": alt, "what": "the pattern"}
        elif kind == "palette":
            mine = list(D.feel["palettes"])
            pool = [p for p in PALETTES if p != D.palette.name]
            inside = [p for p in mine if p != D.palette.name]
            alt = random.choice(inside if (inside and random.random() < 0.65) else pool)
            D2.force_palette = alt
            D2._set_palette(alt)
            q = {"type": "palette", "fam": fam, "key": "palette", "a": D.palette.name, "b": alt, "what": "the colours"}
        else:
            k = random.choice(list(KNOB_TRIES))
            cur = self.prefs.knob(fam, k)
            f = random.choice(KNOB_TRIES[k])
            lo, hi = KNOBS[k]
            alt = min(hi, max(lo, cur * f))
            D2.over = dict(D.over)
            D2.over[k] = alt / cur
            q = {"type": "knob", "fam": fam, "key": k, "a": cur, "b": round(alt, 3), "what": KNOB_NAMES[k]}
        q["flip"] = random.random() < 0.5            # blind: the new option is randomly on the left or right
        self.D2, self.q, self.q_t = D2, q, self.A.t

    def vote(self, side):
        """side: 1 = left strip, 2 = right strip, 0 = neither"""
        q = self.q
        if not q:
            return "no question"
        w = 0 if side == 0 else (side if not q["flip"] else 3 - side)   # 1 = current, 2 = the alternative
        self.prefs.vote(q, w)
        rec = {"wall": time.strftime("%Y-%m-%d %H:%M:%S"), "winner": w, "q": q, "song": self.song,
               "genre": self.G.label if self.G else None, "look": self.D.look, "int": round(self.A.intensity, 2)}
        with open(os.path.join(SESS, "votes.jsonl"), "a") as f:
            f.write(jdump(rec) + "\n")
        if w == 2:
            if q["type"] == "palette":
                self.D._set_palette(q["b"])
            elif q["type"] == "look":
                self.D._set_look(q["b"], self.A)
        self.D2 = None                            # next question on the next hop
        return "saved"

    def snapshot_state(self):
        s = self.A.state()
        s.update(self.D.state())
        if self.G is not None:
            s.update(self.G.state())
        s.pop("pal_cols", None)
        s["song"] = self.song
        s["wall"] = round(time.time(), 2)
        return s

    def feedback(self, kind, note):
        with self.lock:
            rec = {"wall": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "note": note,
                   "song": self.song, "now": self.snapshot_state(), "last10s": list(self.hist)}
        with open(os.path.join(SESS, "feedback.jsonl"), "a") as f:
            f.write(jdump(rec) + "\n")
        self.rec.flush()


def make_handler(eng):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/plain"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "web", "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if self.path.startswith("/stream"):
                return self.stream()
            return self._send(404, "not found")

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            D = eng.D
            if self.path == "/api":
                if "lock" in body and (body["lock"] == "auto" or body["lock"] in LOOKS):
                    D.lock = body["lock"]
                if "leds" in body:
                    with eng.lock:
                        D.resize(int(np.clip(int(body["leds"]), 8, 300)))
                if "brightness" in body:
                    D.brightness = float(np.clip(float(body["brightness"]), 0.05, 1))
                if "budget" in body:
                    D.budget_a = float(np.clip(float(body["budget"]), 0.3, 10))
                if "lookahead" in body:
                    D.lookahead = float(np.clip(float(body["lookahead"]), -0.2, 0.3))
                if "ab" in body:
                    eng.ab = bool(body["ab"])
                    if not eng.ab:
                        eng.D2, eng.q = None, None
                if "vote" in body:
                    return self._send(200, eng.vote(int(body["vote"])))
                if body.get("newpal"):
                    D._new_palette(eng.A)
                return self._send(200, "ok")
            if self.path == "/feedback":
                eng.feedback(body.get("kind", "note"), str(body.get("note", ""))[:500])
                return self._send(200, "saved")
            return self._send(404, "not found")

        def stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last_ev = 0
            last_tl = 0.0
            try:
                while eng.running:
                    with eng.lock:
                        fr = eng.frame.tobytes()
                        fr2 = eng.frame2.tobytes() if eng.frame2 is not None else None
                        q = {k: eng.q[k] for k in ("what", "type")} if eng.q else None
                        if eng.q and eng.q["flip"] and fr2:
                            fr, fr2 = fr2, fr
                        a = eng.A.state()
                        d = eng.D.state()
                        g = eng.G.state() if eng.G is not None else {"glabel": "genre net off"}
                        evs = list(eng.events)
                        tl = [x for x in eng.timeline if x[0] > last_tl]
                    if tl:
                        last_tl = tl[-1][0]
                    new_ev = [e for e in evs if e["t"] > last_ev]
                    if new_ev:
                        last_ev = new_ev[-1]["t"]
                    msg = {"f": base64.b64encode(fr).decode(), "f2": base64.b64encode(fr2).decode() if fr2 else None,
                           "q": q, "ab": eng.ab, "board": eng.board.state(), "votes": eng.prefs.d["votes"], "a": a, "d": d, "g": g, "song": eng.song,
                           "ev": new_ev, "tl": tl, "cpu": round(eng.cpu * 100, 1),
                           "src": eng.src.name, "sr": eng.src.sr}
                    self.wfile.write(("data: " + jdump(msg) + "\n\n").encode())
                    self.wfile.flush()
                    time.sleep(1 / 60)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav")
    ap.add_argument("--leds", type=int, default=115)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--board", default="auto", choices=["auto", "usb", "ble", "off"],
                    help="send the show to the real strip (auto: USB if plugged in, else Bluetooth)")
    args = ap.parse_args()

    src = audio_in.WavSource(HOP, args.wav) if args.wav else audio_in.open_loopback(HOP)
    print(f"audio: {src.name} @ {src.sr} Hz")
    eng = Engine(src, args.leds, "off" if args.wav else args.board)
    threading.Thread(target=eng.run, daemon=True).start()

    def song_poll():
        while eng.running:
            try:
                s = now_playing()
                if s != eng.song:
                    eng.song = s
                    print("now playing:", s)
            except Exception:
                pass
            time.sleep(1.0)
    threading.Thread(target=song_poll, daemon=True).start()

    class Server(ThreadingHTTPServer):
        allow_reuse_address = False            # on Windows, reuse lets a second copy share the port silently
    try:
        srv = Server(("127.0.0.1", args.port), make_handler(eng))
    except OSError:
        print(f"Port {args.port} is busy: another copy of the music brain is already running. Close it first.")
        eng.running = False
        src.close()
        return
    srv.daemon_threads = True
    url = f"http://localhost:{args.port}"
    print("test bench:", url)
    if not args.no_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        eng.running = False
        src.close()


if __name__ == "__main__":
    main()
