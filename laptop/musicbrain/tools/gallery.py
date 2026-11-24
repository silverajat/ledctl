"""Draw every look for a few bars of a song (forced), one under the other, to check what they look like.

  python tools/gallery.py song.wav --start 60 --dur 8 --out gallery.png
"""
import argparse, os, sys
import numpy as np
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from analysis import Analyzer  # noqa: E402
from show import Director, LOOKS  # noqa: E402
import audio_in  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("wav"); ap.add_argument("--start", type=float, default=60); ap.add_argument("--dur", type=float, default=8)
ap.add_argument("--family", default="edm"); ap.add_argument("--out", default="gallery.png"); ap.add_argument("--tier", type=int, default=2)
a = ap.parse_args()
src = audio_in.WavSource(512, a.wav, realtime=False)
A = Analyzer(src.sr, 512)
names = [n for n in LOOKS if n != "idle"]
Ds = {}
for n in names:
    D = Director(115); D.family = a.family; D.lock = n; D.tier = a.tier; Ds[n] = D
rows = {n: [] for n in names}
while True:
    b = src.read()
    if b is None or A.t > a.start + a.dur: break
    A.process(b)
    for n, D in Ds.items():
        D.tier = a.tier
        fr = D.render(A, A.t, A.dt)
        if A.t >= a.start and A.n % 3 == 0: rows[n].append(fr)
from PIL import Image, ImageDraw
tiles = []
for n in names:
    im = Image.fromarray(np.repeat(np.array(rows[n]).transpose(1, 0, 2), 2, axis=0))
    im = im.resize((im.size[0] * 3, im.size[1]))
    d = ImageDraw.Draw(im); d.rectangle((0, 0, 70, 12), fill=(0, 0, 0)); d.text((2, 0), n, fill=(255, 255, 255))
    tiles.append(im)
W = max(t.size[0] for t in tiles); H = sum(t.size[1] + 4 for t in tiles)
o = Image.new("RGB", (W, H), (50, 50, 50)); y = 0
for t in tiles: o.paste(t, (0, y)); y += t.size[1] + 4
o.save(a.out); print(a.out, o.size)
