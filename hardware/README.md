# Hardware (ledctl v1)

- `kicad/`: KiCad project (schematic, 4-layer PCB, custom symbols and footprints)
- `fab/`: what went to JLCPCB: Gerbers, BOM, pick-and-place, schematic PDF, FPGA pin constraints
- `enclosure/`: OpenSCAD source and STL files for a two-part box

## Connectors
| Header | Pins |
|---|---|
| **J2** JTAG (1×6) | 1 3V3 · **2 GND** · 3 TCK · 4 TDI · 5 TDO · 6 TMS |
| **J3** expansion (2×5, not fitted) | 1 3V3 · **2 GND** · **3 FPGA pin 48 (strip data)** · 4–10 more FPGA I/O |
| **J4** strip (1×4) | **1 +5V** (after the current sensor) · 2 G · 3 R · 4 B (low-side MOSFETs) |

**Wiring a WS2812B strip:** GND → J2 pin 2 (or J3 pin 2), 5V → J4 pin 1, DIN → J3 pin 3.
Connect ground first and data last. Power comes from USB-C through a 2.6 A fuse; the firmware limits the strip
to about 2 A.

## Known notes
- J4's outputs are open-drain MOSFETs. For WS2812B data they need a pull-up and switch too slowly at normal
  drive strength, so the strip uses J3 instead (see `docs/bringup.md`).
- MK2's ground ring is 0.16 mm from its sound hole (fixed in the source for v2; no effect on these boards).
