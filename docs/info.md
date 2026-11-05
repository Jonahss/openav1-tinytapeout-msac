<!---
Datasheet page of tt_um_jonahss_msac (Tiny Tapeout). Images < 512 kB each, < 1 MB total.
-->

## How it works

This is the arithmetic (symbol) decoder of [openav1](https://github.com/Jonahss/openav1), an open-source AV1 video
decoder in hardware: the same `msac.sv` the FPGA decoder runs, proven bit-exact over the Argon conformance streams
there, behind a byte-wide register window on the 24 pins.

AV1 entropy-codes every syntax element with a multi-symbol arithmetic coder. For each symbol the decoder gets a
cumulative distribution (CDF, up to 16 symbols, 15 inverted 15-bit entries), splits the current range in proportion,
finds the symbol, renormalises (pulling more stream bits), and adapts the CDF toward the symbol it just decoded. The
core does one symbol in two clocks: stage 1 computes all 16 candidate boundaries in parallel and picks the symbol;
stage 2 renormalises and adapts all 15 CDF entries at once. That parallelism is why it needs 16 tiles: it is the
decoder's core, not a cut-down version.

The core keeps no CDF tables: the host sends the row with each request and reads the adapted row back, as the
decoder's CDF store does on the FPGA. The core also holds the bitstream window, a 4-byte stream FIFO feeds it.

Inputs pass two-flop synchronisers, and a write acts only once strobe, address and data have been stable for two
synchronised clocks, so a host driving the pins from software (the demo board's RP2040) is safe at any phase.

### Pins

| pins | direction | meaning |
|---|---|---|
| `ui[6:0]` | in | register address |
| `ui[7]` | in | write strobe: high = the host drives `uio` and writes the addressed register |
| `uio[7:0]` | bidir | data: the host's byte while the strobe is high, the addressed register's byte while it is low |
| `uo[0]` | out | `resp_valid`: the symbol of the last request is ready (sticky until the next request) |
| `uo[1]` | out | `req_ready`: the core can take a request |
| `uo[2]` | out | `in_ready`: the stream FIFO has room for a byte |
| `uo[3]` | out | `wb_valid`: range, counter and adapted CDF row of the last request are readable |
| `uo[7:4]` | out | the decoded symbol |

### Registers

| address | write | read |
|---|---|---|
| `0x00`..`0x1D` | CDF row: entry k at `2k` (low byte) / `2k+1` (high byte), inverted (32768 - cdf) | |
| `0x1E` | `[3:0]` n = symbols - 1 (1..15), `[5:4]` kind: 0 adaptive, 1 bool (fixed probability in entry 0), 2 equiprobable bit | |
| `0x1F` | `[5:0]` adaptation counter (0..32) | |
| `0x20` | control: bit 0 init (once per strobe; empties the FIFO), bit 1 CDF update enable (level), bit 2 request (once per strobe), bit 3 end of stream (level: no bytes beyond those pushed) | |
| `0x21` | stream byte (one per strobe; check `in_ready` first) | |
| `0x40` | | status (= `uo`) |
| `0x41` | | symbol |
| `0x42` / `0x43` | | range, low / high byte |
| `0x44` | | adapted counter |
| `0x50`..`0x6D` | | adapted CDF row, entry k at `0x50 + 2k` |

### Pin timing (host side)

- write: put the address on `ui[6:0]` with the strobe low and `uio` released; then, in one pin update, raise the
  strobe and drive the data; hold for at least 4 clocks; then, in one pin update, lower the strobe and release `uio`;
  wait at least 4 clocks.
- read: put the address with the strobe low, wait at least 4 clocks, read `uio`.
- the chip drives `uio` whenever the strobe is low (`uio_oe` follows the strobe pin directly).

## How to test

A tile of AV1, symbol by symbol:

1. reset; write control `0x20` = `0x03` (init, CDF update enabled; `0x01` if the frame header sets
   `disable_cdf_update`).
2. push stream bytes into `0x21` while `in_ready` is high; after the last one, write control with bit 3 set.
3. for each symbol: write the CDF row (when it changes), `0x1E` (n, kind), `0x1F` (counter), then control with bit 2
   (keep bits 1 and 3 as they are). Keep pushing stream bytes while waiting; when `resp_valid` rises the symbol is on
   `uo[7:4]`; after `wb_valid`, read the range and, for adaptive symbols, the adapted row and counter.

`test/msac_host.py` is that host as a generator of pin operations; `test/test.py` (cocotb) runs it against the RTL
and the gate-level netlist over four dav1d golden traces (40,360 symbols: 8-bit 4:4:4, 10-bit, multi-tile frames,
`disable_cdf_update`), checking every symbol, range, adapted row and counter. `fpga/kv260/twin.py` runs the same
code against this exact design on a Kria KV260 (the chip behind its 24 pins on an AXI GPIO, on its own clock).

Clock: up to ~35 MHz is safe across corners (timing closes at 50 MHz in the typical corner).

## External hardware

None. The demo board's RP2040 (or any microcontroller with 17 GPIOs) is the host.
