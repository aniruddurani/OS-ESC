// Process-data RAM as a flip-flop array, addresses 0x1000 up, for sizes below 1 KB.
// Three pipeline stages, so read data is valid MEM_LAT = 3 clocks after the request,
// flagged by mem_rvalid:
//   stage 1  register the request: the in-range test, the write strobes (one per
//            16-byte group and one per byte of a group) and the read position
//   stage 2  write; read the byte at the position in every 16-byte group (a 16:1 select
//            per group)
//   stage 3  select the group (an NG:1 select) and register the output
// The array is 16-byte groups so that neither select is wider than 16:1 or 64:1, which
// is what lets the read path close timing at 100 MHz in the slow corner.
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
    localparam NG = (RAM_BYTES + 15) / 16;              // 16-byte groups
    localparam GB = (NG > 1) ? $clog2(NG) : 1;          // group index width
    localparam NP = 1 << GB;                            // groups, rounded up to a power of two

    wire [15:0] off = mem_addr - 16'h1000;
    wire        hit = (mem_addr >= 16'h1000) && ({16'd0, off} < RAM_BYTES);
    wire [GB-1:0] grp = off[GB+3:4];
    wire [3:0]    col = off[3:0];
    wire          wr  = !rst && mem_req && mem_we && hit;

    // stage 1
    reg [NP-1:0]  wrow;
    reg [15:0]    wcol;
    reg           rv1;
    reg [GB-1:0]  grp1;
    reg [3:0]     col1;
    reg [7:0]     wd1;
    // stage 2
    reg           rv2;
    reg [GB-1:0]  grp2;
    reg [8*NP-1:0] pick2;

    wire [8*16*NP-1:0] flat;                            // every byte of the array

    genvar gr, gc;
    generate
        for (gr = 0; gr < NP; gr = gr + 1) begin : g_row
            localparam [11:0] GI = gr;
            always @(posedge clk) begin
                wrow[gr]       <= (gr < NG) && wr && (off[15:4] == GI);
                pick2[8*gr +: 8] <= flat[8 * (16 * gr + col1) +: 8];
            end
            for (gc = 0; gc < 16; gc = gc + 1) begin : g_col
                reg [7:0] bv;
                always @(posedge clk)
                    if (wrow[gr] && wcol[gc]) bv <= wd1;
                assign flat[8 * (16 * gr + gc) +: 8] = bv;
            end
        end
        for (gc = 0; gc < 16; gc = gc + 1) begin : g_wcol
            localparam [3:0] CI = gc;
            always @(posedge clk) wcol[gc] <= (col == CI);
        end
    endgenerate

    always @(posedge clk) begin
        rv1  <= !rst && mem_req && !mem_we && hit;
        grp1 <= grp;
        col1 <= col;
        wd1  <= mem_wdata;

        rv2  <= !rst && rv1;
        grp2 <= grp1;

        mem_rdata  <= pick2[8*grp2 +: 8];
        mem_rvalid <= !rst && rv2;
    end
endmodule
