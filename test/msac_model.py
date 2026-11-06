# SPDX-License-Identifier: Apache-2.0
"""A Python model of the chip's symbol decoder, from the AV1 specification (section 8.2), independent of the RTL:
the decoding routines of openav1's tb/tile_model.py SymbolDecoder, taking requests the way the chip does (rows of
inverted CDF entries, 32768 - cdf, as dav1d and the chip store them; a separate adaptation counter).

Used two ways: checked against the dav1d golden traces (python3 msac_model.py vectors/*.txt), then as the golden
reference of the chip's self-test signature (bist_signature), which no trace covers.
"""
import sys

import msac_host

KIND_ADAPT, KIND_BOOL, KIND_EQUI = msac_host.KIND_ADAPT, msac_host.KIND_BOOL, msac_host.KIND_EQUI


def floor_log2(x):
    return x.bit_length() - 1


class Msac:
    """The spec's symbol decoder over a byte source (a bytes object, or a callable returning byte k)."""

    def __init__(self, data, size=None, disable_cdf_update=False):
        self.get = data if callable(data) else (lambda k: data[k] if k < len(data) else 0)
        sz = len(data) if size is None else size
        self.bitpos = 0
        self.disable_cdf_update = disable_cdf_update
        num_bits = min(sz * 8, 15)
        buf = self._f(num_bits)
        self.value = ((1 << 15) - 1) ^ (buf << (15 - num_bits))
        self.range = 1 << 15
        self.max_bits = 8 * sz - 15

    def _f(self, n):
        x = 0
        for _ in range(n):
            bit = (self.get(self.bitpos >> 3) >> (7 - (self.bitpos & 7))) & 1
            x = (x << 1) | bit
            self.bitpos += 1
        return x

    def read_symbol(self, cdf, adapt):
        """cdf: the spec's (non-inverted) array of N values ending in 32768, plus the counter; adapted in place."""
        N = len(cdf) - 1
        cur = self.range
        symbol = -1
        while True:
            symbol += 1
            prev = cur
            fval = (1 << 15) - cdf[symbol]
            cur = ((self.range >> 8) * (fval >> 6)) >> 1
            cur += 4 * (N - symbol - 1)
            if not (self.value < cur):
                break
        self.range = prev - cur
        self.value -= cur
        bits = 15 - floor_log2(self.range)
        self.range <<= bits
        num_bits = min(bits, max(0, self.max_bits))
        new_data = self._f(num_bits)
        self.value = (new_data << (bits - num_bits)) ^ (((self.value + 1) << bits) - 1)
        self.max_bits -= bits
        if adapt and not self.disable_cdf_update:
            rate = 3 + (cdf[N] > 15) + (cdf[N] > 31) + min(floor_log2(N), 2)
            tmp = 0
            for i in range(N - 1):
                tmp = (1 << 15) if i == symbol else tmp
                if tmp < cdf[i]:
                    cdf[i] -= (cdf[i] - tmp) >> rate
                else:
                    cdf[i] += (tmp - cdf[i]) >> rate
            cdf[N] += 1 if cdf[N] < 32 else 0
        return symbol

    def request(self, kind, n, row, cnt):
        """One chip request. Returns (symbol, range, adapted inverted row, adapted counter); the last two are None
        for the non-adaptive kinds. row: inverted entries (n of them for adaptive requests, the probability for
        bool, ignored for equiprobable)."""
        if kind == KIND_ADAPT:
            cdf = [32768 - v for v in row[:n]] + [32768, cnt]
            sym = self.read_symbol(cdf, adapt=True)
            return sym, self.range, [32768 - v for v in cdf[:n]], cdf[-1]
        f = row[0] if kind == KIND_BOOL else 16384
        sym = self.read_symbol([32768 - f, 32768, 0], adapt=False)
        return sym, self.range, None, None


