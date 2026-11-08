#!/usr/bin/env python3
"""All self-test cases, each twice, without stopping at a failure (the twin's --self-test returns at the first one).
   python3 fpga/u200/selftest_all.py [--dev /dev/xdma0_user] [--base 0]     (2026-11-07, the U200 twin)"""
import argparse, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "kv260")); sys.path.insert(0, os.path.join(HERE, "..", "..", "test"))
import twin, msac_host, msac_model  # noqa: E402
ap = argparse.ArgumentParser(); ap.add_argument("--dev", default="/dev/xdma0_user"); ap.add_argument("--base", type=lambda x: int(x, 0), default=0)
ap.add_argument("--repeat", type=int, default=2); a = ap.parse_args()
p = twin.TwinPins(a.base, a.dev); v = p.check_bitstream(); p.reset()
print(f"twin at {a.dev} + {a.base:#x}: {v:#010x}")
msac_host.run(msac_host.write(0x20, 0x01), p)
fails = 0
for name, kind, n, row, cnt, upd, count in msac_model.BIST_CASES:
    exp = msac_model.bist_signature(kind, n, row, cnt, upd, count)
    for r in range(a.repeat):
        msac_host.run(msac_host.clear_counters(), p)
        got = msac_host.run(msac_host.self_test(kind, n, row, cnt, upd, count), p)
        syms, clks, busy = msac_host.run(msac_host.read_counters(), p)
        ok = got == exp and syms == count; fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} self-test {name} run {r + 1}: {got:#010x} (model {exp:#010x}), {syms} symbols in {clks} clocks, busy {busy}", flush=True)
print(f"SELFTEST_ALL_DONE fails={fails}")
sys.exit(1 if fails else 0)
