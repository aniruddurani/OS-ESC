// Process-data RAM behind the byte-wide memory port, addresses 0x1000 up.
// Same timing as regfile: read data is valid MEM_LAT = 2 clocks after the
// request, flagged by mem_rvalid. Requests outside the RAM are ignored (the
// datagram engine never issues them). Flop-based for now; a larger RAM maps to
// an SRAM macro when the area table calls for it.
module pdram #(
    parameter RAM_BYTES = 1024
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

    reg       rv1;
    reg [7:0] rd1;

    always @(posedge clk) begin
        if (mem_req && mem_we && hit) ram[off[AW-1:0]] <= mem_wdata;
        rd1        <= hit ? ram[off[AW-1:0]] : 8'h00;
        rv1        <= rst ? 1'b0 : (mem_req && !mem_we);
        mem_rvalid <= rst ? 1'b0 : rv1;
        mem_rdata  <= rd1;
    end
endmodule
