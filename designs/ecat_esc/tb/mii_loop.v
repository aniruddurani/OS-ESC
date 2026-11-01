// Test wrapper: mii_tx wired straight into mii_rx on one clock, so tests can
// check what the transmitter puts on the wire by reading it back as bytes.
module mii_loop (
    input        clk,
    input        rst,
    input        tx_valid,
    input  [7:0] tx_data,
    input        tx_first,
    input        tx_last,
    input        tx_err,
    output       tx_ready,
    output       tx_en,
    output       underrun,
    output       rx_valid,
    output [7:0] rx_data,
    output       rx_first,
    output       rx_last,
    output       rx_err
);
    wire [3:0] txd;

    mii_tx u_tx (
        .tx_clk(clk), .rst(rst),
        .valid(tx_valid), .data(tx_data), .first(tx_first), .last(tx_last), .err(tx_err),
        .ready(tx_ready), .tx_en(tx_en), .txd(txd), .underrun(underrun)
    );

    mii_rx u_rx (
        .rx_clk(clk), .rst(rst),
        .rx_dv(tx_en), .rx_er(1'b0), .rxd(txd),
        .valid(rx_valid), .data(rx_data), .first(rx_first), .last(rx_last), .err(rx_err)
    );
endmodule
