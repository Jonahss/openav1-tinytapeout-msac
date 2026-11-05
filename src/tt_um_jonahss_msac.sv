// openav1 on Tiny Tapeout: the AV1 arithmetic (symbol) decoder core, rtl/msac.sv of github.com/Jonahss/openav1, behind a
// byte-wide register window on the 24 pins. The core is the one the FPGA decoder runs (two-stage symbol loop, CDF rows
// supplied by the requester and returned adapted); here the requester is the host on the pins.
//
// Pins:
//   ui_in[6:0]  register address (0x00..0x7F)
//   ui_in[7]    write strobe: while high, the byte on uio is written to the addressed register on every clock (data
//               registers are idempotent; the control register and the stream push act once per strobe edge)
//   uio[7:0]    data: driven by the host while ui_in[7] is high, by the chip (the addressed byte) while it is low
//   uo_out[7:0] the status byte, always: {resp_sym[3:0], wb_valid, in_ready, req_ready, resp_valid}
//
// Registers (write):
//   0x00..0x1D  CDF row, 15 inverted entries of 16 bits, entry k at 0x00 + 2k (low byte first)
//   0x1E        [3:0] n (symbols - 1, 1..15), [5:4] kind (0 adaptive, 1 bool with fixed probability, 2 equiprobable)
//   0x1F        [5:0] adaptation counter (0..32)
//   0x20        control: bit 0 init (pulse), bit 1 cdf_update_en (level), bit 2 request (pulse), bit 3 eos (level:
//               no more stream bytes after the ones already pushed)
//   0x21        stream byte push (one byte per strobe edge; in_ready in the status says there is room)
// Registers (read):
//   0x40 status  0x41 symbol  0x42/0x43 range (low, high)  0x44 adapted counter  0x50..0x6D adapted CDF row
//
// A symbol: write the row (once per row change), n/kind/cnt, then control with bit 2; resp_valid rises in the status
// one clock after the request is accepted and the symbol is in the top nibble; wb_valid one clock later says the
// adapted row is readable at 0x50. Both stay set until the next request.
`default_nettype none

module tt_um_jonahss_msac (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
    wire rst = !rst_n;
    wire [6:0] addr = ui_in[6:0];
    wire       wr   = ui_in[7];

    // ---- request registers ----
    logic [239:0] r_cdf;
    logic [3:0]   r_n;
    logic [1:0]   r_kind;
    logic [5:0]   r_cnt;
    logic         r_upd, r_eos;
    logic         wr_prev;                       // for the once-per-strobe-edge registers
    logic [6:0]   addr_prev;
    wire  wr_edge = wr && !(wr_prev && addr_prev == addr);

    // ---- core handshake ----
    logic req_pending, init_pulse;
    logic st_resp, st_wb;                        // sticky until the next request
    logic req_ready, resp_valid, resp_wb_valid, in_ready;
    logic [3:0]  resp_sym;
    logic [15:0] resp_rng;
    logic [239:0] resp_cdf;
    logic [5:0]  resp_cnt;

    // ---- stream byte FIFO (4 deep) ----
    logic [7:0] fifo [0:3];
    logic [1:0] f_wp, f_rp;
    logic [2:0] f_cnt;
    wire f_full  = (f_cnt == 3'd4);
    wire f_empty = (f_cnt == 3'd0);
    wire push = wr_edge && (addr == 7'h21) && !f_full;
    wire pop  = !f_empty && in_ready;

    always_ff @(posedge clk) begin
        if (rst) begin
            r_cdf <= '0; r_n <= 4'd1; r_kind <= 2'd0; r_cnt <= 6'd0; r_upd <= 1'b1; r_eos <= 1'b0;
            wr_prev <= 1'b0; addr_prev <= 7'd0;
            req_pending <= 1'b0; init_pulse <= 1'b0; st_resp <= 1'b0; st_wb <= 1'b0;
            f_wp <= 2'd0; f_rp <= 2'd0; f_cnt <= 3'd0;
        end else begin
            wr_prev <= wr; addr_prev <= addr;
            init_pulse <= 1'b0;
            if (wr) begin
                if (addr < 7'h1E) r_cdf[8 * addr +: 8] <= uio_in;
                if (addr == 7'h1E) begin r_n <= uio_in[3:0]; r_kind <= uio_in[5:4]; end
                if (addr == 7'h1F) r_cnt <= uio_in[5:0];
                if (addr == 7'h20 && wr_edge) begin
                    r_upd <= uio_in[1]; r_eos <= uio_in[3];
                    if (uio_in[0]) begin init_pulse <= 1'b1; f_wp <= 2'd0; f_rp <= 2'd0; f_cnt <= 3'd0; end
                    if (uio_in[2]) begin req_pending <= 1'b1; st_resp <= 1'b0; st_wb <= 1'b0; end
                end
            end
            if (req_pending && req_ready) req_pending <= 1'b0;
            if (resp_valid) st_resp <= 1'b1;
            if (resp_wb_valid) st_wb <= 1'b1;
            // FIFO (an init clears it above; a push and a pop in the same cycle keep the count)
            if (push) begin fifo[f_wp] <= uio_in; f_wp <= f_wp + 2'd1; end
            if (pop) f_rp <= f_rp + 2'd1;
            if (!(push && (addr == 7'h20 && wr_edge && uio_in[0]))) f_cnt <= f_cnt + 3'(push) - 3'(pop);
        end
    end

    msac u_core (
        .clk(clk), .rst(rst),
        .in_data(fifo[f_rp]), .in_valid(!f_empty), .in_ready(in_ready), .in_eos(r_eos && f_empty),
        .init(init_pulse), .cdf_update_en(r_upd),
        .req_valid(req_pending), .req_ready(req_ready), .req_kind(r_kind), .req_n(r_n), .req_cdf(r_cdf), .req_cnt(r_cnt),
        .resp_valid(resp_valid), .resp_sym(resp_sym), .resp_wb_valid(resp_wb_valid), .resp_rng(resp_rng),
        .resp_cdf(resp_cdf), .resp_cnt(resp_cnt)
    );

    // ---- status and the read mux ----
    wire [7:0] status = {resp_sym, st_wb, !f_full, req_ready && !req_pending, st_resp};
    logic [7:0] rd;
    always_comb begin
        rd = 8'h00;
        if (addr == 7'h40) rd = status;
        else if (addr == 7'h41) rd = {4'd0, resp_sym};
        else if (addr == 7'h42) rd = resp_rng[7:0];
        else if (addr == 7'h43) rd = resp_rng[15:8];
        else if (addr == 7'h44) rd = {2'd0, resp_cnt};
        else if (addr >= 7'h50 && addr < 7'h6E) rd = resp_cdf[8 * (addr - 7'h50) +: 8];
    end
    assign uo_out  = status;
    assign uio_out = rd;
    assign uio_oe  = {8{!wr}};

    wire _unused = &{ena, 1'b0};
endmodule
