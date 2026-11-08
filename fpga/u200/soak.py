#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Host-sequence soak of the twin (2026-11-08). Random interleavings of pin resets, inits, self-tests (the five fixed
cases and random request shapes checked against msac_model), golden-trace replays cut short at random points, stray
register writes, stray requests, abandoned self-tests and random reads, each activity with its own oracle. It looks
for state a host sequence leaves behind that the next activity trips over: the class of the 2026-11-07 bug (the first
self-test after a host init decoded from byte 1), which ordered tests cannot see.

    python3 fpga/u200/soak.py [--dev /dev/xdma0_user] [--base 0] [--minutes 30] [--seed N] [--model-only 50]

Stops at the first failure and prints the activity sequence that led there (the seed reproduces it)."""
import argparse
import glob
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "kv260"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "test"))
import msac_host  # noqa: E402
import msac_model  # noqa: E402

ADAPT, BOOL, EQUI = msac_host.KIND_ADAPT, msac_host.KIND_BOOL, msac_host.KIND_EQUI
VECTORS = sorted(glob.glob(os.path.join(HERE, "..", "..", "test", "vectors", "*.txt")))


def rand_case(rng):
    """A random self-test request: valid row for the kind, realistic leftovers (stale 15-bit entries) beyond n."""
    kind = rng.choice([ADAPT] * 4 + [BOOL, EQUI])
    junk = [rng.randint(0, 32767) if rng.random() < 0.5 else 0 for _ in range(15)]
    if kind == ADAPT:
        n = rng.randint(1, 15)
        row = sorted(rng.sample(range(1, 32768), n), reverse=True) + junk[n:]
        cnt = rng.choice([0, 1, 2, 5, 15, 16, 31, 32, rng.randint(0, 32)])
    elif kind == BOOL:
        n, row, cnt = 1, [rng.randint(1, 32767)] + junk[1:], 0
    else:
        n, row, cnt = 1, junk, 0
    upd = rng.randint(0, 1)
    r = rng.random()
    count = 65536 if r < 0.02 else rng.choice([1, 2, 3, rng.randint(4, 300), rng.randint(300, 5000), rng.randint(5000, 20000)])
    name = f"rand kind{kind} n{n} cnt{cnt} upd{upd} N{count}"
    return name, kind, n, row, cnt, upd, count


class Soak:
    def __init__(self, pins, rng, log):
        self.p, self.rng, self.log = pins, rng, log
        self.counts = {}
        self.upd = 2
        self.stray = False        # a stray request may still be in flight: its late response adds one to the symbol counter

    def run(self, gen):
        return msac_host.run(gen, self.p)

    def note(self, line):
        self.log.append(line)
        print(line, flush=True)

    # ---- activities: each returns None or raises on a failed oracle ----
    def act_reset(self):
        self.p.reset()
        self.stray = False
        self.note("reset")

    def act_init(self):
        bits = 0x01 | self.rng.choice([0, 2]) | self.rng.choice([0, 0, 8])
        self.upd = bits & 2
        self.run(msac_host.write(0x20, bits))
        self.stray = False
        self.note(f"init 0x20={bits:#04x}")

    def bist(self, case, check_counters):
        name, kind, n, row, cnt, upd, count = case
        exp = msac_model.bist_signature(kind, n, row, cnt, upd, count)
        if check_counters:
            self.run(msac_host.clear_counters())
        got = self.run(msac_host.self_test(kind, n, row, cnt, upd, count & 0xFFFF))
        line = f"bist {name}: {got:#010x} model {exp:#010x}"
        if check_counters:
            syms, clks = self.run(msac_host.read_counters())
            line += f" syms {syms} clks {clks}"
            allowed = {count, count + 1} if self.stray else {count}
            if syms not in allowed or not (2 * count <= clks <= 40 * count + 5_000_000):   # clks run from the clear: host setup included
                self.note("FAIL " + line)
                raise AssertionError(f"counters: syms {syms} (expected {count}), clks {clks}")
            if syms == count + 1:
                line += " (+1: the stray request's late response, seen 2026-11-08)"
        self.stray = False
        if got != exp:
            self.note("FAIL " + line)
            raise AssertionError(f"signature {got:#010x} != model {exp:#010x} ({name})")
        self.note(line)

    def act_bist_fixed(self):
        self.bist(self.rng.choice(msac_model.BIST_CASES), self.rng.random() < 0.5)

    def act_bist_rand(self):
        self.bist(rand_case(self.rng), self.rng.random() < 0.5)

    def act_replay(self):
        path = self.rng.choice(VECTORS)
        full = self.rng.random() < 0.08
        max_syms = None if full else self.rng.choice([1, 2, 3, 7, 20, 60, 200, 600, self.rng.randint(1, 1500)])
        port = self.rng.random() < 0.3
        gen = msac_host.replay_port(path, max_syms) if port else msac_host.replay(path, max_syms)
        try:
            n = self.run(gen)
        except msac_host.Mismatch as e:
            self.note(f"FAIL replay {os.path.basename(path)} max {max_syms} port {port}: {e}")
            raise
        if self.p.contention():
            self.note(f"FAIL replay {os.path.basename(path)}: uio contention")
            raise AssertionError("uio contention")
        self.stray = False
        self.note(f"replay {os.path.basename(path)} {n} syms{' port' if port else ''}{' full' if full else ''}")

    def act_stray_writes(self):
        k = self.rng.randint(1, 12)
        addrs = []
        for _ in range(k):
            a = self.rng.choice(list(range(0x00, 0x20)) + [0x21, 0x21, 0x22, 0x23, 0x23, 0x24, 0x26, 0x27, 0x1E, 0x1F])
            v = self.rng.randint(0, 255)
            if a == 0x24:
                v &= 3
            if a == 0x1E:
                v = (self.rng.randint(0, 2) << 4) | self.rng.randint(1, 15)
            self.run(msac_host.write(a, v))
            addrs.append(f"{a:#04x}={v:#04x}")
        self.note("stray writes " + " ".join(addrs))

    def act_stray_req(self):
        # a request out of the blue on whatever the registers hold (control bits 1/3 random), then nothing: the next
        # activity has to cope with a core that may be waiting for stream bytes
        bits = 0x04 | self.rng.choice([0, 2]) | self.rng.choice([0, 8])
        self.upd = bits & 2
        self.run(msac_host.write(0x20, bits))
        self.run(("wait", self.rng.randint(0, 200)) for _ in range(1))
        s = self.run(msac_host.status())
        self.stray = True
        self.note(f"stray request 0x20={bits:#04x} -> status {s:#04x}")
        # a request issued while the core is uninitialised parks in the wrapper (req_pending) and is served when the
        # next init completes, on the new tile's first bits (soak 2026-11-08: a replay right after it failed at its
        # first symbol). Documented, not fixed in silicon: so the next thing here is a self-test (+1 tolerated) or a reset
        if self.rng.random() < 0.6:
            self.act_bist_rand()
        else:
            self.p.reset()
            self.stray = False
            self.note("reset (after the stray request)")

    def act_abandon_bist(self):
        name, kind, n, row, cnt, upd, count = rand_case(self.rng)
        count = count & 0xFFFF

        def start():
            for k, c in enumerate(row):
                yield from msac_host.write(2 * k, c & 0xFF)
                yield from msac_host.write(2 * k + 1, (c >> 8) & 0xFF)
            yield from msac_host.write(0x1E, (kind << 4) | n)
            yield from msac_host.write(0x1F, cnt)
            yield from msac_host.write(0x20, 0x02 if upd else 0x00)
            yield from msac_host.write(0x26, count & 0xFF)
            yield from msac_host.write(0x27, (count >> 8) & 0xFF)
            yield from msac_host.write(0x25, 0x01)
            yield ("wait", self.rng.randint(0, 3000))
            st = yield from msac_host.read(0x74)
            return st

        st = self.run(start())
        self.p.reset()
        self.note(f"abandoned bist {name} (status {st:#04x} before the pin reset)")

    def act_reads(self):
        vals = []
        for _ in range(self.rng.randint(1, 10)):
            a = self.rng.choice(list(range(0x40, 0x4C)) + list(range(0x50, 0x6E)) + list(range(0x70, 0x75)) + [0x00, 0x7F])
            vals.append(f"{a:#04x}:{self.run(msac_host.read(a)):#04x}")
        self.run(("wait", self.rng.randint(0, 3000)) for _ in range(1))
        self.note("reads " + " ".join(vals))

    ACTS = [("reset", 6), ("init", 8), ("bist_fixed", 8), ("bist_rand", 14), ("replay", 18),
            ("stray_writes", 6), ("stray_req", 2), ("abandon_bist", 4), ("reads", 6)]

    def step(self):
        names = [a for a, w in self.ACTS for _ in range(w)]
        name = self.rng.choice(names)
        self.counts[name] = self.counts.get(name, 0) + 1
        getattr(self, "act_" + name)()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", default="/dev/xdma0_user")
    ap.add_argument("--base", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--model-only", type=int, default=0, help="no hardware: just generate N random cases and model them")
    a = ap.parse_args()
    seed = a.seed if a.seed is not None else random.randrange(1 << 31)
    rng = random.Random(seed)
    print(f"soak seed {seed}", flush=True)
    if a.model_only:
        t0 = time.time()
        for _ in range(a.model_only):
            name, kind, n, row, cnt, upd, count = rand_case(rng)
            sig = msac_model.bist_signature(kind, n, row, cnt, upd, count)
            print(f"model {name}: {sig:#010x}")
        print(f"MODEL_ONLY_DONE {a.model_only} cases in {time.time() - t0:.1f} s")
        return 0
    import twin  # noqa: E402  (the hardware backend)
    p = twin.TwinPins(a.base, a.dev)
    v = p.check_bitstream()
    print(f"twin at {a.dev} + {a.base:#x}: {v:#010x}", flush=True)
    log = []
    s = Soak(p, rng, log)
    s.act_reset()
    t0 = time.time()
    steps = 0
    try:
        while time.time() - t0 < a.minutes * 60:
            s.step()
            steps += 1
    except (AssertionError, msac_host.Mismatch) as e:
        print(f"\nFAIL after {steps} activities, {time.time() - t0:.0f} s, seed {seed}: {e}")
        print("last 40 activities:")
        for line in log[-40:]:
            print("  " + line[:200])
        print(f"SOAK_DONE steps={steps} fails=1 seed={seed}")
        return 1
    print(f"\nPASS {steps} activities in {time.time() - t0:.0f} s, seed {seed}: " +
          " ".join(f"{k}={v}" for k, v in sorted(s.counts.items())))
    print(f"SOAK_DONE steps={steps} fails=0 seed={seed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
