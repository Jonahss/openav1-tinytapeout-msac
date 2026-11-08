#!/usr/bin/env bash
# Build the Alveo U200 twin of the Tiny Tapeout chip: tt_um_jonahss_msac behind an AXI GPIO at BAR0 offset 0, chip clock
# 50 MHz, in the openav1 U200 shell (PCIe XDMA + DDR4) with U200_TWIN=1:   fpga/u200/build.sh [tag]
# Needs an openav1 checkout with fpga/u200/build.tcl's twin mode (OPENAV1, default ~/openav1-wt-ttshell), Vivado 2025.2.
# Output ${TT_TWIN_OUT:-$HOME/tt-twin}/u200-<tag>/: project, impl_*.rpt, the bitstream. Then on the host:
#   <openav1>/fpga/u200/program.sh <bit>;  python3 fpga/kv260/twin.py --dev /dev/xdma0_user --base 0 [--self-test]
# 2026-11-07, the day the KV260 died: the same twin, register-driven as it always was, on the card.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
tag=${1:-$(git -C "$root" rev-parse --short HEAD)$(git -C "$root" diff --quiet || echo -dirty)}
OPENAV1=${OPENAV1:-$HOME/openav1-wt-ttshell}
out=${TT_TWIN_OUT:-$HOME/tt-twin}/u200-$tag
mkdir -p "$out/work"
export U200_ROOT=$OPENAV1 U200_OUT=$out U200_MHZ=50 U200_TWIN=1 U200_DDR=${U200_DDR:-1} U200_PBLOCK=1
export U200_BOARD_REPO=${U200_BOARD_REPO:-$HOME/vivado-dl/boards} U200_THREADS=${U200_THREADS:-8}
export U200_SOURCES="$root/src/cdf_map_pkg.sv $root/src/msac.sv $root/src/tt_um_jonahss_msac.sv $root/fpga/kv260/tt_twin_v.v"
echo "tt twin u200 build $tag -> $out (shell $OPENAV1 @ $(git -C "$OPENAV1" rev-parse --short HEAD))"
source /opt/Xilinx/2025.2.1/Vivado/settings64.sh > /dev/null
cd "$out/work"
vivado -mode batch -nojournal -log "$out/vivado.log" -source "$OPENAV1/fpga/u200/build.tcl"
grep -q BUILD_DONE "$out/vivado.log" || { echo "build failed, see $out/vivado.log" >&2; exit 1; }
grep -A3 "WNS(ns)" "$out/impl_timing.rpt" | head -4 || true
echo "bit: $out/proj/u200.runs/impl_1/sys_wrapper.bit"
