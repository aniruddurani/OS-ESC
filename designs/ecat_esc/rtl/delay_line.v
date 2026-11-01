// Byte delay line for the frame stream. Each byte appears DEPTH input bytes
// after it arrived (counting accepted bytes, not clock cycles), with its tag
// bits riding along. The last DEPTH bytes of a frame are dropped and `last`
// (and `err`) move onto the final byte that survives. With DEPTH = 4 this
// removes the received FCS, which mii_tx regenerates.
module delay_line #(
    parameter DEPTH = 4,
    parameter TW    = 13
) (
    input               clk,
    input               rst,
    input               valid,
    input         [7:0] data,
    input               first,
    input               last,
    input               err,
    input      [TW-1:0] tag,
    output reg          o_valid,
    output reg    [7:0] o_data,
    output reg          o_first,
    output reg          o_last,
    output reg          o_err,
    output reg [TW-1:0] o_tag
);
    localparam EW = 8 + 1 + TW;                 // data, first, tag
    localparam CW = $clog2(DEPTH + 1);

    reg [EW-1:0] sh [0:DEPTH-1];                // sh[0] newest ... sh[DEPTH-1] oldest
    reg [CW-1:0] fill;
    integer      i;

    wire [CW-1:0] cur  = first ? {CW{1'b0}} : fill;
    wire          full = (cur == DEPTH[CW-1:0]);

    always @(posedge clk) begin
        o_valid <= 1'b0;
        o_first <= 1'b0;
        o_last  <= 1'b0;
        o_err   <= 1'b0;
        if (rst) begin
            fill   <= {CW{1'b0}};
            o_data <= 8'd0;
            o_tag  <= {TW{1'b0}};
        end else if (valid) begin
            if (full) begin
                o_valid <= 1'b1;
                {o_data, o_first, o_tag} <= sh[DEPTH-1];
                o_last  <= last;
                o_err   <= last & err;
            end
            for (i = DEPTH - 1; i > 0; i = i - 1)
                sh[i] <= sh[i-1];
            sh[0] <= {data, first, tag};
            if (last)
                fill <= {CW{1'b0}};
            else if (!full)
                fill <= cur + {{(CW - 1){1'b0}}, 1'b1};
            else
                fill <= cur;
        end
    end
endmodule
