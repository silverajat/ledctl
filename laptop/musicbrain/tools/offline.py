"""Run the brain over a wav as fast as possible; report tempo/kick accuracy, looks, and draw the show."""
import json, sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis import Analyzer
from show import Director
from genre import GenreNet
import audio_in
from PIL import Image

path = sys.argv[1]
leds = 60
src = audio_in.WavSource(512, path, realtime=False)
A = Analyzer(src.sr, 512); D = Director(leds)
mp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ml', 'yamnet.onnx')
G = GenreNet(src.sr, mp, background=False) if os.path.exists(mp) else None
truth = json.load(open(path.replace('.wav', '.json'))) if os.path.exists(path.replace('.wav', '.json')) else None
rows, kicks, beats, log = [], [], [], []
last_look = None; last_pal = None
while True:
    b = src.read()
    if b is None: break
    A.process(b)
    if G and G.ok:
        G.push(b.mean(axis=1))
        if A.n % int(A.fps) == 0:
            G.step(); A.tempo_prior = G.tempo_prior; A.singing = G.singing; A.singing_known = G.runs > 2
            D.set_genre(G.family, G.family_conf, G.valence)
            if A.n % int(A.fps * 10) == 0: log.append(f"   net: {G.label} fam={G.family} conf={G.family_conf:.2f} sing={G.singing:.2f} {G.fam_scores}")
    fr = D.render(A, A.t, A.dt)
    if A.n % 2 == 0: rows.append(fr)
    if A.hit_kick: kicks.append(A.t)
    if A.beat_tick: beats.append((A.t, A.bar_beat, A.locked))
    if D.look != last_look or D.palette.name != last_pal or A.drop or A.section_change:
        log.append(f"{A.t:6.2f}s look={D.look:9s} pal={D.palette.name:8s} tier={D.tier} int={A.intensity:.2f} "
                   f"bpm={A.bpm:.1f} conf={A.tconf:.2f} build={A.build:.2f}{' DROP' if A.drop else ''}{' SECTION' if A.section_change else ''}")
        last_look, last_pal = D.look, D.palette.name
    if A.n % int(A.fps*4) == 0:
        log.append(f"   {A.t:6.2f}s  bpm={A.bpm:.1f} conf={A.tconf:.2f} locked={A.locked} E={A.energy:.2f} int={A.intensity:.2f} bass={A.bass_slow:.2f} key={A.key_name()} build={A.build:.2f} nov={A.novelty:.2f}")
print("\n".join(log))
if truth:
    tk = np.array(truth['kicks']); dk = np.array(kicks)
    lat = 0.03
    hit = sum(1 for k in tk if np.any(np.abs(dk - k - lat) < 0.05))
    fa = sum(1 for k in dk if not np.any(np.abs(tk + lat - k) < 0.05))
    print(f"kicks: truth {len(tk)}, detected {len(dk)}, matched {hit} ({hit/max(len(tk),1):.0%}), false {fa}")
    tb = np.array(truth['beats'])
    errs = []
    for (t, bb, lk) in beats:
        if not lk: continue
        j = np.argmin(np.abs(tb - t)); errs.append(t - tb[j])
    errs = np.array(errs)
    p = 60/truth['bpm']
    print(f"beats locked {len(errs)}/{len(beats)}, phase err median {np.median(errs)*1000:.0f} ms, |err|<70ms: {np.mean(np.abs(errs)<0.07):.0%}; true bpm {truth['bpm']}")
    # downbeat check: bar_beat==0 should land on truth beats index%4==0
    ok = tot = 0
    for (t, bb, lk) in beats:
        if not lk or bb != 0: continue
        j = np.argmin(np.abs(tb - t));
        if abs(tb[j]-t) < 0.1: tot += 1; ok += (j % 4 == 0)
    print(f"downbeats correct {ok}/{tot}")
img = np.array(rows)  # time x leds x 3
img = np.repeat(img, 4, axis=1)
Image.fromarray(img.transpose(1, 0, 2)).save(path.replace('.wav', '_show.png'))
print("image", img.shape)
