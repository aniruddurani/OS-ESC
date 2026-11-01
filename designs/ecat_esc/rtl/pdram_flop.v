// Process-data RAM as a flip-flop array, addresses 0x1000 up, for sizes below 1 KB.
// Three pipeline stages, so read data is valid MEM_LAT = 3 clocks after the request,
// flagged by mem_rvalid:
//   stage 1  register the request, the in-range test and the RAM offset
//   stage 2  write, and read the array on the registered offset
//   stage 3  register the output
// Requests outside the RAM are ignored (the datagram engine never issues them).
// A read presented one clock after a write of the same byte sees the new data.
module pdram_flop #(
    parameter RAM_BYTES = 256
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
    localparam AW = $clog2(RAM_BYTES);

    reg [7:0] ram [0:RAM_BYTES-1];

    wire [15:0] off = mem_addr - 16'h1000;
    wire        hit = (mem_addr >= 16'h1000) && ({16'd0, off} < RAM_BYTES);

    // stage 1
    reg          wr1;
    reg          rv1;
    reg [AW-1:0] off1;
    reg [7:0]    wd1;
    // stage 2
    reg          rv2;
    reg [7:0]    rd2;

    always @(posedge clk) begin
        wr1  <= !rst && mem_req && mem_we && hit;
        rv1  <= !rst && mem_req && !mem_we && hit;
        off1 <= off[AW-1:0];
        wd1  <= mem_wdata;

        if (wr1) ram[off1] <= wd1;
        rd2 <= ram[off1];
        rv2 <= !rst && rv1;

        mem_rdata  <= rd2;
        mem_rvalid <= !rst && rv2;
    end
endmodule
