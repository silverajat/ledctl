# Music brain

Listens to whatever the laptop is playing (Spotify or anything else), works out what a lighting operator
would listen for, and runs a light show on the board's WS2812B strip. A virtual copy of the strip, plus
everything the brain is hearing, is shown on the light bench at http://localhost:8766.

## Run (Windows, Python 3.13)
```sh
pip install numpy pyaudiowpatch onnxruntime pyserial bleak
python brain.py                    # live: whatever the laptop is playing
python brain.py --wav song.wav     # a file instead
python brain.py --leds 115 --board usb|ble|off
```
`START_LIGHT_BENCH.bat` does the same with a double-click.

## Files
| File | Job |
|---|---|
| `audio_in.py` | WASAPI loopback capture (what the laptop plays) or a `.wav` file |
| `analysis.py` | real-time analysis, ~10 ms hops, nothing looks ahead (below) |
| `genre.py` | YAMNet in the background: genre family, mood, vocals |
| `show.py` | the director and the effect engine (below) |
| `board.py` | sends frames to the board: checksummed frames over USB at 60 fps, Bluetooth LE as fallback, gamma correction |
| `brain.py` | ties it together and serves the light bench (`web/index.html`) |
| `prefs.py` | taste learned from the A/B test, per genre family |
| `truth.py` | offline reference answers for a recorded song |
| `tools/` | scoring, tuning, synthetic test songs, offline rendering |

## What it hears (`analysis.py`)
- 40 mel bands, each normalised to its own recent range
- kick, snare and hi-hat detectors: spectral flux per band group with adaptive thresholds
- tempo from an autocorrelation with harmonic scoring and a tempo prior; beat phase from a PLL on a
  continuous beat position, so a correction never adds or drops a beat
- bar lines from chord changes, bass-note changes and kicks, minus backbeat snares
- energy relative to the song, intensity (energy + bass + drum density)
- key (Krumhansl), chord changes, lead pitch, voice level and stereo position
- sections from novelty spikes on downbeats, build-ups from a steady rise with the bass held back,
  drops from a hard kick and full bass after a build or a bass-light stretch

## What it shows (`show.py`)
The director works like a club lighting operator:
- the genre sets the colour family and how punchy the show may be
- looks change only on downbeats (a drop changes instantly) and their size follows the music's intensity:
  ambient, bassline, groove, chase, swell, build, peak, breakdown
- each drum has its own job: kick = pulse from the centre, snare = bursts, hi-hats = sparkle;
  the voice is a soft glow placed where the singer sits in the stereo mix
- colours stay hue-preserving: auto-exposure and limits scale colours down instead of washing them out
  to white
- a power limit keeps the real strip within the USB supply

## Teaching it
- **Light bench:** "looks right / looks wrong" (keys G / B) saves the last 10 s of what the brain was
  thinking, with the song name. Sessions are logged locally.
- **A/B taste test:** two strips show the same moment with one thing different (colours, pattern,
  brightness, voice light, pulses or smoothness), randomly left or right. Picks (keys 1 / 2 / 0) are saved
  per genre family and the director follows them.
- **Reference answers:** songs played for at least 45 s are recorded locally. `truth.py` works out beats
  and bar lines with [Beat This!](https://github.com/CPJKU/beat_this) and sections and drops from the
  song's self-similarity.
- **Scoring and tuning:** `tools/score.py` replays every recording through the live brain and scores
  beats, tempo, bar lines, sections and drops. `tools/tune.py` searches the brain's settings to raise that
  score and saves `tuned.json`.
- **Offline:** `tools/synth_songs.py` makes test songs with known beats and sections;
  `tools/offline.py` scores a file and draws the show as an image.

Recordings, session logs and taste data stay on the laptop and are not in this repository.
