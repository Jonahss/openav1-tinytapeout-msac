# SPDX-License-Identifier: Apache-2.0
"""The host side of tt_um_jonahss_msac, written once and run three ways: by the cocotb test (test/test.py), by the
KV260 twin (fpga/kv260/twin.py: the same tt_um module behind an AXI GPIO), and later by the demo board.

The protocol is a generator of pin operations, so it carries no notion of simulation time or of a bus:

    ("pins", ui, uio, drive)   set ui_in[7:0], and uio to `uio` if drive else let go of uio (the chip may drive it)
    ("wait", n)                let at least n chip clocks pass
    ("sample",)                -> (uo_out, uio) as seen on the pins

A backend runs a generator with `run(gen, backend)`, sending each ("sample",) result back in. The pin timing is
the one tt_um_jonahss_msac's header asks for: address first with the strobe low, then strobe + data in one pin
write, held >= 4 clocks, released in one write, held >= 4 clocks; reads wait >= 4 clocks after the address.

The trace format is the one openav1's tb/test_msac.py replays (tools/trace.sh <stream> <out> 1).
"""

KIND_ADAPT, KIND_BOOL, KIND_EQUI = 0, 1, 2
ST_RESP, ST_READY, ST_INREADY, ST_WB = 1, 2, 4, 8
HOLD = 4                       # chip clocks per pin phase (two synchroniser flops + two stable clocks)


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


# ---------------------------------------------------------------- pin-level primitives (generators)

def write(addr, value):
    yield ("pins", addr, 0, False)                       # address, strobe low, host off the bus
    yield ("wait", HOLD)
    yield ("pins", 0x80 | addr, value & 0xFF, True)      # strobe and data together
    yield ("wait", HOLD)
    yield ("pins", addr, 0, False)                       # release both
    yield ("wait", HOLD)


def read(addr):
    yield ("pins", addr, 0, False)
    yield ("wait", HOLD)
    uo, uio = yield ("sample",)
    return uio


def status():
    uo, uio = yield ("sample",)
    return uo


def read16(addr):
    lo = yield from read(addr)
    hi = yield from read(addr + 1)
    return lo | (hi << 8)


class Mismatch(AssertionError):
    pass


def replay(path, max_syms=None, progress=None, poll_limit=2000):
    """The golden-trace replay: every symbol, range, adapted row and counter checked. Returns the symbol count.
    progress(done, tile) is called once per 256 symbols and at every tile start."""
    yield ("pins", 0, 0, False)
    done = 0
    for ti, (data, dis, reqs) in enumerate(parse_trace(path)):
        if progress:
            progress(done, ti)
        upd = 0 if dis else 2
        yield from write(0x20, 0x01 | upd)                    # init: empties the byte FIFO, cdf_update_en per tile
        pending = list(data)

        def top_up():
            while pending:
                s = yield from status()
                if not (s & ST_INREADY):
                    return
                yield from write(0x21, pending.pop(0))
                if not pending:
                    yield from write(0x20, 0x08 | upd)        # the last byte went in: eos from now on

        yield from top_up()
        if not data:
            yield from write(0x20, 0x08 | upd)
        last_cdf = None
        for ri, (kind, n, cdf, cnt, exp_sym, exp_rng, post, postcnt) in enumerate(reqs):
            if cdf != last_cdf:
                for k, c in enumerate(cdf):
                    yield from write(2 * k, c & 0xFF)
                    yield from write(2 * k + 1, (c >> 8) & 0xFF)
                last_cdf = cdf                               # the chip keeps the written row, not the adapted one
            yield from write(0x1E, (kind << 4) | n)
            yield from write(0x1F, cnt)
            yield from write(0x20, 0x04 | upd | (0x08 if not pending else 0))
            for _ in range(poll_limit):                      # the core may stall on stream bytes: keep feeding
                yield from top_up()
                s = yield from status()
                if s & ST_RESP:
                    break
                yield ("wait", 1)
            else:
                raise Mismatch(f"tile {ti} req {ri}: no response (status {s:#04x})")
            sym = s >> 4
            if sym != exp_sym:
                raise Mismatch(f"tile {ti} req {ri}: sym {sym} != {exp_sym} (kind {kind} n {n})")
            for _ in range(poll_limit):
                s = yield from status()
                if s & ST_WB:
                    break
                yield ("wait", 1)
            else:
                raise Mismatch(f"tile {ti} req {ri}: no write-back (status {s:#04x})")
            rng = yield from read16(0x42)
            if rng != exp_rng:
                raise Mismatch(f"tile {ti} req {ri}: rng {rng} != {exp_rng}")
            if post is not None:
                got = []
                for k in range(len(post)):
                    got.append((yield from read16(0x50 + 2 * k)))
                if tuple(got) != post:
                    raise Mismatch(f"tile {ti} req {ri}: adapted row {got} != {list(post)}")
                gcnt = yield from read(0x44)
                if gcnt != postcnt:
                    raise Mismatch(f"tile {ti} req {ri}: cnt {gcnt} != {postcnt}")
            done += 1
            if progress and done % 256 == 0:
                progress(done, ti)
            if max_syms and done >= max_syms:
                return done
        if pending:
            raise Mismatch(f"tile {ti}: {len(pending)} stream bytes never consumed")
    return done


def run(gen, backend):
    """Drive a protocol generator with a synchronous backend (pins(ui, uio, drive), wait(n), sample())."""
    try:
        op = next(gen)
        while True:
            if op[0] == "pins":
                backend.pins(op[1], op[2], op[3]); op = gen.send(None)
            elif op[0] == "wait":
                backend.wait(op[1]); op = gen.send(None)
            else:
                op = gen.send(backend.sample())
    except StopIteration as e:
        return e.value
