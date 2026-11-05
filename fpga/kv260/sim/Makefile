# cocotb simulation of the KV260 twin top (tt_twin_v + the chip) driven through twin.py's own pin encoding:
#   make            (Verilator; MSAC_TRACE / MSAC_MAX_SYMS as for test/)
SIM ?= verilator
TOPLEVEL_LANG = verilog
SRC = $(PWD)/../../../src
VERILOG_SOURCES = $(SRC)/cdf_map_pkg.sv $(SRC)/msac.sv $(SRC)/tt_um_jonahss_msac.sv $(PWD)/../tt_twin_v.v
COCOTB_TOPLEVEL = tt_twin_v
COCOTB_TEST_MODULES = test_twin
EXTRA_ARGS += --timing -Wno-fatal
include $(shell cocotb-config --makefiles)/Makefile.sim
