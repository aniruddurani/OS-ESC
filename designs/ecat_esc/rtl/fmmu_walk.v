// Per-offset lookup over the segments of fmmu_plan, in three registered stages:
//   W1  for each FMMU: does its segment cover the offset (two 11-bit compares), per direction
//   W2  the lowest-numbered covering FMMU of each direction, and its seg_base
//   W3  address = seg_base + offset (one 16-bit adder)
// One request per clock. The result for a request presented before clock edge k is on the
// outputs after edge k + 2, so it is visible three samples after the request: res_valid
// with the request's offset in res_off, the read and write hits and their addresses (0
// without a hit). The segment inputs must stay constant while requests are in flight.
module fmmu_walk #(
    parameter N_FMMU = 3        // 0..16
) (
    input         clk,
    input         rst,

    input  [((N_FMMU == 0) ? 1 : N_FMMU)-1:0]      hr,
    input  [((N_FMMU == 0) ? 1 : N_FMMU)-1:0]      hw,
    input  [11*((N_FMMU == 0) ? 1 : N_FMMU)-1:0]   seg_start,
    input  [11*((N_FMMU == 0) ? 1 : N_FMMU)-1:0]   seg_end,
    input  [16*((N_FMMU == 0) ? 1 : N_FMMU)-1:0]   seg_base,

    input         req_valid,
    input  [10:0] req_off,

    output reg        res_valid,
    output reg [10:0] res_off,
    output reg        rd_hit,
    output reg [15:0] rd_addr,
    output reg        wr_hit,
    output reg [15:0] wr_addr
);
    localparam NF = (N_FMMU == 0) ? 1 : N_FMMU;

    // W1
    reg          v1;
    reg [10:0]   n1;
    reg [NF-1:0] rh1, wh1;

    genvar g;
    generate
        for (g = 0; g < NF; g = g + 1) begin : g_cmp
            wire in_seg = (req_off >= seg_start[11*g +: 11]) && (req_off < seg_end[11*g +: 11]);
            always @(posedge clk) begin
                rh1[g] <= (N_FMMU != 0) && hr[g] && in_seg;
                wh1[g] <= (N_FMMU != 0) && hw[g] && in_seg;
            end
        end
    endgenerate

    // W2: lowest-numbered hit and its base
    reg        r_found, w_found;
    reg [15:0] r_base, w_base;
    integer    k;
    always @* begin
        r_found = 1'b0; w_found = 1'b0; r_base = 16'd0; w_base = 16'd0;
        for (k = 0; k < NF; k = k + 1) begin
            if (!r_found && rh1[k]) begin
                r_found = 1'b1;
                r_base  = seg_base[16*k +: 16];
            end
            if (!w_found && wh1[k]) begin
                w_found = 1'b1;
                w_base  = seg_base[16*k +: 16];
            end
        end
    end

    reg        v2, rf2, wf2;
    reg [10:0] n2;
    reg [15:0] rb2, wb2;

    always @(posedge clk) begin
        v1 <= req_valid && !rst;
        n1 <= req_off;

        v2  <= v1 && !rst;
        n2  <= n1;
        rf2 <= r_found;
        wf2 <= w_found;
        rb2 <= r_base;
        wb2 <= w_base;

        res_valid <= v2 && !rst;
        res_off   <= n2;
        rd_hit    <= rf2;
        wr_hit    <= wf2;
        rd_addr   <= rf2 ? rb2 + {5'd0, n2} : 16'd0;
        wr_addr   <= wf2 ? wb2 + {5'd0, n2} : 16'd0;
    end
endmodule
