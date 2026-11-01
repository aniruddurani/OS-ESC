// Process-data RAM behind the byte-wide memory port, addresses 0x1000 up.
// Read data is valid MEM_LAT = 3 clocks after the request, flagged by mem_rvalid.
// This wrapper picks the implementation: a flip-flop array below 1 KB.
module pdram #(
    parameter RAM_BYTES = 1024
) (
    input            clk,
    input            rst,
    input            mem_req,
    input            mem_we,
    input     [15:0] mem_addr,
    input     [7:0]  mem_wdata,
    output     [7:0] mem_rdata,
    output           mem_rvalid
);
    pdram_flop #(.RAM_BYTES(RAM_BYTES)) u_ram (
        .clk(clk), .rst(rst),
        .mem_req(mem_req), .mem_we(mem_we), .mem_addr(mem_addr), .mem_wdata(mem_wdata),
        .mem_rdata(mem_rdata), .mem_rvalid(mem_rvalid)
    );
endmodule
