#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Drive the KV260 twin of the Tiny Tapeout chip (fpga/kv260/tt_twin_v.v behind an AXI GPIO at 0xA000_0000) with
the same host protocol the cocotb test runs (test/msac_host.py), and replay golden symbol traces through it.

    sudo python3 fpga/kv260/twin.py [--show] [trace.txt ...]      (default: every test/vectors/*.txt)

Register accesses are single 32-bit loads and stores through ctypes (a Python slice or struct store on an mmap can
become several narrower bus writes: openav1 2026-10-14). --show puts the progress on the panel (fb0).
"""
import argparse
import ctypes
import glob
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "test"))
import msac_host  # noqa: E402
import msac_model  # noqa: E402

BASE = 0xA0000000
GPIO_DATA, GPIO2_DATA = 0x0, 0x8
RST_N, ENA, CLR = 1 << 17, 1 << 18, 1 << 19


class TwinPins:
    def __init__(self, base=BASE):
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.mm = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=base)
        os.close(fd)
        addr = ctypes.addressof(ctypes.c_char.from_buffer(self.mm))
        self.out = ctypes.c_uint32.from_address(addr + GPIO_DATA)
        self.inp = ctypes.c_uint32.from_address(addr + GPIO2_DATA)
        self.ctl = 0

    def raw(self):
        return self.inp.value

    def check_bitstream(self):
        v = self.raw()
        if v >> 28 != 0xA:
            raise SystemExit(f"no tt twin at {BASE:#x}: read {v:#010x} (load it: sudo fpga/kv260/load.sh <fw dir>)")
        b0 = (v >> 25) & 7
        t0 = time.time()
        while (self.raw() >> 25) & 7 == b0:               # the heartbeat: bit 23 of a 50 MHz counter, ~6 Hz
            if time.time() - t0 > 2:
                raise SystemExit("chip clock not running (heartbeat stuck): pl_clk1 off? check the overlay")
        return v

    def reset(self):
        self.ctl = 0
        self.out.value = CLR
        for _ in range(64):
            self.raw()
        self.ctl = RST_N | ENA
        self.out.value = self.ctl
        for _ in range(64):
            self.raw()

    # the msac_host backend
    def pins(self, ui, uio, drive):
        self.out.value = self.ctl | (ui & 0xFF) | ((uio & 0xFF) << 8) | ((1 << 16) if drive else 0)

    def wait(self, n):
        # one AXI read round trip is >= ~10 cycles of the 100 MHz bus, i.e. >= 5 chip clocks: n reads cover n clocks
        for _ in range(max(1, n)):
            self.raw()

    def sample(self):
        v = self.raw()
        return v & 0xFF, (v >> 8) & 0xFF

    def contention(self):
        return bool((self.raw() >> 24) & 1)


class Panel:
    """A status screen on the 7-inch panel (fb0, RGB565), drawn with PIL when it is there, else a no-op."""

    def __init__(self, enable):
        self.ok = False
        if not enable:
            return
        try:
            from PIL import Image, ImageDraw, ImageFont
            with open("/sys/class/graphics/fb0/virtual_size") as f:
                self.w, self.h = (int(x) for x in f.read().strip().split(","))
            with open("/sys/class/graphics/fb0/stride") as f:
                self.stride = int(f.read())
            self.Image, self.ImageDraw = Image, ImageDraw
            try:
                self.font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 28)
                self.small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 20)
            except OSError:
                self.font = self.small = ImageFont.load_default()
            self.ok = True
        except Exception as e:  # no panel, no PIL: the run itself matters more
            print(f"(panel off: {e})")

    def draw(self, lines, colour=(255, 255, 255)):
        if not self.ok:
            return
        W, H = 800, 480
        img = self.Image.new("RGB", (W, H), (10, 12, 30))
        d = self.ImageDraw.Draw(img)
        d.text((24, 20), "openav1 msac  -  Tiny Tapeout twin on the KV260", font=self.font, fill=(120, 200, 255))
        y = 80
        for i, ln in enumerate(lines):
            d.text((24, y), ln, font=self.font if i == 0 else self.small, fill=colour if i == 0 else (220, 220, 220))
            y += 44 if i == 0 else 30
        import numpy as np
        a = np.asarray(img, dtype=np.uint16)
        v = ((a[:, :, 0] >> 3) << 11) | ((a[:, :, 1] >> 2) << 5) | (a[:, :, 2] >> 3)
        rows = v.astype("<u2").tobytes()
        with open("/dev/fb0", "r+b") as fb:
            for yy in range(H):
                fb.seek(yy * self.stride)
                fb.write(rows[yy * W * 2:(yy + 1) * W * 2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="*")
    ap.add_argument("--max-syms", type=int, default=0)
    ap.add_argument("--show", action="store_true", help="progress and verdict on the panel")
    ap.add_argument("--self-test", action="store_true", help="also run the on-chip self-test cases against the model")
    a = ap.parse_args()
    paths = a.traces or sorted(glob.glob(os.path.join(HERE, "..", "..", "test", "vectors", "*.txt")))
    p = TwinPins()
    v = p.check_bitstream()
    p.reset()
    s = p.sample()
    print(f"twin at {BASE:#x}: {v:#010x}, chip clock running, after reset uo_out {s[0]:#04x} uio {s[1]:#04x}")
    panel = Panel(a.show)
    total, t0 = 0, time.time()
    log = []
    for path in paths:
        name = os.path.basename(path)
        last = [0.0]

        def progress(done, tile):
            now = time.time()
            if now - last[0] > 3.0:
                last[0] = now
                rate = (total + done) / max(now - t0, 1e-6)
                print(f"  {name}: tile {tile}, {done} symbols ({rate:.0f}/s)", flush=True)
                panel.draw([f"replaying {name}", f"tile {tile}   {done} symbols", f"total {total + done}   {rate:.0f} symbols/s"] + log)

        try:
            n = msac_host.run(msac_host.replay(path, a.max_syms or None, progress), p)
        except msac_host.Mismatch as e:
            print(f"FAIL {name}: {e}")
            panel.draw([f"FAIL  {name}", str(e)[:60]] + log, colour=(255, 90, 90))
            return 1
        if p.contention():
            print(f"FAIL {name}: uio contention seen")
            panel.draw([f"FAIL  {name}", "uio contention"] + log, colour=(255, 90, 90))
            return 1
        total += n
        log.append(f"PASS {name}: {n} symbols")
        print(f"PASS {name}: {n} symbols", flush=True)
    dt = time.time() - t0
    print(f"PASS: {total} symbols over the pins in {len(paths)} trace(s), {dt:.1f} s")
    if a.self_test:
        msac_host.run(msac_host.write(0x20, 0x01), p)
        for name, kind, n, row, cnt, upd, count in msac_model.BIST_CASES:
            msac_host.run(msac_host.clear_counters(), p)
            got = msac_host.run(msac_host.self_test(kind, n, row, cnt, upd, count), p)
            exp = msac_model.bist_signature(kind, n, row, cnt, upd, count)
            syms, clks, busy = msac_host.run(msac_host.read_counters(), p)
            ok = got == exp and syms == count
            line = f"{'PASS' if ok else 'FAIL'} self-test {name}: {got:#010x} (model {exp:#010x}), {syms} symbols in {clks} clocks"
            print(line, flush=True)
            log.append(line[:60])
            if not ok:
                panel.draw([f"FAIL  self-test {name}"] + log, colour=(255, 90, 90))
                return 1
    panel.draw([f"PASS  {total} symbols", f"{len(paths)} traces, {dt:.0f} s, every symbol, range and CDF row"] + log,
               colour=(120, 255, 140))
    return 0


if __name__ == "__main__":
    sys.exit(main())
