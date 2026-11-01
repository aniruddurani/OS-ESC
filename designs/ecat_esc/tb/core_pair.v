// Test wrapper: two identical esc_core instances fed the same bytes, one with
// diag and one without, so a test can show diag never changes the stream.
module core_pair #(
    parameter RAM_BYTES = 256
) (
    input        clk,
    input        rst,
    input        in_valid,
    input  [7:0] in_data,
    input        in_first,
    input        in_last,
    input        in_err,

    output       a_valid, output [7:0] a_data, output a_first, output a_last, output a_err,
    output       b_valid, output [7:0] b_data, output b_first, output b_last, output b_err
);
    esc_core #(.RAM_BYTES(RAM_BYTES), .DIAG_EN(1)) u_with (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_data(in_data), .in_first(in_first), .in_last(in_last), .in_err(in_err),
        .out_valid(a_valid), .out_data(a_data), .out_first(a_first), .out_last(a_last), .out_err(a_err)
    );
    esc_core #(.RAM_BYTES(RAM_BYTES), .DIAG_EN(0)) u_without (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_data(in_data), .in_first(in_first), .in_last(in_last), .in_err(in_err),
        .out_valid(b_valid), .out_data(b_data), .out_first(b_first), .out_last(b_last), .out_err(b_err)
    );
endmodule
