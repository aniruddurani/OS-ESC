// Testbench wrapper around port 0 of the 1 KB macro, so one test can drive either the
// behavioural model (tb/models) or the PDK's own verilog model.
module sram_tb (
    input        clk,
    input        csb0,
    input        web0,
    input  [9:0] addr0,
    input  [7:0] din0,
    output [7:0] dout0
);
    wire [7:0] dout1_unused;
    sky130_sram_1kbyte_1rw1r_8x1024_8 u_mac (
        .clk0(clk), .csb0(csb0), .web0(web0), .wmask0(1'b1), .addr0(addr0), .din0(din0),
        .dout0(dout0),
        .clk1(clk), .csb1(1'b1), .addr1(10'd0), .dout1(dout1_unused)
    );
endmodule
