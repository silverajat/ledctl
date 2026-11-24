# ESP32-C3 firmware

- `ledctl.ino`: modes, Bluetooth LE / Wi-Fi control, USB frame link (`0xA8` checksummed frames from the
  laptop), forwarding to the FPGA at 1 Mbaud, INA219 current limit, state JSON (`?` over USB).
- `fpga_loader.h`: loads the FPGA's SRAM over JTAG at every boot (Gowin TN653 sequence), so the board
  doesn't depend on the FPGA's own flash.
- `audio.h`: the board-mic audio path (gated Kalman noise floor, beat tracker).
- `page.html` → `page.h` (`tools/mkpage.py`): the phone/Wi-Fi control page.
- `fpga_bits.h` is generated: `python tools/fs2h.py ../fpga/v2/ledctl_v2.fs ledctl/fpga_bits.h`.

Board settings: ESP32C3 Dev Module, USB CDC on boot enabled, partition scheme "Huge APP".
