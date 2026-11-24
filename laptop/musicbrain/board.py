"""Sends the light show to the real strip: laptop -> ESP32 (USB cable or Bluetooth) -> FPGA -> WS2812B.

USB:  0xA8, n_lo, n_hi, n x (R, G, B), sum8 of the RGB bytes      over the ESP32's USB serial port
      (the ESP32 drops any frame whose checksum fails)
BLE:  chunks of [led_off_lo, led_off_hi, flags, RGB...] written to the FRAME characteristic
      (flags bit 0 = last chunk: show the frame)
Settings (LED count) go as text: "n=115".
The newest frame always wins: if the link is slow, old frames are dropped, never queued.
"""
import asyncio
import threading
import time
import numpy as np

SVC = "6e1d0001-8c2a-4f0e-9b1a-1edc7a5e0001"
CMD = "6e1d0002-8c2a-4f0e-9b1a-1edc7a5e0001"
FRAME = "6e1d0004-8c2a-4f0e-9b1a-1edc7a5e0001"

# perceived brightness (what the bench shows) -> LED PWM value. WS2812 PWM is linear, eyes are not.
# below 16 (about 6 %) is off: the director already cuts those, and the lowest PWM steps glow visibly in the dark
GAMMA = np.array([0 if i < 16 else max(1, round(255 * (i / 255) ** 2.0)) for i in range(256)], np.uint8)


class Board:
    def __init__(self, mode="auto", fps=60):
        self.mode = mode
        self.fps = fps
        self.status = "off" if mode == "off" else "looking for the board…"
        self.link = None
        self.frame = None
        self.n_sent = None
        self.new = threading.Event()
        self.sent = 0
        self.rate = 0.0
        self.alive = True
        if mode != "off":
            threading.Thread(target=self._run, daemon=True).start()

    def push(self, rgb):
        """rgb: (n, 3) uint8, perceptual (as shown on the bench)"""
        self.frame = GAMMA[rgb]
        self.new.set()

    def state(self):
        return {"status": self.status, "fps": round(self.rate, 1)}

    # ------------------------------------------------------------------
    def _run(self):
        while self.alive:
            ok = False
            if self.mode in ("auto", "usb"):
                ok = self._usb()
            if not ok and self.mode in ("auto", "ble"):
                ok = self._ble()
            if not ok:
                time.sleep(2.0)

    def _count(self):
        now = time.time()
        self.sent += 1
        if not hasattr(self, "_t0"):
            self._t0, self._c0 = now, 0
        if now - self._t0 > 1.0:
            self.rate = (self.sent - self._c0) / (now - self._t0)
            self._t0, self._c0 = now, self.sent

    def _usb(self):
        try:
            import serial
            from serial.tools import list_ports
        except ImportError:
            return False
        port = next((p.device for p in list_ports.comports() if p.vid == 0x303A), None)
        if not port:
            return False
        try:
            s = serial.Serial(port, 115200, timeout=0.2, write_timeout=0.5)
        except Exception as e:  # noqa  (busy: e.g. a serial monitor has the port open)
            self.status = f"USB {port} busy: {e}"[:80]
            return False
        self.status = f"USB {port}"
        self.n_sent = None
        period = 1.0 / self.fps
        try:
            while self.alive:
                if not self.new.wait(0.5):
                    continue
                self.new.clear()
                f = self.frame
                n = len(f)
                if n != self.n_sent:
                    s.write(f"n={n}\n".encode())
                    self.n_sent = n
                data = f.tobytes()
                s.write(bytes([0xA8, n & 0xFF, n >> 8]) + data + bytes([sum(data) & 0xFF]))   # checksummed
                if s.in_waiting:
                    s.read(s.in_waiting)          # the ESP32 prints state; drain it
                self._count()
                time.sleep(max(0.0, period - 0.002))
        except Exception as e:  # noqa  (unplugged)
            self.status = f"USB lost ({e.__class__.__name__}), retrying"
        finally:
            try:
                s.close()
            except Exception:
                pass
        return True

    def _ble(self):
        try:
            import bleak  # noqa
        except ImportError:
            self.status = "no USB board found (pip install bleak for Bluetooth)"
            return False
        try:
            return asyncio.run(self._ble_async())
        except Exception as e:  # noqa
            self.status = f"Bluetooth: {e}"[:80]
            return False

    async def _ble_async(self):
        from bleak import BleakClient, BleakScanner
        self.status = "Bluetooth: scanning…"
        dev = await BleakScanner.find_device_by_filter(
            lambda d, ad: (d.name == "ledctl") or (SVC in [u.lower() for u in (ad.service_uuids or [])]), timeout=6.0)
        if not dev:
            self.status = "board not found (USB or Bluetooth)"
            return False
        async with BleakClient(dev) as c:
            mtu = getattr(c, "mtu_size", 185) or 185
            per = max(10, (min(mtu, 512) - 3 - 3) // 3)          # LEDs per write
            self.status = f"Bluetooth ({per} LEDs/packet)"
            self.n_sent = None
            period = 1.0 / self.fps
            while self.alive and c.is_connected:
                if not self.new.is_set():
                    await asyncio.sleep(0.004)
                    continue
                self.new.clear()
                f = self.frame
                n = len(f)
                if n != self.n_sent:
                    await c.write_gatt_char(CMD, f"n={n}".encode(), response=True)
                    self.n_sent = n
                for off in range(0, n, per):
                    k = min(per, n - off)
                    last = 1 if off + k >= n else 0
                    pkt = bytes([off & 0xFF, off >> 8, last]) + f[off:off + k].tobytes()
                    await c.write_gatt_char(FRAME, pkt, response=False)
                self._count()
                await asyncio.sleep(max(0.0, period - 0.004))
            self.status = "Bluetooth disconnected, retrying"
        return True
