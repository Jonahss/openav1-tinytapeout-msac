# SPDX-License-Identifier: Apache-2.0
"""Replay a dav1d golden symbol trace through the msac core on the Tiny Tapeout pins (the byte-wide register window of
tt_um_jonahss_msac) and check every symbol, range and adapted CDF row. The trace format is the one openav1's
tb/test_msac.py replays (tools/trace.sh <stream> <out> 1); test/vectors/*.txt holds a small one.

Env: MSAC_TRACE (default test/vectors/tile0.txt), MSAC_MAX_SYMS caps the run.
"""
import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Timer

KIND_ADAPT, KIND_BOOL, KIND_EQUI = 0, 1, 2
HERE = os.path.dirname(os.path.abspath(__file__))


def parse_trace(path):
    """Yield (data, disable_cdf_update, requests) per tile; a request is
    (kind, n, cdf_tuple, cnt, exp_sym, exp_rng, post_tuple_or_None, postcnt_or_None)."""
    frame, pending, cur, hold = [], [], None, None

    def flush_hold():
        nonlocal hold
        if hold is not None:
            cur["reqs"].append(hold); hold = None

    with open(path) as fh:
        for line in fh:
            if not line or line[0] == "#":
                continue
            f = line.split()
            k = f[0]
            if k == "U":
                if hold is not None:
                    kk, n, cdf, cnt, sym, rng, _, _ = hold
                    hold = (KIND_ADAPT, 1, cdf, int(f[1]), sym, rng, (int(f[2]),), int(f[3]))
                continue
            if hold is not None:
                flush_hold()
            if k == "T":
                if frame and not pending:
                    for t in frame:
                        yield t["data"], t["dis"], t["reqs"]
                    frame = []
                data = bytes.fromhex(f[3]) if len(f) > 3 else b""
                assert len(data) == int(f[1])
                cur = dict(data=data, dis=int(f[2]), reqs=[], key=None)
                frame.append(cur); pending.append(cur)
            elif k == "H":
                cur["key"] = (int(f[1]), int(f[2]))
            elif k == "D":
                pending = []
                cur = next(t for t in frame if t["key"] == (int(f[1]), int(f[2])))
            elif k == "S":
                dis = cur["dis"]
                if f[1] == "A":
                    n, sym, rng, cnt = int(f[2]), int(f[3]), int(f[4]), int(f[5])
                    pre = tuple(int(x) for x in f[6:6 + n])
                    post = postcnt = None
                    if not dis:
                        post = tuple(int(x) for x in f[6 + n:6 + 2 * n]); postcnt = int(f[6 + 2 * n])
                    cur["reqs"].append((KIND_ADAPT, n, pre, cnt, sym, rng, post, postcnt))
                elif f[1] == "B":
                    hold = (KIND_BOOL, 1, (int(f[2]),), 0, int(f[3]), int(f[4]), None, None)
                elif f[1] == "E":
                    cur["reqs"].append((KIND_EQUI, 1, (), 0, int(f[2]), int(f[3]), None, None))
    if hold is not None:
        flush_hold()
    for t in frame:
        yield t["data"], t["dis"], t["reqs"]


class Pins:
    """The host side of the register window: ui_in[6:0] address, ui_in[7] write strobe, uio data, uo_out status."""

    def __init__(self, dut):
        self.dut = dut

    async def write(self, addr, value):
        self.dut.ui_in.value = 0x80 | addr
        self.dut.uio_in.value = value & 0xFF
        await RisingEdge(self.dut.clk)
        await Timer(1, "ns")
        self.dut.ui_in.value = addr                 # strobe low: one write per strobe edge for 0x20 / 0x21
        await RisingEdge(self.dut.clk)
        await Timer(1, "ns")

    async def read(self, addr):
        self.dut.ui_in.value = addr
        self.dut.uio_in.value = 0
        await RisingEdge(self.dut.clk)
        await ReadOnly()
        v = int(self.dut.uio_out.value)
        await Timer(1, "ns")
        return v

    def status(self):
        return int(self.dut.uo_out.value)

    async def wait_status(self, mask, timeout=200):
        for _ in range(timeout):
            await ReadOnly()
            s = self.status()
            if s & mask:
                await Timer(1, "ns")
                return s
            await RisingEdge(self.dut.clk)
        raise AssertionError(f"status bit {mask:#x} never rose (status {self.status():#04x})")


