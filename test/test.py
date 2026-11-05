# SPDX-License-Identifier: Apache-2.0
"""Replay dav1d golden symbol traces through the msac core on the Tiny Tapeout pins and check every symbol, range
and adapted CDF row. The host protocol is test/msac_host.py, the same code the KV260 twin runs (fpga/kv260/twin.py).

This backend plays a slow, sloppy host: every wait is stretched by 0..2 random clocks, and when MSAC_SKEW=1
(the default) the data pins of a write land one clock after the strobe, the pin skew the input synchronisers
must absorb.

Env: MSAC_TRACE (one trace; default every test/vectors/*.txt), MSAC_MAX_SYMS caps each run, MSAC_SKEW=0 turns
the skew off, MSAC_SEED seeds the jitter.
"""
import glob
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Timer

import msac_host

HERE = os.path.dirname(os.path.abspath(__file__))


class SimPins:
    def __init__(self, dut, rnd, skew):
        self.dut, self.rnd, self.skew = dut, rnd, skew
        self.ui, self.uio, self.drive = 0, 0, False

    @staticmethod
    def bits(sig, what, need=0xFF):
        """A signal as an int, X/Z bits as 0; any X/Z bit inside `need` fails (gate level starts unreset flops at X:
        harmless where the host does not look, a bug where it does)."""
        s = str(sig.value)
        v = bad = 0
        for c in s:
            v, bad = (v << 1) | (c == "1"), (bad << 1) | (c not in "01")
        if bad & need:
            raise AssertionError(f"{what} = {s}: undefined bits where the host reads")
        return v

    def pad(self):
        """uio as the pins see it: the chip's byte where it drives (uio_oe), the host's where it drives, else 0."""
        oe = self.bits(self.dut.uio_oe, "uio_oe")
        out = self.bits(self.dut.uio_out, "uio_out", need=oe)
        host = self.uio if self.drive else 0
        if self.drive and oe:
            raise AssertionError(f"uio contention: host drives {self.uio:#04x} while uio_oe = {oe:#04x}")
        return (out & oe) | (host & ~oe & 0xFF)

    async def pins(self, ui, uio, drive):
        rising = (ui & 0x80) and not (self.ui & 0x80)
        self.ui = ui
        self.dut.ui_in.value = ui
        if rising and self.skew:                          # the strobe first, the data one clock late
            self.drive = False
            self.dut.uio_in.value = self.rnd.getrandbits(8)
            await RisingEdge(self.dut.clk); await Timer(1, "ns")
        self.uio, self.drive = uio, drive
        self.dut.uio_in.value = uio if drive else 0

    async def wait(self, n):
        await ClockCycles(self.dut.clk, n + self.rnd.randint(0, 2))
        await Timer(1, "ns")

    async def sample(self):
        await ReadOnly()
        uo = self.bits(self.dut.uo_out, "uo_out", need=0x0F)          # the handshake bits always
        if uo & 1:
            uo = self.bits(self.dut.uo_out, "uo_out", need=0xFF)      # and the symbol once a response is flagged
        v = (uo, self.pad())
        await Timer(1, "ns")
        return v


async def run_async(gen, b):
    try:
        op = next(gen)
        while True:
            if op[0] == "pins":
                await b.pins(op[1], op[2], op[3]); op = gen.send(None)
            elif op[0] == "wait":
                await b.wait(op[1]); op = gen.send(None)
            else:
                op = gen.send(await b.sample())
    except StopIteration as e:
        return e.value


@cocotb.test()
async def replay_traces(dut):
    paths = [os.environ["MSAC_TRACE"]] if os.environ.get("MSAC_TRACE") else sorted(glob.glob(os.path.join(HERE, "vectors", "*.txt")))
    max_syms = int(os.environ.get("MSAC_MAX_SYMS", "0")) or None
    skew = os.environ.get("MSAC_SKEW", "1") != "0"
    rnd = random.Random(int(os.environ.get("MSAC_SEED", "1")))
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    total = 0
    for path in paths:
        n = await run_async(msac_host.replay(path, max_syms), SimPins(dut, rnd, skew))
        dut._log.info(f"OK: {n} symbols over the pins ({os.path.basename(path)}, skew {skew})")
        total += n
    dut._log.info(f"OK: {total} symbols over the pins in {len(paths)} trace(s)")
