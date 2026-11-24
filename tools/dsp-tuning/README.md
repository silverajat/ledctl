# DSP tuning

Offline simulations used to tune the board-mic audio path (`firmware/ledctl/audio.h`) against captured
FPGA telemetry:
- `sim.py capture.csv`: replays a capture through a Python model of the noise-floor filter and beat detector
- `tracker.py`, `trtest.py`: the beat tracker model, tested on `caps/testtrack.csv`
- `evalgt.py`: scores beats against `beats120_gt.txt` (a 120 BPM test track)
- `harness.cpp`: runs the real firmware code on the PC against the same captures
