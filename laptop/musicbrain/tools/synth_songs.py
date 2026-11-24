"""Synthetic test songs with known structure (ground truth for kicks, beats, sections)."""
import json, sys, wave
import numpy as np
SR = 44100
rng = np.random.default_rng(1)

def lp(x, fc):
    a = np.exp(-2*np.pi*fc/SR); y = np.zeros_like(x); s = 0.0
    # vectorised one-pole via scipy-free loop in chunks
    from itertools import accumulate
    out = np.empty_like(x); prev = 0.0
    for i in range(len(x)):
        prev = (1-a)*x[i] + a*prev; out[i] = prev
    return out

def kick(n=0.35):
    t = np.arange(int(SR*n))/SR
    f = 45 + 90*np.exp(-t*30)
    ph = 2*np.pi*np.cumsum(f)/SR
    return np.sin(ph)*np.exp(-t*9)*0.9 + 0.15*rng.standard_normal(len(t))*np.exp(-t*200)

def snare(n=0.25):
    t = np.arange(int(SR*n))/SR
    nz = rng.standard_normal(len(t))
    nz = nz - np.convolve(nz, np.ones(4)/4, 'same')
    return (0.5*nz*np.exp(-t*18) + 0.4*np.sin(2*np.pi*190*t)*np.exp(-t*25))*0.7

def hat(n=0.06):
    t = np.arange(int(SR*n))/SR
    nz = rng.standard_normal(len(t)); nz = np.diff(np.diff(nz, prepend=0), prepend=0)
    return nz*np.exp(-t*70)*0.12

def tone(f, n, kind='saw', dec=0.0, att=0.005):
    t = np.arange(int(SR*n))/SR
    if kind == 'saw': w = 2*((t*f) % 1)-1
    elif kind == 'sq': w = np.sign(np.sin(2*np.pi*f*t))
    else: w = np.sin(2*np.pi*f*t)
    env = np.minimum(1, t/att) * (np.exp(-t*dec) if dec else 1)
    rel = np.minimum(1, (n - t)/0.02)
    return w*env*rel

def midi(m): return 440*2**((m-69)/12)

def place(buf, x, t, g=1.0):
    i = int(t*SR); j = min(len(buf), i+len(x))
    if i < len(buf): buf[i:j] += x[:j-i]*g

def edm():
    bpm = 128; b = 60/bpm
    plan = [('intro', 8), ('verse', 16), ('build', 8), ('drop', 16), ('break', 8), ('drop2', 8)]
    nb = sum(n for _, n in plan)*4
    L = nb*b + 2; out = np.zeros(int(L*SR)); truth = {'bpm': bpm, 'kicks': [], 'sections': [], 'beats': []}
    chords = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]   # Am F C G
    basses = [45, 41, 36, 43]
    lead = [69, 72, 76, 74, 72, 69, 67, 69]
    beat = 0
    for name, bars in plan:
        truth['sections'].append((round(beat*b, 3), name))
        for bar in range(bars):
            ch = chords[bar % 4]; bs = basses[bar % 4]
            t0 = beat*b
            if name in ('intro', 'break', 'build', 'verse'):
                pad = sum(tone(midi(m), 4*b, 'saw', att=0.3) for m in ch)/3
                place(out, pad, t0, 0.10 if name != 'verse' else 0.07)
            for q in range(4):
                tq = t0 + q*b
                truth['beats'].append(round(tq, 3))
                if name in ('verse', 'drop', 'drop2'):
                    place(out, kick(), tq, 1.0); truth['kicks'].append(round(tq, 3))
                    for e in (0.5,):
                        place(out, tone(midi(bs+12), b*0.45, 'saw', dec=4), tq+e*b, 0.22 if name != 'verse' else 0.15)
                    place(out, tone(midi(bs), b*0.9, 'sine', dec=1), tq, 0.35)
                if name in ('drop', 'drop2') and q in (1, 3):
                    place(out, snare(), tq, 0.9)
                if name in ('verse', 'drop', 'drop2', 'intro'):
                    for e in (0.5,) if name == 'intro' else (0, 0.5):
                        place(out, hat(), tq+e*b, 1.0)
                if name in ('drop', 'drop2'):
                    for e in (0.25, 0.75): place(out, hat(), tq+e*b, 0.6)
                    m = lead[(bar*4+q) % 8]
                    place(out, tone(midi(m), b*0.95, 'sq', dec=2), tq, 0.08)
                if name == 'build':
                    prog = (bar*4+q)/(bars*4)
                    sub = 1 if prog < .25 else 2 if prog < .5 else 4 if prog < .75 else 8
                    if bar == bars-1 and q == 3: sub = 0
                    for s in range(sub):
                        place(out, snare(), tq + s*b/sub, 0.4 + 0.5*prog)
                    nz = rng.standard_normal(int(b*SR))
                    t = np.arange(len(nz))/SR
                    riser = nz - np.convolve(nz, np.ones(int(30 - 25*prog)+1)/(int(30-25*prog)+1), 'same')
                    if not (bar == bars-1 and q == 3):
                        place(out, riser, tq, 0.04 + 0.12*prog)
                beat += 1
    return out, truth

def rnb():
    bpm = 72; b = 60/bpm
    out = np.zeros(int((24*4*b + 2)*SR)); truth = {'bpm': bpm, 'kicks': [], 'sections': [(0, 'intro'), (8*4*b, 'verse')], 'beats': []}
    chords = [[62, 65, 69, 72], [60, 64, 67, 71], [58, 62, 65, 69], [57, 60, 64, 67]]
    bassn = [38, 36, 34, 33]
    beat = 0
    for bar in range(24):
        t0 = beat*b; ch = chords[bar % 4]
        rh = sum(tone(midi(m), 4*b, 'sine', dec=0.6, att=0.01) for m in ch)/4
        place(out, rh, t0, 0.25); place(out, rh, t0+2.5*b, 0.12)
        place(out, tone(midi(bassn[bar % 4]), 1.4*b, 'sine', dec=0.8, att=0.02), t0, 0.5)
        place(out, tone(midi(bassn[bar % 4]+7), 0.5*b, 'sine', dec=2, att=0.02), t0+2.75*b, 0.35)
        for q in range(4): truth['beats'].append(round(t0+q*b, 3))
        if bar >= 8:
            for kt in (0, 1.75, 2.5):
                place(out, kick(), t0+kt*b, 0.9); truth['kicks'].append(round(t0+kt*b, 3))
            place(out, snare(), t0+1*b, 0.6); place(out, snare(), t0+3*b, 0.6)
            for e in range(8):
                sw = 0.08 if e % 2 else 0
                place(out, hat(), t0 + (e*0.5+sw)*b, 0.7 if e % 2 == 0 else 0.4)
        beat += 4
    return out, truth

def save(name, x, truth):
    x = x/np.max(np.abs(x))*0.8
    w = wave.open(name+'.wav', 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((x*32767).astype(np.int16).tobytes()); w.close()
    json.dump(truth, open(name+'.json', 'w'))

if __name__ == '__main__':
    import os; os.makedirs('songs', exist_ok=True)
    for f in (edm, rnb):
        x, tr = f(); save('songs/'+f.__name__, x, tr); print(f.__name__, len(x)/SR, 's')