def check_trace(path):
    """Replay a dav1d golden trace through the model; returns the symbol count, raises on the first mismatch."""
    done = 0
    for ti, (data, dis, reqs) in enumerate(msac_host.parse_trace(path)):
        m = Msac(bytes(data), disable_cdf_update=bool(dis))
        for ri, (kind, n, cdf, cnt, exp_sym, exp_rng, post, postcnt) in enumerate(reqs):
            sym, rng, row, c = m.request(kind, n, list(cdf), cnt)
            if (sym, rng) != (exp_sym, exp_rng):
                raise AssertionError(f"{path} tile {ti} req {ri}: model ({sym}, {rng}) != trace ({exp_sym}, {exp_rng})")
            if post is not None and (tuple(row) != tuple(post) or c != postcnt):
                raise AssertionError(f"{path} tile {ti} req {ri}: model row {row} cnt {c} != trace {list(post)} {postcnt}")
            done += 1
    return done


# ---------------------------------------------------------------- the self-test reference

LFSR_SEED = 0xACE1


def lfsr_bytes():
    """The self-test stream: byte k is the low byte of the 16-bit LFSR after 8k steps from the seed
    (x^16 + x^14 + x^13 + x^11 + 1, Fibonacci form, shifting left; tt_um_jonahss_msac's lfsr_next)."""
    x = LFSR_SEED
    while True:
        yield x & 0xFF
        for _ in range(8):
            x = ((x << 1) & 0xFFFF) | (((x >> 15) ^ (x >> 13) ^ (x >> 12) ^ (x >> 10)) & 1)


class LazyBytes:
    def __init__(self, gen):
        self.gen, self.buf = gen, []

    def __call__(self, k):
        while len(self.buf) <= k:
            self.buf.append(next(self.gen))
        return self.buf[k]


def sig_step(sig, word):
    fb = ((sig >> 31) ^ (sig >> 21) ^ (sig >> 1) ^ sig) & 1
    return (((sig << 1) & 0xFFFFFFFF) | fb) ^ word


def bist_signature(kind, n, row15, cnt, upd, count):
    """The chip's self-test: `count` requests of the same (kind, n, row, cnt) against the LFSR stream, each response
    folded into the signature. row15: the 15 inverted entries of the chip's row register (unused entries matter:
    the chip's adapted row keeps them, and the fold covers all 15)."""
    m = Msac(LazyBytes(lfsr_bytes()), size=1 << 30, disable_cdf_update=not upd)
    sig = 0xFFFFFFFF
    for _ in range(count):
        sym, rng, row, c = m.request(kind, n, list(row15), cnt)
        if kind == KIND_ADAPT:
            full = list(row) + list(row15[n:])          # the chip writes back all 15 entries; beyond n, the request's
            fold = 0
            for v in full:
                fold ^= v
            word = ((fold << 16) | rng) ^ ((c << 4) | sym)
        else:
            word = rng ^ sym
        sig = sig_step(sig, word & 0xFFFFFFFF)
    return sig


# the self-test cases the cocotb test and the KV260 twin both run
BIST_CASES = [
    # (name, kind, n, row15 (inverted entries), cnt, cdf_update_en, count)
    ("adaptive n=15", KIND_ADAPT, 15,
     [32768 - (k + 1) * 2048 + 64 * k for k in range(15)], 5, 1, 3000),
    ("adaptive n=3 counter 31", KIND_ADAPT, 3, [24000, 15000, 6000] + [0] * 12, 31, 1, 2000),
    ("adaptive n=1, updates off", KIND_ADAPT, 1, [12000] + [0] * 14, 0, 0, 2000),
    ("bool f=20000", KIND_BOOL, 1, [20000] + [0] * 14, 0, 1, 2000),
    ("equiprobable", KIND_EQUI, 1, [0] * 15, 0, 1, 2000),
]


if __name__ == "__main__":
    total = 0
    for p in sys.argv[1:]:
        n = check_trace(p)
        print(f"model == dav1d: {n} symbols ({p})")
        total += n
    print(f"model == dav1d: {total} symbols in {len(sys.argv) - 1} trace(s)")
