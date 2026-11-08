#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Random-stream fuzz traces for the chip (2026-11-08): random tiles (random bytes, random lengths down to zero, random
disable_cdf_update) with random request sequences (kinds, n, rows, counters), expected results from msac_model, written
in the golden-trace format so test/test.py (MSAC_TRACE=...) and the twins replay and check them with the same code as
the dav1d traces. Unlike real streams these run far past the end of the data (zero fill, eos) and change the row shape
every symbol.

    python3 test/fuzz_trace.py --tiles 40 --seed 1 out.txt
"""
import argparse
import random

import msac_host
import msac_model

ADAPT, BOOL, EQUI = msac_host.KIND_ADAPT, msac_host.KIND_BOOL, msac_host.KIND_EQUI


def rand_request(rng, prev):
    """A random request; one in three keeps the previous row shape (the chip keeps the written row, not the adapted
    one, so this exercises re-use of a row register across symbols)."""
    if prev is not None and rng.random() < 0.33:
        return prev
    kind = rng.choice([ADAPT] * 6 + [BOOL, EQUI])
    if kind == ADAPT:
        n = rng.choice([1, 1, 2, 3, 4, 7, 15, rng.randint(1, 15)])
        if rng.random() < 0.15:                        # crowded rows: tiny probabilities, entries near the ends
            lo = rng.randint(1, 64)
            row = sorted(rng.sample(range(32768 - lo - n, 32768), n), reverse=True) if rng.random() < 0.5 \
                else sorted(rng.sample(range(1, lo + n + 1), n), reverse=True)
        else:
            row = sorted(rng.sample(range(1, 32768), n), reverse=True)
        cnt = rng.choice([0, 1, 2, 5, 15, 16, 31, 32, rng.randint(0, 32)])
    elif kind == BOOL:
        n, row, cnt = 1, [rng.choice([1, 2, 16384, 32766, 32767, rng.randint(1, 32767)])], 0
    else:
        n, row, cnt = 1, [], 0
    return kind, n, tuple(row), cnt


def gen(rng, tiles, out):
    total = 0
    for _ in range(tiles):
        dis = rng.random() < 0.15
        nreq = rng.choice([1, 2, 5, 20, 100, 400, rng.randint(1, 500)])
        reqs, prev = [], None
        for _ in range(nreq):
            prev = rand_request(rng, prev)
            reqs.append(prev)
        # pass 1: how many bytes these requests pull from an endless stream; then the tile's length: usually just
        # enough (the chip prefetches at most ~4 bytes into its window and 4 into the FIFO, so the host can always
        # deliver up to +4 and the replay checker sees every byte consumed), sometimes short (zero fill past the end)
        full = bytes(rng.getrandbits(8) for _ in range(4096))
        m = msac_model.Msac(full, disable_cdf_update=dis)
        for kind, n, row, cnt in reqs:
            m.request(kind, n, list(row) if row else [0], cnt)
        need = (m.bitpos + 7) // 8
        r = rng.random()
        length = min(need + rng.randint(0, 4), 4096) if r < 0.7 else (0 if r < 0.75 else rng.randint(0, need))
        data = full[:length]
        out.write(f"T {length} {int(dis)}" + (f" {data.hex()}" if length else "") + "\n")
        m = msac_model.Msac(data, disable_cdf_update=dis)
        for kind, n, row, cnt in reqs:
            sym, rng_, post, postcnt = m.request(kind, n, list(row) if row else [0], cnt)
            if kind == ADAPT:
                line = f"S A {n} {sym} {rng_} {cnt} " + " ".join(str(v) for v in row)
                if not dis:
                    line += " " + " ".join(str(v) for v in post) + f" {postcnt}"
            elif kind == BOOL:
                line = f"S B {row[0]} {sym} {rng_}"
            else:
                line = f"S E {sym} {rng_}"
            out.write(line + "\n")
            total += 1
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--tiles", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    with open(a.out, "w") as f:
        f.write(f"# fuzz trace: seed {a.seed}, {a.tiles} tiles (test/fuzz_trace.py)\n")
        n = gen(rng, a.tiles, f)
    chk = msac_model.check_trace(a.out)
    print(f"{a.out}: {n} symbols in {a.tiles} tiles, model re-check {chk}")


if __name__ == "__main__":
    main()
