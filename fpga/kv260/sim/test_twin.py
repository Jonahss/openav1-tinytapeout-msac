# SPDX-License-Identifier: Apache-2.0
"""The KV260 twin top in simulation, driven through fpga/kv260/twin.py's TwinPins (its GPIO bit packing, reset,
heartbeat check and contention flag), so the board script is exercised before the board."""
import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "test"))
import msac_host  # noqa: E402
import twin  # noqa: E402


class Reg:
    def __init__(self, sig, writable):
        self.sig, self.writable = sig, writable

    @property
    def value(self):
        return int(self.sig.value)

    @value.setter
    def value(self, v):
        assert self.writable
        self.sig.value = v


def sim_twin(dut):
    p = twin.TwinPins.__new__(twin.TwinPins)          # TwinPins without /dev/mem: the GPIO words go to the sim
    p.out, p.inp, p.ctl = Reg(dut.gpio_o, True), Reg(dut.gpio_i, False), 0
    return p


async def run_async(gen, p, dut):
    try:
        op = next(gen)
        while True:
            if op[0] == "pins":
                p.pins(op[1], op[2], op[3]); op = gen.send(None)
            elif op[0] == "wait":
                await ClockCycles(dut.chip_clk, op[1]); await Timer(1, "ns"); op = gen.send(None)
            else:
                op = gen.send(p.sample())
    except StopIteration as e:
        return e.value


@cocotb.test()
async def twin_replay(dut):
    cocotb.start_soon(Clock(dut.chip_clk, 20, unit="ns").start())
    dut.gpio_o.value = 0
    await ClockCycles(dut.chip_clk, 4)
    p = sim_twin(dut)
    v = p.raw()
    assert v >> 28 == 0xA, f"magic {v:#010x}"
    p.out.value = twin.CLR
    await ClockCycles(dut.chip_clk, 8)
    p.ctl = twin.RST_N | twin.ENA
    p.out.value = p.ctl
    await ClockCycles(dut.chip_clk, 8)
    assert not p.contention()
    # the contention flag works: drive uio while the chip drives it (strobe low)
    p.pins(0x40, 0x55, True)
    await ClockCycles(dut.chip_clk, 2)
    assert p.contention(), "contention not flagged"
    p.out.value = p.ctl | twin.CLR
    await ClockCycles(dut.chip_clk, 2)
    p.out.value = p.ctl
    await ClockCycles(dut.chip_clk, 2)
    assert not p.contention()
    paths = [os.environ["MSAC_TRACE"]] if os.environ.get("MSAC_TRACE") else [
        os.path.join(HERE, "..", "..", "..", "test", "vectors", "argon10001_f5_2tiles.txt")]
    max_syms = int(os.environ.get("MSAC_MAX_SYMS", "0")) or None
    total = 0
    for path in paths:
        n = await run_async(msac_host.replay(path, max_syms), p, dut)
        assert not p.contention(), "uio contention during the replay"
        dut._log.info(f"OK: {n} symbols through the twin ({os.path.basename(path)})")
        total += n
    dut._log.info(f"OK: {total} symbols through the twin top")
