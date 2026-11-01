// Process-data RAM behind the byte-wide memory port, addresses 0x1000 up.
// Read data is valid MEM_LAT = 3 clocks after the request, flagged by mem_rvalid.
// This wrapper picks the implementation: a flip-flop array below 1 KB, banks of the
// 1 KB SRAM macro from 1 KB up (RAM_BYTES a multiple of 1024, at most 16384).
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
    generate
        if (RAM_BYTES < 1024) begin : g_flop
            pdram_flop #(.RAM_BYTES(RAM_BYTES)) u_ram (
                .clk(clk), .rst(rst),
                .mem_req(mem_req), .mem_we(mem_we), .mem_addr(mem_addr), .mem_wdata(mem_wdata),
                .mem_rdata(mem_rdata), .mem_rvalid(mem_rvalid)
            );
        end else begin : g_macro
            if ((RAM_BYTES % 1024) != 0 || RAM_BYTES > 16384) begin : g_bad
                $error("pdram: RAM_BYTES must be below 1024 or a multiple of 1024 up to 16384");
            end
            pdram_macro #(.RAM_BYTES(RAM_BYTES)) u_ram (
                .clk(clk), .rst(rst),
                .mem_req(mem_req), .mem_we(mem_we), .mem_addr(mem_addr), .mem_wdata(mem_wdata),
                .mem_rdata(mem_rdata), .mem_rvalid(mem_rvalid)
            );
        end
    endgenerate
endmodule
