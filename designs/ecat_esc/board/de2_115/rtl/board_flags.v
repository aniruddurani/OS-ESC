// The ESC's FIFO flags for the board status, in the 50 MHz clock: sticky[0] rx_overflow,
// sticky[1] tx_overflow, sticky[2] tx_underrun. rx_overflow and tx_overflow are already
// sticky in esc_top (rx_clk and core domains); tx_underrun can be a one-tx_clk pulse, so
// it is latched in the tx_clk domain first. Each level then crosses into clk50 through two
// flops. rst (asynchronous, active high: esc_top's reset) clears everything.
module board_flags (
    input        clk50,
    input        tx_clk,
    input        rst,
    input        rx_overflow,
    input        tx_overflow,
    input        tx_underrun,
    output [2:0] sticky
);
    reg underrun_seen;
    always @(posedge tx_clk or posedge rst)
        if (rst) underrun_seen <= 1'b0;
        else if (tx_underrun) underrun_seen <= 1'b1;

    reg [2:0] s1, s2;
    always @(posedge clk50 or posedge rst)
        if (rst) begin
            s1 <= 3'b0;
            s2 <= 3'b0;
        end else begin
            s1 <= {underrun_seen, tx_overflow, rx_overflow};
            s2 <= s1;
        end
    assign sticky = s2;
endmodule
