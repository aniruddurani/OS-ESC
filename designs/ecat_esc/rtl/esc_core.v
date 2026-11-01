// The ESC frame core on the core clock: byte stream in, byte stream out.
// No MII and no clock crossing here (esc_top adds those). Pipeline:
//   frame_parse (tags) -> dgram_exec (rewrites) -> delay_line (drops the old FCS)
// with crc32 checking the received FCS on the side. The output stream carries
// `err` on its last byte when the frame must leave with a wrong FCS.
module esc_core #(
    parameter N_FMMU    = 3,
    parameter N_SM      = 4,
    parameter RAM_BYTES = 1024
) (
    input        clk,
    input        rst,

    input        in_valid,
    input  [7:0] in_data,
    input        in_first,
    input        in_last,
    input        in_err,

    output       out_valid,
    output [7:0] out_data,
    output       out_first,
    output       out_last,
    output       out_err
);
    // --- parser -----------------------------------------------------------
    wire        p_valid, p_first, p_last, p_err;
    wire  [7:0] p_data;
    wire  [1:0] p_kind;
    wire [10:0] p_pos;
    wire        dg_start, dg_hdr_done, dg_more, dg_circ;
    wire  [7:0] dg_cmd, dg_idx;
    wire [31:0] dg_addr;
    wire [10:0] dg_len;
    wire        ev_end, ev_runt, ev_oversize, ev_non_ethercat, ev_malformed, ev_rx_err;

    frame_parse u_parse (
        .clk(clk), .rst(rst),
        .valid(in_valid), .data(in_data), .first(in_first), .last(in_last), .err(in_err),
        .o_valid(p_valid), .o_data(p_data), .o_first(p_first), .o_last(p_last), .o_err(p_err),
        .o_kind(p_kind), .o_pos(p_pos),
        .dg_start(dg_start), .dg_hdr_done(dg_hdr_done),
        .dg_cmd(dg_cmd), .dg_idx(dg_idx), .dg_addr(dg_addr), .dg_len(dg_len),
        .dg_more(dg_more), .dg_circ(dg_circ),
        .ev_end(ev_end), .ev_runt(ev_runt), .ev_oversize(ev_oversize),
        .ev_non_ethercat(ev_non_ethercat), .ev_malformed(ev_malformed), .ev_rx_err(ev_rx_err)
    );

    // --- received-FCS check, one clock ahead of the engine's output ---------
    wire crc_residue, crc_residue_next;
    wire [31:0] crc_unused;
    crc32 u_crc (
        .clk(clk), .rst(rst),
        .init(p_valid && p_last),            // ready for the next frame
        .valid(p_valid), .data(p_data),
        .crc(crc_unused), .residue_ok(crc_residue), .residue_next(crc_residue_next)
    );

    wire bad_frame = ev_runt || ev_oversize || ev_non_ethercat || ev_malformed ||
                     ev_rx_err || !crc_residue_next;

    // --- memory: registers below 0x1000, process RAM above -------------------
    wire        mem_req, mem_we, mem_rvalid;
    wire [15:0] mem_addr;
    wire  [7:0] mem_wdata, mem_rdata;
    wire        hi = |mem_addr[15:12];

    wire  [7:0] rf_rdata, pd_rdata;
    wire        rf_rvalid, pd_rvalid;
    wire [15:0] station_addr;
    wire [8*16*((N_FMMU == 0) ? 1 : N_FMMU)-1:0] fmmu_cfg;
    wire [8*8*((N_SM == 0) ? 1 : N_SM)-1:0]      sm_cfg;

    regfile #(.N_FMMU(N_FMMU), .N_SM(N_SM), .RAM_BYTES(RAM_BYTES)) u_regfile (
        .clk(clk), .rst(rst),
        .mem_req(mem_req && !hi), .mem_we(mem_we), .mem_addr(mem_addr), .mem_wdata(mem_wdata),
        .mem_rdata(rf_rdata), .mem_rvalid(rf_rvalid),
        .dl_status(16'd0),
        .station_addr(station_addr), .fmmu_cfg(fmmu_cfg), .sm_cfg(sm_cfg)
    );

    pdram #(.RAM_BYTES(RAM_BYTES)) u_pdram (
        .clk(clk), .rst(rst),
        .mem_req(mem_req && hi), .mem_we(mem_we), .mem_addr(mem_addr), .mem_wdata(mem_wdata),
        .mem_rdata(pd_rdata), .mem_rvalid(pd_rvalid)
    );

    assign mem_rvalid = rf_rvalid || pd_rvalid;
    assign mem_rdata  = rf_rvalid ? rf_rdata : pd_rdata;

    // --- FMMUs (logical addressing) -------------------------------------------
    wire        fm_calc, fm_q0_rd_hit, fm_q0_wr_hit, fm_q1_rd_hit;
    wire [10:0] fm_q0_off, fm_q1_off;
    wire [15:0] fm_q0_wr_addr, fm_q1_rd_addr;
    wire        is_rd_l = (dg_cmd == 8'd10) || (dg_cmd == 8'd12);
    wire        is_wr_l = (dg_cmd == 8'd11) || (dg_cmd == 8'd12);

    fmmu #(.N_FMMU(N_FMMU), .RAM_BYTES(RAM_BYTES)) u_fmmu (
        .clk(clk), .rst(rst), .cfg(fmmu_cfg),
        .calc(fm_calc), .dg_addr(dg_addr), .dg_len(dg_len), .is_rd(is_rd_l), .is_wr(is_wr_l),
        .q0_off(fm_q0_off), .q0_rd_hit(fm_q0_rd_hit),
        .q0_wr_hit(fm_q0_wr_hit), .q0_wr_addr(fm_q0_wr_addr),
        .q1_off(fm_q1_off), .q1_rd_hit(fm_q1_rd_hit), .q1_rd_addr(fm_q1_rd_addr)
    );

    // --- datagram engine -----------------------------------------------------
    wire        x_valid, x_first, x_last, x_err;
    wire  [7:0] x_data;
    wire        ev_dg, ev_addressed;
    wire  [1:0] ev_wkc_inc;

    dgram_exec #(.RAM_BYTES(RAM_BYTES)) u_exec (
        .clk(clk), .rst(rst),
        .p_valid(p_valid), .p_data(p_data), .p_first(p_first), .p_last(p_last),
        .p_kind(p_kind), .p_pos(p_pos),
        .dg_start(dg_start), .dg_hdr_done(dg_hdr_done),
        .dg_cmd(dg_cmd), .dg_addr(dg_addr), .dg_len(dg_len),
        .station_addr(station_addr), .bad_frame(bad_frame),
        .mem_req(mem_req), .mem_we(mem_we), .mem_addr(mem_addr), .mem_wdata(mem_wdata),
        .mem_rdata(mem_rdata), .mem_rvalid(mem_rvalid),
        .fm_calc(fm_calc), .fm_q0_off(fm_q0_off), .fm_q0_rd_hit(fm_q0_rd_hit),
        .fm_q0_wr_hit(fm_q0_wr_hit), .fm_q0_wr_addr(fm_q0_wr_addr),
        .fm_q1_off(fm_q1_off), .fm_q1_rd_hit(fm_q1_rd_hit), .fm_q1_rd_addr(fm_q1_rd_addr),
        .o_valid(x_valid), .o_data(x_data), .o_first(x_first), .o_last(x_last), .o_err(x_err),
        .ev_dg(ev_dg), .ev_addressed(ev_addressed), .ev_wkc_inc(ev_wkc_inc)
    );

    // --- drop the received FCS (the transmitter writes a new one) ------------
    wire tag_unused;
    delay_line #(.DEPTH(4), .TW(1)) u_delay (
        .clk(clk), .rst(rst),
        .valid(x_valid), .data(x_data), .first(x_first), .last(x_last), .err(x_err), .tag(1'b0),
        .o_valid(out_valid), .o_data(out_data), .o_first(out_first), .o_last(out_last),
        .o_err(out_err), .o_tag(tag_unused)
    );

    wire _unused_ok = &{1'b0, p_err, dg_idx, dg_more, dg_circ, ev_end, crc_residue,
                        crc_unused, sm_cfg, ev_dg, ev_addressed, ev_wkc_inc,
                        tag_unused};
endmodule
