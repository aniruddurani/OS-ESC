// Declaration of the PDK macro for `make dlint` only: lint needs the ports of a module
// that the RTL instantiates but does not define. Not used for simulation or synthesis.
/* verilator lint_off UNUSEDSIGNAL */
module sky130_sram_1kbyte_1rw1r_8x1024_8 (
    input        clk0,
    input        csb0,
    input        web0,
    input        wmask0,
    input  [9:0] addr0,
    input  [7:0] din0,
    output [7:0] dout0,
    input        clk1,
    input        csb1,
    input  [9:0] addr1,
    output [7:0] dout1
);
    assign dout0 = 8'd0;
    assign dout1 = 8'd0;
endmodule
/* verilator lint_on UNUSEDSIGNAL */
