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
| `songmap.py` | known songs: finds where we are in a recorded song and lays its beats, sections and drops over the live analysis |
| `cinema.py` | cinema mode: screen colours + sound (films, YouTube, games) |
| `stars.py` | the star string on J4's spare output: one clean flash per strong on-beat kick (a fuller star show is one setting away) |
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
- drums vs. sustained sound (median-filter harmonic/percussive split), stereo balance, risers and impacts

## What it shows (`show.py`)
The director works like a club lighting operator (LJ):
- the beat goes into movement and colour, not brightness; brightness follows the size of the section
- the strip is 8 virtual fixtures; layers are a base colour, one moving look and at most one accent
- looks: wash, bassfloor, bounce, stripes, sweep, chase, scanner, split, punch, swell, peak, build,
  in three styles (smooth for R&B / chill, punchy for hip-hop, club for EDM / pop / rock)
- phrases: a look holds for a section, varies every 4 bars, changes with the next section; build-ups close
  in and speed up, the beat before a drop goes dark, the drop hits
- colours: genre picks the family, the key picks the palette, the bass note picks the lead colour;
  palettes change only at big moments (drops, into a chorus); two-colour looks use opposite hues
- restraint: most drum hits do nothing; snares can wipe or reverse the movement (at most every 2 bars),
  big impacts send a ripple (at most every 4 bars)
- no white: grey is never sent, limits scale colours down instead of washing them out
- a power limit keeps the real strip within the USB supply
- the star string flashes on the kick and nothing else; in cinema mode it glows in dark scenes and flashes
  with impacts

## Modes
- **music**: the show above. Songs you have played before (and that `truth.py` has analysed) get a
  pre-planned show: `songmap.py` matches the live audio against the recording and the director gets the
  real beats, sections and the time to the next drop.
- **cinema**: films, YouTube, games. The strip takes the scene's main colours (room layout) or the colour
  of each screen edge (strip around the screen); dialogue keeps it calm, a score swells it, music with a
  beat sends soft waves, explosions and hits send one accent in the scene's colour. Subtle / normal /
  intense. Stays on while a video site or player is in front, even when the film has music.
- **auto** (default): music while Spotify plays or the sound is music; cinema when it hears speech or film
  sounds for a few seconds.

## Teaching it
- **Light bench:** "looks right / looks wrong" (keys G / B) saves the last 10 s of what the brain was
  thinking, with the song name. Sessions are logged locally.
- **A/B taste test:** two strips show the same moment with one thing different (colours, pattern,
  brightness, voice light or smoothness), randomly left or right. Picks (keys 1 / 2 / 0) are saved
  per genre family and the director follows them.
- **Reference answers:** songs played for at least 45 s are recorded locally. `truth.py` works out beats
  and bar lines with [Beat This!](https://github.com/CPJKU/beat_this) and sections and drops from the
  song's self-similarity.
- **Scoring and tuning:** `tools/score.py` replays every recording through the live brain and scores
  beats, tempo, bar lines, sections and drops. `tools/tune.py` searches the brain's settings to raise that
  score and saves `tuned.json`.
- **Offline:** `tools/synth_songs.py` makes test songs with known beats and sections;
  `tools/offline.py` scores a file and draws the show as an image; `tools/render.py song.wav --plan`
  renders the whole show (as a known song) to a picture; `tools/gallery.py` draws every look for a few bars.

Recordings, session logs and taste data stay on the laptop and are not in this repository.
