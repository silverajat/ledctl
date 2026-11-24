"""Audio sources: what the laptop is playing (WASAPI loopback), or a .wav file."""
import queue
import threading
import time
import wave
import numpy as np


class Source:
    sr = 48000
    name = "?"

    def __init__(self, hop):
        self.hop = hop
        self.q = queue.Queue(maxsize=200)
        self._acc = np.zeros((0, 2), np.float32)

    def _push(self, st):
        """st: (n, 2) stereo float32"""
        if st.ndim == 1:
            st = np.stack([st, st], axis=1)
        self._acc = np.concatenate([self._acc, st.astype(np.float32)]) if len(self._acc) else st.astype(np.float32)
        while len(self._acc) >= self.hop:
            blk, self._acc = self._acc[:self.hop], self._acc[self.hop:]
            try:
                self.q.put_nowait(blk)
            except queue.Full:
                pass

    def read(self, timeout=0.03):
        """next hop of audio; zeros if nothing arrived (WASAPI sends nothing during silence)"""
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        pass


class LoopbackPyAudio(Source):
    """pyaudiowpatch: PyAudio fork with WASAPI loopback (records the default speakers)."""

    def __init__(self, hop):
        super().__init__(hop)
        import pyaudiowpatch as pyaudio
        self.pa = pyaudio.PyAudio()
        dev = self.pa.get_default_wasapi_loopback()
        self.name = dev["name"]
        self.sr = int(dev["defaultSampleRate"])
        self.ch = int(dev["maxInputChannels"])

        def cb(data, frames, ti, status):
            a = np.frombuffer(data, np.float32).reshape(-1, self.ch)
            self._push(a[:, :2] if self.ch >= 2 else a[:, 0])
            return (None, pyaudio.paContinue)

        self.stream = self.pa.open(format=pyaudio.paFloat32, channels=self.ch, rate=self.sr,
                                   input=True, input_device_index=dev["index"],
                                   frames_per_buffer=hop, stream_callback=cb)
        self.stream.start_stream()

    def close(self):
        try:
            self.stream.stop_stream(); self.stream.close(); self.pa.terminate()
        except Exception:
            pass


class LoopbackSoundcard(Source):
    """fallback: the `soundcard` package, loopback of the default speaker."""

    def __init__(self, hop):
        super().__init__(hop)
        import soundcard as sc
        spk = sc.default_speaker()
        self.name = spk.name
        mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
        self.sr = 48000
        self.alive = True

        def run():
            with mic.recorder(samplerate=self.sr, blocksize=hop) as rec:
                while self.alive:
                    d = rec.record(numframes=hop)
                    self._push(d[:, :2] if d.shape[1] >= 2 else d[:, 0])
        threading.Thread(target=run, daemon=True).start()

    def close(self):
        self.alive = False


class WavSource(Source):
    """plays a wav file through the analyser (in real time, or as fast as possible)."""

    def __init__(self, hop, path, realtime=True):
        super().__init__(hop)
        w = wave.open(path, "rb")
        self.sr = w.getframerate()
        ch, sw = w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
        dt = {1: np.int8, 2: np.int16, 4: np.int32}[sw]
        a = np.frombuffer(raw, dt).astype(np.float32) / float(2 ** (8 * sw - 1))
        a = a.reshape(-1, ch)
        self.data = a[:, :2] if ch >= 2 else np.stack([a[:, 0], a[:, 0]], axis=1)
        self.name = path
        self.pos = 0
        self.realtime = realtime
        self.t0 = time.time()
        self.done = False

    def read(self, timeout=0.03):
        if self.pos + self.hop > len(self.data):
            self.done = True
            return None
        if self.realtime:
            due = self.t0 + self.pos / self.sr
            d = due - time.time()
            if d > 0:
                time.sleep(d)
        blk = self.data[self.pos:self.pos + self.hop]
        self.pos += self.hop
        return blk


def open_loopback(hop):
    errs = []
    for cls in (LoopbackPyAudio, LoopbackSoundcard):
        try:
            return cls(hop)
        except Exception as e:  # noqa
            errs.append(f"{cls.__name__}: {e}")
    raise RuntimeError("no loopback capture available:\n  " + "\n  ".join(errs))
