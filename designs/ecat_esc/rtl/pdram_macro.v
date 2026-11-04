// Process-data RAM from sky130_sram_1kbyte_1rw1r_8x1024_8 macros (1 KB each, byte-wide),
// addresses 0x1000 up, for RAM_BYTES = 1024 * n. Same three-clock read latency and
// ports as pdram_flop:
//   stage 1  register the request, the in-range test, the bank and the RAM offset
//   stage 2  the macro latches its inputs on the next rising edge and reads or writes on
//            the falling edge after it
//   stage 3  register the selected bank's output
// A read presented one clock after a write of the same byte sees the new data.
module pdram_macro #(
    parameter RAM_BYTES = 2048
) (
    input            clk,
    input            rst,
    input            mem_req,
    input            mem_we,
    input     [15:0] mem_addr,
    input     [7:0]  mem_wdata,
    output reg [7:0] mem_rdata,
    output reg       mem_rvalid
);
    localparam NB = RAM_BYTES / 1024;
    localparam BW = (NB > 1) ? $clog2(NB) : 1;

    wire [15:0] off = mem_addr - 16'h1000;
    wire        hit = (mem_addr >= 16'h1000) && ({16'd0, off} < RAM_BYTES);

    // stage 1
    reg        wr1, rd1;
    reg [9+BW:0] off1;
    reg [7:0]  wd1;
    // stage 2
    reg          rv2;
    reg [BW-1:0] bsel2;

    wire [BW-1:0] bank1 = off1[10 +: BW];
    wire [8*NB-1:0] q;

    genvar b;
    generate
        for (b = 0; b < NB; b = b + 1) begin : g_bank
            wire sel = (wr1 || rd1) && (bank1 == b);
            /* verilator lint_off PINCONNECTEMPTY */
            sky130_sram_1kbyte_1rw1r_8x1024_8 u_mac (
                .clk0(clk), .csb0(!sel), .web0(!wr1), .wmask0(1'b1),
                .addr0(off1[9:0]), .din0(wd1), .dout0(q[8*b +: 8]),
                .clk1(clk), .csb1(1'b1), .addr1(10'd0), .dout1()
            );
            /* verilator lint_on PINCONNECTEMPTY */
        end
    endgenerate

    always @(posedge clk) begin
        wr1   <= !rst && mem_req && mem_we && hit;
        rd1   <= !rst && mem_req && !mem_we && hit;
        off1  <= off[9+BW:0];
        wd1   <= mem_wdata;

        rv2   <= !rst && rd1;
        bsel2 <= bank1;

        mem_rdata  <= q[8*bsel2 +: 8];
        mem_rvalid <= !rst && rv2;
    end
endmodule
