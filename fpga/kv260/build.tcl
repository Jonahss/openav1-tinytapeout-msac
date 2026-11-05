# Vivado (batch) build of the tt-msac KV260 twin: the Zynq MPSoC PS, an AXI GPIO (two 32-bit channels: host -> pins,
# pins -> host) on HPM0 at 0xA000_0000, and tt_twin_v (the chip behind its 24 pins) on pl_clk1. Driven by build.sh
# (TT_ROOT = the tt-msac repo, TT_OUT = the work dir).
set root $::env(TT_ROOT)
set out  $::env(TT_OUT)
set part xck26-sfvc784-2LV-c
set_param general.maxThreads 8
create_project -force tt_twin $out/proj -part $part
set_property target_language Verilog [current_project]
add_files -norecurse [list $root/src/cdf_map_pkg.sv $root/src/msac.sv $root/src/tt_um_jonahss_msac.sv $root/fpga/kv260/tt_twin_v.v]
set_property file_type SystemVerilog [get_files -filter {NAME =~ *.sv}]
add_files -fileset constrs_1 -norecurse $root/fpga/kv260/tt_twin.xdc
update_compile_order -fileset sources_1

create_bd_design sys
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 ps]
# The PS settings openav1's fpga/kv260/build.tcl found the hard way: HPM0 at 128 bits (the K26 firmware sets
# AFI_FS so; a 32-bit PL sees only lane 0), the K26 SOM's DDR geometry (the address map comes from it).
# pl_clk0 = IOPLL/15 = 100 MHz for the AXI side, pl_clk1 = IOPLL/30 = 50 MHz for the chip.
set_property -dict [list \
    CONFIG.PSU__USE__M_AXI_GP0 {1} CONFIG.PSU__MAXIGP0__DATA_WIDTH {128} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} CONFIG.PSU__USE__M_AXI_GP2 {0} \
    CONFIG.PSU__USE__S_AXI_GP0 {0} CONFIG.PSU__USE__S_AXI_GP1 {0} CONFIG.PSU__USE__S_AXI_GP2 {0} \
    CONFIG.PSU__USE__S_AXI_GP3 {0} CONFIG.PSU__USE__S_AXI_GP4 {0} CONFIG.PSU__USE__S_AXI_GP5 {0} CONFIG.PSU__USE__S_AXI_GP6 {0} \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__SRCSEL {IOPLL} CONFIG.PSU__CRL_APB__PL0_REF_CTRL__DIVISOR0 {15} CONFIG.PSU__CRL_APB__PL0_REF_CTRL__DIVISOR1 {1} \
    CONFIG.PSU__FPGA_PL1_ENABLE {1} CONFIG.PSU__CRL_APB__PL1_REF_CTRL__FREQMHZ {50} \
    CONFIG.PSU__CRL_APB__PL1_REF_CTRL__SRCSEL {IOPLL} CONFIG.PSU__CRL_APB__PL1_REF_CTRL__DIVISOR0 {30} CONFIG.PSU__CRL_APB__PL1_REF_CTRL__DIVISOR1 {1} \
    CONFIG.PSU__FPGA_PL2_ENABLE {0} CONFIG.PSU__FPGA_PL3_ENABLE {0} \
    CONFIG.PSU__USE__IRQ0 {0} \
    CONFIG.PSU__DDRC__MEMORY_TYPE {DDR 4} CONFIG.PSU__DDRC__COMPONENTS {Components} CONFIG.PSU__DDRC__SPEED_BIN {DDR4_2400R} \
    CONFIG.PSU__DDRC__BUS_WIDTH {64 Bit} CONFIG.PSU__DDRC__DRAM_WIDTH {16 Bits} CONFIG.PSU__DDRC__DEVICE_CAPACITY {8192 MBits} \
    CONFIG.PSU__DDRC__RANK_ADDR_COUNT {0} CONFIG.PSU__DDRC__BG_ADDR_COUNT {1} CONFIG.PSU__DDRC__BANK_ADDR_COUNT {2} \
    CONFIG.PSU__DDRC__ROW_ADDR_COUNT {16} CONFIG.PSU__DDRC__COL_ADDR_COUNT {10}] $ps
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst]
set sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 sc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc
set gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio]
set_property -dict [list CONFIG.C_IS_DUAL {1} CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_OUTPUTS {1} CONFIG.C_DOUT_DEFAULT {0x00000000} \
    CONFIG.C_GPIO2_WIDTH {32} CONFIG.C_ALL_INPUTS_2 {1}] $gpio
set twin [create_bd_cell -type module -reference tt_twin_v twin]

connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins ps/maxihpm0_fpd_aclk] [get_bd_pins sc/aclk] [get_bd_pins gpio/s_axi_aclk] \
    [get_bd_pins rst/slowest_sync_clk]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins rst/ext_reset_in]
connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins sc/aresetn] [get_bd_pins gpio/s_axi_aresetn]
connect_bd_net [get_bd_pins ps/pl_clk1] [get_bd_pins twin/chip_clk]
connect_bd_net [get_bd_pins gpio/gpio_io_o] [get_bd_pins twin/gpio_o]
connect_bd_net [get_bd_pins twin/gpio_i] [get_bd_pins gpio/gpio2_io_i]
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins sc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins sc/M00_AXI] [get_bd_intf_pins gpio/S_AXI]
assign_bd_address -offset 0xA0000000 -range 64K -target_address_space [get_bd_addr_spaces ps/Data] [get_bd_addr_segs gpio/S_AXI/Reg]
validate_bd_design
save_bd_design
set bd [get_files sys.bd]
generate_target all $bd
make_wrapper -files $bd -top
add_files -norecurse [file join $out proj tt_twin.gen sources_1 bd sys hdl sys_wrapper.v]
set_property top sys_wrapper [current_fileset]
update_compile_order -fileset sources_1

launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { puts "SYNTH_FAILED"; exit 1 }
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { puts "IMPL_FAILED"; exit 1 }
open_run impl_1
report_timing_summary -max_paths 10 -file $out/impl_timing.rpt
report_utilization -file $out/impl_util.rpt
report_utilization -hierarchical -hierarchical_depth 3 -file $out/impl_util_hier.rpt
report_clock_interaction -file $out/impl_clock_interaction.rpt
puts "BUILD_DONE"
exit
