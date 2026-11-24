"""Render the light show for a wav file as a picture (time left to right, LEDs top to bottom),
as fast as possible, and print what the director did.

  python tools/render.py song.wav [--start 40 --dur 30] [--plan] [--leds 115] [--out show.png]

--plan uses the song's .truth.json as a "known song" (the pre-planned show), aligned by audio like live.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from analysis import Analyzer  # noqa: E402
from show import Director  # noqa: E402
from genre import GenreNet  # noqa: E402
import audio_in  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=0.0)
    ap.add_argument("--leds", type=int, default=115)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--fps", type=float, default=30.0)
    a = ap.parse_args()

    src = audio_in.WavSource(512, a.wav, realtime=False)
    A = Analyzer(src.sr, 512)
    D = Director(a.leds)
    mp = os.path.join(HERE, "ml", "yamnet.onnx")
    G = GenreNet(src.sr, mp, background=False) if os.path.exists(mp) else None
    plan = None
    if a.plan:
        from songmap import SongMap
        plan = SongMap(os.path.dirname(os.path.abspath(a.wav)), src.sr, 512)
        plan.set_song(os.path.basename(a.wav)[:-4], now_t=0.0, sync=True)
    rows, log = [], []
    every = max(1, int(round(A.fps / a.fps)))
    last = (None, None)
    ms = []
    while True:
        b = src.read()
        if b is None:
            break
        t0 = time.perf_counter()
        A.process(b)
        V = A
        if plan:
            plan.feed(A)
            if A.n % int(A.fps * 2) == 0:
                plan.match_now()
            V = plan.view(A)
        if G and G.ok:
            if not A.silence:
                G.push(b.mean(axis=1))
            if A.n % int(A.fps) == 0:
                G.step()
                A.tempo_prior, A.singing, A.singing_known = G.tempo_prior, G.singing, G.runs > 2
                D.set_genre(G.family, G.family_conf, G.valence, dict(G.fam_scores, _top=G.top_family), V)
        fr = D.render(V, A.t, A.dt)
        ms.append((time.perf_counter() - t0) * 1000)
        if A.t < a.start or (a.dur and A.t > a.start + a.dur):
            continue
        if A.n % every == 0:
            rows.append(fr)
        key = (D.look, D.palette.name)
        if key != last or V.drop or V.section_change or D.events:
            evs = ",".join(f"{k}:{v}" for k, v in D.events if k == "accent")
            log.append(f"{A.t:6.1f}s {D.look:9s} {D.palette.name:8s} root={D.root} tier={D.tier} int={A.intensity:.2f} "
                       f"bpm={V.bpm:.0f} perc={A.perc:.2f} tonal={A.tonal:.2f} riser={A.riser:.2f} build={A.build:.2f}"
                       f"{' DROP' if V.drop else ''}{' SECTION' if V.section_change else ''} {evs}"
                       f"{' fam=' + str(D.family) if D.family else ''}{' PLAN' if getattr(V, 'planned', False) else ''}")
            last = key
        D.events.clear()
    print("\n".join(log[-400:]))
    print(f"render: {np.mean(ms):.2f} ms/hop mean, {np.percentile(ms, 99):.2f} ms p99 (hop = {A.dt * 1000:.1f} ms)")
    if plan:
        print("plan:", plan.state())
    img = np.array(rows).transpose(1, 0, 2)          # LEDs x time x 3
    img = np.repeat(img, 3, axis=0)
    out = a.out or os.path.splitext(a.wav)[0] + "_show.png"
    from PIL import Image
    Image.fromarray(img).save(out)
    print("image", out, img.shape)


if __name__ == "__main__":
    main()
