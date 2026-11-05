# The chip's clock (pl_clk1 -> clk_pl_1) and the AXI side (pl_clk0 -> clk_pl_0) are asynchronous, as the RP2040 and
# the chip are: the chip's own input synchronisers handle the crossing, the host reads back slowly, nothing is
# timed across.
set_clock_groups -asynchronous -group [get_clocks clk_pl_0] -group [get_clocks clk_pl_1]
