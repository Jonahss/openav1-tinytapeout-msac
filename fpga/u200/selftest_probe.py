#!/usr/bin/env python3
"""Probe the first-run self-test failure (2026-11-07): after a pin reset, which BIST runs fail and does a soft reset
   bring the failure back?  python3 fpga/u200/selftest_probe.py [--dev /dev/xdma0_user] [--base 0]"""
import argparse, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "kv260")); sys.path.insert(0, os.path.join(HERE, "..", "..", "test"))
import twin, msac_host, msac_model  # noqa: E402
ap = argparse.ArgumentParser(); ap.add_argument("--dev", default="/dev/xdma0_user"); ap.add_argument("--base", type=lambda x: int(x, 0), default=0)
a = ap.parse_args()
p = twin.TwinPins(a.base, a.dev); v = p.check_bitstream()
cases = {c[0]: c for c in msac_model.BIST_CASES}
def run(name):
    _, kind, n, row, cnt, upd, count = cases[name]
    exp = msac_model.bist_signature(kind, n, row, cnt, upd, count)
    msac_host.run(msac_host.clear_counters(), p)
    got = msac_host.run(msac_host.self_test(kind, n, row, cnt, upd, count), p)
    syms, clks, busy = msac_host.run(msac_host.read_counters(), p)
    print(f"{'PASS' if got == exp and syms == count else 'FAIL'} {name}: {got:#010x} (model {exp:#010x}) {syms} syms", flush=True)
def reset():
    p.reset(); msac_host.run(msac_host.write(0x20, 0x01), p); print("-- pin reset", flush=True)
print(f"twin at {a.dev} + {a.base:#x}: {v:#010x}")
reset(); run("bool f=20000"); run("adaptive n=15"); run("adaptive n=15")          # A: a different kind first
reset(); run("adaptive n=15"); run("adaptive n=15")                               # B: soft reset, then the case twice
reset(); run("equiprobable"); run("adaptive n=3 counter 31"); run("adaptive n=15") # C: two others first
print("PROBE_DONE")