ST_RESP, ST_READY, ST_INREADY, ST_WB = 1, 2, 4, 8


@cocotb.test()
async def replay_trace(dut):
    path = os.environ.get("MSAC_TRACE", os.path.join(HERE, "vectors", "tile0.txt"))
    max_syms = int(os.environ.get("MSAC_MAX_SYMS", "0")) or None
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    p = Pins(dut)
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)

    done = 0
    for ti, (data, dis, reqs) in enumerate(parse_trace(path)):
        # init: clears the byte FIFO, cdf_update_en per tile; then the stream bytes (as many as fit; the rest later)
        upd = 0 if dis else 2
        await p.write(0x20, 0x01 | upd)
        pending = list(data)
        async def top_up():
            while pending and (p.status() & ST_INREADY):
                await p.write(0x21, pending.pop(0))
        await top_up()
        if not pending:
            await p.write(0x20, 0x08 | upd)         # eos: nothing more to push
        last_cdf = None
        for ri, (kind, n, cdf, cnt, exp_sym, exp_rng, post, postcnt) in enumerate(reqs):
            if cdf != last_cdf:
                for k, c in enumerate(cdf):
                    await p.write(2 * k, c & 0xFF)
                    await p.write(2 * k + 1, (c >> 8) & 0xFF)
                last_cdf = cdf
            await p.write(0x1E, (kind << 4) | n)
            await p.write(0x1F, cnt)
            await p.write(0x20, 0x04 | upd | (0x08 if not pending else 0))
            # the core may stall on stream bytes: keep the FIFO fed while waiting for the symbol
            fed = False
            for _ in range(400):
                had = bool(pending)
                await top_up()
                if had and not pending:
                    await p.write(0x20, 0x08 | upd)   # the last byte went in: eos from now on
                await ReadOnly()
                s = p.status()
                await Timer(1, "ns")
                if s & ST_RESP:
                    break
                await RisingEdge(dut.clk)
            else:
                raise AssertionError(f"tile {ti} req {ri}: no response (status {p.status():#04x})")
            sym = s >> 4
            assert sym == exp_sym, f"tile {ti} req {ri}: sym {sym} != {exp_sym} (kind {kind} n {n})"
            s = await p.wait_status(ST_WB)
            rng = (await p.read(0x42)) | ((await p.read(0x43)) << 8)
            if rng != exp_rng:
                trail = []
                for _ in range(6):
                    trail.append((p.status(), (await p.read(0x42)) | ((await p.read(0x43)) << 8)))
                dut._log.info(f"DEBUG tile {ti} req {ri} kind {kind} n {n} cnt {cnt}: status/rng trail {trail}; "
                              f"prev req {reqs[ri - 1][:2] if ri else None} exp_rng prev {reqs[ri - 1][5] if ri else None}")
            assert rng == exp_rng, f"tile {ti} req {ri}: rng {rng} != {exp_rng}"
            if post is not None:
                got = []
                for k in range(len(post)):
                    lo = await p.read(0x50 + 2 * k); hi = await p.read(0x51 + 2 * k)
                    got.append(lo | (hi << 8))
                assert tuple(got) == post, f"tile {ti} req {ri}: adapted row {got} != {list(post)}"
                gcnt = await p.read(0x44)
                assert gcnt == postcnt, f"tile {ti} req {ri}: cnt {gcnt} != {postcnt}"
                # the chip's request row is what the host wrote, not the adapted one: last_cdf stays the written row
            done += 1
            if max_syms and done >= max_syms:
                break
        if max_syms and done >= max_syms:
            break
        if pending:
            raise AssertionError(f"tile {ti}: {len(pending)} stream bytes never consumed")
    dut._log.info(f"OK: {done} symbols over the pins")
