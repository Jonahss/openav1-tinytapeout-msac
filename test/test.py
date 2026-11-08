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
import msac_model

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


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


def sim_pins(dut):
    skew = os.environ.get("MSAC_SKEW", "1") != "0"
    return SimPins(dut, random.Random(int(os.environ.get("MSAC_SEED", "1"))), skew), skew


@cocotb.test()
async def replay_traces(dut):
    paths = [os.environ["MSAC_TRACE"]] if os.environ.get("MSAC_TRACE") else sorted(glob.glob(os.path.join(HERE, "vectors", "*.txt")))
    max_syms = int(os.environ.get("MSAC_MAX_SYMS", "0")) or None
    skew = os.environ.get("MSAC_SKEW", "1") != "0"
    rnd = random.Random(int(os.environ.get("MSAC_SEED", "1")))
    await reset(dut)
    total = 0
    for path in paths:
        n = await run_async(msac_host.replay(path, max_syms), SimPins(dut, rnd, skew))
        dut._log.info(f"OK: {n} symbols over the pins ({os.path.basename(path)}, skew {skew})")
        total += n
    dut._log.info(f"OK: {total} symbols over the pins in {len(paths)} trace(s)")


@cocotb.test()
async def row_port_and_counters(dut):
    """The same golden replay with every CDF row written through the row port (0x22/0x23), then the counters:
    exactly one symbol per request, clocks = the simulated clocks between clear and snapshot."""
    await reset(dut)
    p, skew = sim_pins(dut)
    path = os.environ.get("MSAC_TRACE") or os.path.join(HERE, "vectors", "argon10001_f5_2tiles.txt")
    ticks = [0]

    async def count_clocks():
        while True:
            await RisingEdge(dut.clk)
            ticks[0] += 1

    cocotb.start_soon(count_clocks())
    await run_async(msac_host.clear_counters(), p)
    t0 = ticks[0]
    n = await run_async(msac_host.replay_port(path), p)
    t1 = ticks[0]
    syms, clks = await run_async(msac_host.read_counters(), p)
    dut._log.info(f"OK: {n} symbols through the row port; counters: {syms} symbols, {clks} clocks "
                  f"(sim {t1 - t0} clocks)")
    assert syms == n, f"symbol counter {syms} != {n}"
    # clear and snapshot each act a fixed delay after their strobe; the snapshot write comes after t1
    assert t1 - t0 <= clks <= t1 - t0 + 200, f"clock counter {clks} vs sim {t1 - t0}"
    await run_async(msac_host.clear_counters(), p)
    syms2, clks2 = await run_async(msac_host.read_counters(), p)
    assert syms2 == 0 and clks2 < 100, f"after clear: {syms2} symbols, {clks2} clocks"




@cocotb.test()
async def self_test_signatures(dut):
    """The self-test against the Python model (msac_model.py, itself checked against the dav1d traces): several
    request shapes, each signature must match; the symbol counter must count the self-test's symbols; and a host
    replay must still work after a self-test (init again)."""
    await reset(dut)
    p, skew = sim_pins(dut)
    for name, kind, n, row, cnt, upd, count in msac_model.BIST_CASES:
        await run_async(msac_host.clear_counters(), p)
        got = await run_async(msac_host.self_test(kind, n, row, cnt, upd, count), p)
        exp = msac_model.bist_signature(kind, n, row, cnt, upd, count)
        syms, clks = await run_async(msac_host.read_counters(), p)
        dut._log.info(f"self-test {name}: {count} symbols, signature {got:#010x} (model {exp:#010x}), "
                      f"counter {syms}, {clks} clocks")
        assert got == exp, f"self-test {name}: signature {got:#010x} != model {exp:#010x}"
        assert syms == count, f"self-test {name}: symbol counter {syms} != {count}"
    path = os.path.join(HERE, "vectors", "argon10001_f5_2tiles.txt")
    n = await run_async(msac_host.replay(path), p)
    dut._log.info(f"OK: {n} symbols replayed after the self-tests")


@cocotb.test()
async def self_test_after_init(dut):
    """Hardware-found 2026-11-07 (U200 twin): a host init (register 0x20) right before the first self-test makes that
    self-test's signature wrong; the self-test's own init then discards a byte the LFSR had already stepped past."""
    await reset(dut)
    p, skew = sim_pins(dut)
    await run_async(msac_host.write(0x20, 0x01), p)
    name, kind, n, row, cnt, upd, count = msac_model.BIST_CASES[0]
    got = await run_async(msac_host.self_test(kind, n, row, cnt, upd, count), p)
    exp = msac_model.bist_signature(kind, n, row, cnt, upd, count)
    dut._log.info(f"self-test after init {name}: signature {got:#010x} (model {exp:#010x}, hardware showed 0x458aa45c)")
    assert got == exp, f"self-test after init: {got:#010x} != model {exp:#010x}"


@cocotb.test()
async def request_before_init_parks(dut):
    """Soak-found 2026-11-08 (U200 twin, random host sequences): a request issued while the core is not ready (before
    the first init after reset, or after an init while the stream FIFO is still empty and eos is clear) parks in the
    wrapper and is served as soon as the core is ready, which can be after a later init or self-test, on that new
    data. The datasheet says init first, requests one at a time, after the first bytes or with eos. This pins the
    behaviour: a following self-test's signature is untouched and the symbol counter shows the extra response."""
    await reset(dut)
    p, skew = sim_pins(dut)
    await run_async(msac_host.write(0x20, 0x0E), p)                    # request + eos + updates: no init, no bytes
    s = await run_async(msac_host.status(), p)
    assert s & 0x02 == 0, f"req_ready set with a request parked: status {s:#04x}"
    await run_async(msac_host.clear_counters(), p)
    name, kind, n, row, cnt, upd, count = msac_model.BIST_CASES[2]
    got = await run_async(msac_host.self_test(kind, n, row, cnt, upd, count), p)
    exp = msac_model.bist_signature(kind, n, row, cnt, upd, count)
    syms, clks = await run_async(msac_host.read_counters(), p)
    dut._log.info(f"request before init, then self-test {name}: signature {got:#010x} (model {exp:#010x}), "
                  f"counter {syms} = {count} + the parked request")
    assert got == exp, f"signature {got:#010x} != model {exp:#010x}"
    assert syms == count + 1, f"symbol counter {syms}: expected {count} + 1 (the parked request, served after the self-test)"
    s = await run_async(msac_host.status(), p)
    assert s & 0x03 == 0x03, f"after the parked request: status {s:#04x} (resp_valid and req_ready expected)"
    n_syms = await run_async(msac_host.replay(os.path.join(HERE, "vectors", "argon10001_f5_2tiles.txt"), 50), p)
    dut._log.info(f"OK: {n_syms} symbols replayed after that (the replay inits first)")
