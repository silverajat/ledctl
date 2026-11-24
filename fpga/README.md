# FPGA (Gowin GW2AR-LV18QN88C8/I7, 27 MHz)

- **v1** (`v1/`): audio front end. I2S mic receiver, loudness and beat detector, 3-band IIR filter bank
  (~130 Hz / ~1.1 kHz) with log2 envelopes, RGB PWM for an analog strip, telemetry to the ESP32.
- **v2** (`v2/`): everything in v1 plus the WS2812B strip driver. This is what runs on the board.

## ESP32 → FPGA (UART, 1 Mbaud, 8N1)
| Packet | Meaning |
|---|---|
| `A5 'C' r g b` | base colour (used when no frames arrive) |
| `A5 'M' m 0 0` | mode for the board's own effects |
| `A5 'T' ratio floor 0` | beat detector thresholds |
| `A5 'N' n_lo n_hi 0` | LED count (max 300) |
| `A5 'A' on 0 0` | 1 = WS2812B output, 0 = analog PWM |
| `A7 n_lo n_hi` + n × `R G B` | one frame; shown as soon as it's complete (double-buffered) |

A packet that stops for more than 1 ms is dropped and the parser resyncs. With no frames for 1 s the strip
shows the base colour.

## FPGA → ESP32 (every 10 ms and on each beat)
`FF loud level beats bass mid high rx_count strip_refreshes`. The last two are diagnostics: command packets
received, and frames sent to the strip.

## Pins (see `ledctl_v2.cst`)
| Signal | Pin | Notes |
|---|---|---|
| clk | 4 | 27 MHz |
| uart_rx / uart_tx | 70 / 69 | from / to the ESP32 |
| **ws_direct** | **48** | **WS2812B data, J3 pin 3** (3.3 V, drive 16 mA) |
| pwm_r / g / b | 71 / 72 / 73 | J4 MOSFET outputs (inverted WS2812B data on pin 71) |
| led0_n / led1_n | 74 / 75 | beat LED, frame-activity LED |
| i2s sck / ws / sd | 82 / 81 / 80 | MEMS mic |

## Build and test
```sh
cd v2
./build.sh                                   # yowasp-yosys + nextpnr-himbaechel + gowin_pack
iverilog -o tbw tb_ws.v ledctl_top.v && vvp tbw        # frame -> decoded LED colours
iverilog -o tbb tb_boot.v ledctl_top.v && vvp tbb      # the ESP32's boot commands, then a frame
iverilog -o tbg tb_glitch.v ledctl_top.v && vvp tbg    # same, with crosstalk spikes on the UART input
```
