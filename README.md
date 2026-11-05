![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg)

# openav1 msac on Tiny Tapeout: the AV1 symbol decoder

The arithmetic (symbol) decoder of [openav1](https://github.com/Jonahss/openav1), an open-source AV1 hardware video
decoder, on a [Tiny Tapeout](https://tinytapeout.com) chip (SkyWater 130 nm, 8x2 tiles). `src/msac.sv` is the same
file the FPGA decoder runs, proven there bit-exact over the Argon conformance streams; here it sits behind a
byte-wide register window on the 24 pins. Datasheet: [docs/info.md](docs/info.md).

Written by Claw, the AI agent that wrote openav1's RTL, models and test benches; Jonah Stiennon runs the project.

## What is here

| path | what |
|---|---|
| `src/msac.sv`, `src/cdf_map_pkg.sv` | the core, unmodified from openav1 |
| `src/tt_um_jonahss_msac.sv` | the pin wrapper: register window, input synchronisers, 4-byte stream FIFO |
| `test/msac_host.py` | the host protocol, written once as a generator of pin operations |
| `test/test.py` | cocotb: replays dav1d golden symbol traces over the pins through a deliberately sloppy host (jittered waits, data a clock after the strobe) and checks every symbol, range, adapted CDF row and counter |
| `test/vectors/` | 40,360 symbols: 8-bit 4:4:4 (2 tiles), 10-bit, Argon test10001 (4 tiles with `disable_cdf_update`, tile reordering; 2 tiles) |
| `fpga/kv260/` | the chip's twin on a Kria KV260: this exact `tt_um` module behind its 24 pins on an AXI GPIO, on its own clock, driven by `twin.py` with the same host protocol |

## Run the tests

```sh
cd test
make                        # RTL, icarus >= 12 (make SIM=verilator is ~4x faster)
make GATES=yes              # gate level: copy the hardened netlist to test/gate_level_netlist.v first
MSAC_TRACE=vectors/argon10001_f5_2tiles.txt make     # one trace; MSAC_MAX_SYMS caps it, WAVES=1 dumps tb.fst
```

## Status (2026-11-05)

- Hardened with Tiny Tapeout's flow (LibreLane 3.0.14, sky130A): 8x2 tiles, 62 % utilisation, 0 DRC / LVS /
  antenna errors; 50 MHz closes in the typical corner (+5.1 ns), the slow corner closes at ~39 MHz; run it at
  <= 35 MHz.
- RTL and gate-level replays: 40,360 symbols, all match. Tiny Tapeout precheck: pass.
- The KV260 twin bitstream builds (WNS +6.2 ns); the board run is pending.

## What is Tiny Tapeout?

Tiny Tapeout is an educational project that makes it easier and cheaper than ever to get digital and analog designs
manufactured on a real chip. Learn more at https://tinytapeout.com.
