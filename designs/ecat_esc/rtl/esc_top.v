// EtherCAT slave controller, one MII port. Three clock domains:
//   rx_clk  (from the PHY)   mii_rx
//   clk     (core, 100 MHz)  esc_core
//   tx_clk  (from the PHY)   mii_tx
// joined by two asynchronous FIFOs. A stream word is {err, last, first, data[7:0]}.
// The frame goes back out the same port (a one-port slave closes the loop).
//
//   rst           asynchronous, active high; released synchronously in each domain
//   rx_overflow   sticky: the receive FIFO dropped a byte
//   tx_overflow   sticky: the transmit FIFO dropped a byte
//   tx_underrun   the transmitter ran dry mid-frame (the frame went out with a bad FCS)
module esc_top #(
    parameter N_FMMU      = 3,
    parameter N_SM        = 4,
    parameter RAM_BYTES   = 1024,
    parameter DIAG_EN     = 1,
    parameter TRACE_DEPTH = 8,
    parameter CNT_W       = 16,
    parameter FIFO_DEPTH  = 16
) (
    input        rst,

    input        rx_clk,
    input        rx_dv,
    input        rx_er,
    input  [3:0] rxd,

    input        tx_clk,
    output       tx_en,
    output [3:0] txd,

    input        clk,

    output reg   rx_overflow,
    output reg   tx_overflow,
    output       tx_underrun
);
    // --- resets: asynchronous assert, synchronous release per domain -------------
    reg [1:0] rx_rs, core_rs, tx_rs;
    always @(posedge rx_clk or posedge rst)
        if (rst) rx_rs <= 2'b11; else rx_rs <= {rx_rs[0], 1'b0};
    always @(posedge clk or posedge rst)
        if (rst) core_rs <= 2'b11; else core_rs <= {core_rs[0], 1'b0};
    always @(posedge tx_clk or posedge rst)
        if (rst) tx_rs <= 2'b11; else tx_rs <= {tx_rs[0], 1'b0};
    wire rx_rst = rx_rs[1], core_rst = core_rs[1], tx_rst = tx_rs[1];

    // --- receive: MII -> bytes -> FIFO ---------------------------------------------
    wire       r_valid, r_first, r_last, r_err;
    wire [7:0] r_data;

    mii_rx u_rx (
        .rx_clk(rx_clk), .rst(rx_rst),
        .rx_dv(rx_dv), .rx_er(rx_er), .rxd(rxd),
        .valid(r_valid), .data(r_data), .first(r_first), .last(r_last), .err(r_err)
    );

    wire        rxf_full, rxf_empty;
    wire [10:0] rxf_rdata;

    async_fifo #(.W(11), .DEPTH(FIFO_DEPTH)) u_rxfifo (
        .wclk(rx_clk), .wrst(rx_rst), .we(r_valid),
        .wdata({r_err, r_last, r_first, r_data}), .full(rxf_full),
        .rclk(clk), .rrst(core_rst), .re(!rxf_empty), .rdata(rxf_rdata), .empty(rxf_empty)
    );

    always @(posedge rx_clk or posedge rst)
        if (rst) rx_overflow <= 1'b0;
        else if (r_valid && rxf_full) rx_overflow <= 1'b1;

    // --- core ------------------------------------------------------------------------
    wire       c_valid, c_first, c_last, c_err;
    wire [7:0] c_data;

    esc_core #(.N_FMMU(N_FMMU), .N_SM(N_SM), .RAM_BYTES(RAM_BYTES), .DIAG_EN(DIAG_EN),
               .TRACE_DEPTH(TRACE_DEPTH), .CNT_W(CNT_W)) u_core (
        .clk(clk), .rst(core_rst),
        .in_valid(!rxf_empty), .in_data(rxf_rdata[7:0]), .in_first(rxf_rdata[8]),
        .in_last(rxf_rdata[9]), .in_err(rxf_rdata[10]),
        .out_valid(c_valid), .out_data(c_data), .out_first(c_first), .out_last(c_last),
        .out_err(c_err)
    );

    // --- transmit: FIFO -> bytes -> MII ----------------------------------------------
    wire        txf_full, txf_empty, t_ready;
    wire [10:0] txf_rdata;

    async_fifo #(.W(11), .DEPTH(FIFO_DEPTH)) u_txfifo (
        .wclk(clk), .wrst(core_rst), .we(c_valid),
        .wdata({c_err, c_last, c_first, c_data}), .full(txf_full),
        .rclk(tx_clk), .rrst(tx_rst), .re(t_ready && !txf_empty), .rdata(txf_rdata),
        .empty(txf_empty)
    );

    always @(posedge clk or posedge rst)
        if (rst) tx_overflow <= 1'b0;
        else if (c_valid && txf_full) tx_overflow <= 1'b1;

    mii_tx u_tx (
        .tx_clk(tx_clk), .rst(tx_rst),
        .valid(!txf_empty), .data(txf_rdata[7:0]), .first(txf_rdata[8]), .last(txf_rdata[9]),
        .err(txf_rdata[10]), .ready(t_ready), .tx_en(tx_en), .txd(txd), .underrun(tx_underrun)
    );
endmodule
