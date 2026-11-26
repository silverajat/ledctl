# Bring-up log

What broke while bringing up ledctl, how each problem was found, and the fix. In order.

## Board v1 (18 Nov 2026)

### 1. The FPGA's flash couldn't be written
**Symptom:** programming the GW2AR's SPI flash through the CH347 JTAG adapter failed every time
("write en: Error"). Loading the FPGA's SRAM directly worked.
**Fix:** instead of reworking the board, I wrote a JTAG loader for the ESP32 (`firmware/ledctl/fpga_loader.h`),
following Gowin's programming spec (TN653) and openFPGALoader's command sequence: erase SRAM, init address,
stream the bitstream, write the checksum, check DONE. The bitstream lives in the ESP32's flash, and the
ESP32 configures the FPGA at every power-up. The board no longer needs a programmer at all.

### 2. Music mode didn't react
**Symptom:** the loudness level from the FPGA never moved.
**Cause:** an unsigned subtraction in the FPGA's fast loudness filter (`lfast - ax` when `ax > lfast`) wrapped
around.
**Fix:** compare first, then subtract in the right order. Caught again by a testbench.

### 3. The room fan caused fake beats
**Symptom:** beats fired with no music playing.
**Fix:** a per-band Kalman filter on the noise floor (`firmware/ledctl/audio.h`). It's *gated*: only samples
within 2σ of the predicted floor update it, so music isn't learned as noise, and it may only rise slowly
(over minutes) but fall fast. Signal = level above floor + 2σ, so the fan maps to zero.

### 4. Spotify's audio-analysis API was shut down
The plan was to take beat and section data from Spotify. Spotify closed that API to new apps, so the laptop
captures its own audio output (WASAPI loopback) and analyses it itself. In the end that made the project
better: the analysis is mine and works for any audio.

### 5. Windows Bluetooth error 0x80070016
Uncached GATT reads failed on Windows. Fixed with a persistent `GattSession` (MaintainConnection) and a
cached-read fallback.

### 6. "Too flashy", "can't feel the music", "blue lights on a party song"
Not bugs but design problems, found by testing with real songs. They led to:
- looks that change only on downbeats, sized to the song's intensity
- separate jobs for each drum, and a voice layer placed by stereo position
- colour families chosen per genre by a neural network (YAMNet), locked once a song settles
- no white flashes, hue-preserving limits, auto-exposure
- a test bench with "looks right / looks wrong" logging, and an A/B taste test

## The addressable strip (23 Nov 2026)

### 7. Wrong end of the strip
**Symptom:** frozen random colours. WS2812B LEDs show random colours at power-up until they get valid data.
**Cause:** wired to the strip's output end (both ends have the same connector). The arrows show the data
direction.

### 8. The first LED died
**Symptom:** after a period where the ground and data wires were mixed up, the strip froze on one colour,
and the first LED stayed dark.
**Diagnosis:** the board's current sensor showed current changing with the data, but nothing visible
changed. Each WS2812B passes data on to the next, so a dead first pixel blocks the whole strip.
**Fix:** cut off LED 1 and solder onto LED 2's pads. Rule from then on: ground first, data last.

### 9. Strip signal vs. the serial link (the hard one)
**Symptom:** at normal drive strength, the strip (driven through the board's MOSFET output plus a pull-up)
mostly ignored the data. At the FPGA's maximum drive the strip read it perfectly, but then the board's own
commands stopped working and the strip froze.
**Diagnosis:** with no way to probe the board, I added two counters to the FPGA's telemetry: command
packets received and strip refreshes sent.
- maximum drive on pin 71: commands not getting through
- normal drive: commands received and the strip refreshed about 50 times a second, but the strip didn't take
  the data

Pin 71 (strip data) is right next to pin 70 (the UART from the ESP32). Fast edges on 71 coupled into 70.
**Fix:** a glitch filter on the UART input, and the strip moved to a direct FPGA pin (J3 pin 3, FPGA pin 48)
on another side of the chip. The WS2812B accepts the FPGA's 3.3 V data at this cable length.

### 10. Random colours on the strip, but not on the bench
**Symptom:** the bench showed a smooth colour wash while the strip showed random colours everywhere.
**Cause:** the laptop sends ~18 KB/s of frames over USB. The ESP32's USB receive buffer (default ~256 bytes)
overflowed while Wi-Fi and Bluetooth were busy. Dropped bytes shifted every colour after them.
**Fix:** an 8 KB receive buffer and a checksum on every frame (`0xA8, n, RGB…, sum8`). A damaged frame is
dropped, never shown, and the board counts drops (`fbad` in its state).

## The star lights (26 Nov 2026)

### 11. Star lights that only lit at power-up
**Setup:** a 2×AA warm-white star string, wired from J4 pin 1 (+5 V) through 30 Ω to one of J4's spare
low-side MOSFET outputs, dimmed by the FPGA.
**Symptom:** the stars never lit while the show ran, but flashed for about a second every time the board
started.
**Steps:**
1. A full flash erases the board's saved settings, so the strip length could fall back to 100 and the
   extra "star pixel" would be read as a strip LED. Made that impossible: the laptop reads the board's state
   every 2 s and resends the length, and the firmware treats the last pixel as the stars whenever a frame
   is longer than the strip.
2. Still dark. A test script with the strip held constant and the stars cycling full / half / off showed
   the board's current didn't change at all (~345 mA in every phase), so no current was flowing.
3. Added read-back at every stage: the star level the ESP32 sends, how many star commands it sent, the
   level the FPGA holds, and the FPGA's output pins (in the telemetry). Every stage reported the right value
   and the output pin high, at 100%.
4. So the fault was past the board: the wire was on **J2 pin 5**, not J4 pin 4. J2 pin 5 is the FPGA's
   JTAG data output, which only toggles while the ESP32 loads the FPGA: that was the flash at power-up.
   Moving it with the power on also left the FPGA unconfigured until a power cycle.
**Fix:** the wire on J4 pin 4. The stars now flash once per strong on-beat kick.

## Lessons
- Add observability before guessing. The two FPGA counters ended two hours of guesswork in one run.
- When a fix makes one thing work and another break, look at what's physically next to it.
- Measure before rewriting. The current sensor said "no current" before any code changed; read-back at
  every stage then proved the code and pointed at the wiring.
- Change wiring with the power off.
- Test against what the user actually sees: the bench matched, the strip didn't, and that difference was the
  bug.
