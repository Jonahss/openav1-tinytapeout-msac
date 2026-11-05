#!/usr/bin/env bash
# Build the KV260 twin of the Tiny Tapeout chip (tt_um_jonahss_msac behind an AXI GPIO, chip clock 50 MHz) and
# package it for the Kria firmware loader:   fpga/kv260/build.sh [tag]
# Output in ${TT_TWIN_OUT:-$HOME/tt-twin}/<tag>/: the Vivado project, impl_*.rpt and fw/tt_twin/{tt_twin.bit.bin,
# tt_twin.dtbo, shell.json} for `sudo fpga/kv260/load.sh <fw dir>` on the board. Needs Vivado (with bootgen) and dtc.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
tag=${1:-$(git -C "$root" rev-parse --short HEAD)$(git -C "$root" diff --quiet || echo -dirty)}
out=${TT_TWIN_OUT:-$HOME/tt-twin}/$tag
export TT_ROOT=$root TT_OUT=$out
mkdir -p "$out/work" "$out/fw/tt_twin"
echo "tt twin build $tag -> $out"
cd "$out/work"
vivado -mode batch -nojournal -log "$out/vivado.log" -source "$here/build.tcl"
grep -q BUILD_DONE "$out/vivado.log" || { echo "build failed, see $out/vivado.log" >&2; exit 1; }
bit=$out/proj/tt_twin.runs/impl_1/sys_wrapper.bit
cd "$out/fw"
printf 'all: { [destination_device = pl] %s }\n' "$bit" > tt_twin.bif
bootgen -arch zynqmp -image tt_twin.bif -process_bitstream bin -w > bootgen.log 2>&1
cp "$bit.bin" tt_twin/tt_twin.bit.bin
cp "$here/shell.json" tt_twin/
dtc -@ -O dtb -o tt_twin/tt_twin.dtbo "$here/tt_twin.dts"
ls -la tt_twin
grep -A3 "WNS(ns)" "$out/impl_timing.rpt" | head -4 || true
echo "fw dir: $out/fw/tt_twin"
