// Fieldbus memory management units: map logical addresses to physical ones.
//
// `cfg` is the regfile's FMMU configuration bus, 16 bytes per FMMU:
//   +0..3 logical start, +4..5 length, +8..9 physical start, +11 type
//   (bit 0 read, bit 1 write), +12 bit 0 active. Bit-granular mapping (the
//   start/stop bit bytes) is not supported: mapping is byte-wise.
//
// When a logical datagram's length is known, `calc` pulses and each FMMU decides
// once for the whole datagram whether it contributes: it must be active, overlap
// the datagram, allow the datagram's direction and the mapped chunk must fit the
// registers or the RAM. After that two lookups answer, for a byte offset into the
// datagram, which physical address a read or write goes to. Where windows of the
// same direction overlap, the lowest-numbered FMMU owns the byte.
module fmmu #(
    parameter N_FMMU    = 3,        // 0..16
    parameter RAM_BYTES = 1024
) (
    input         clk,
    input         rst,
    input  [8*16*((N_FMMU == 0) ? 1 : N_FMMU)-1:0] cfg,

    input         calc,
    input  [31:0] dg_addr,
    input  [10:0] dg_len,
    input         is_rd,
    input         is_wr,

    input  [10:0] q0_off,
    output reg        q0_rd_hit,
    output reg        q0_wr_hit,
    output reg [15:0] q0_wr_addr,

    input  [10:0] q1_off,
    output reg        q1_rd_hit,
    output reg [15:0] q1_rd_addr
);
    localparam NF = (N_FMMU == 0) ? 1 : N_FMMU;
    localparam [31:0] RAM_LIM = RAM_BYTES;

    wire [31:0] f_start [0:NF-1];
    wire [15:0] f_len   [0:NF-1];
    wire [15:0] f_phys  [0:NF-1];
    wire        f_rd    [0:NF-1];
    wire        f_wr    [0:NF-1];
    wire        f_act   [0:NF-1];
    wire        f_hr_n  [0:NF-1];     // this FMMU would serve the datagram's reads
    wire        f_hw_n  [0:NF-1];

    reg [NF-1:0] hr, hw;

    genvar g;
    generate
        for (g = 0; g < NF; g = g + 1) begin : g_f
            assign f_start[g] = cfg[128*g + 0 +: 32];
            assign f_len[g]   = cfg[128*g + 32 +: 16];
            assign f_phys[g]  = cfg[128*g + 64 +: 16];
            assign f_rd[g]    = cfg[128*g + 88];
            assign f_wr[g]    = cfg[128*g + 89];
            assign f_act[g]   = cfg[128*g + 96];
            // not used: start/stop bit bytes, physical start bit, reserved bits
            wire _unused_ok = &{1'b0, cfg[128*g + 48 +: 16], cfg[128*g + 80 +: 8],
                                cfg[128*g + 90 +: 6], cfg[128*g + 97 +: 31]};

            // overlap of the datagram [dg_addr, +dg_len) and the window
            wire [32:0] ds  = {1'b0, dg_addr};
            wire [32:0] de  = ds + {22'd0, dg_len};
            wire [32:0] ws  = {1'b0, f_start[g]};
            wire [32:0] we  = ws + {17'd0, f_len[g]};
            wire [32:0] lo  = (ds > ws) ? ds : ws;
            wire [32:0] hi  = (de < we) ? de : we;
            wire        ovl = f_act[g] && (f_len[g] != 16'd0) && (lo < hi);
            wire [15:0] off = lo[15:0] - ws[15:0];           // below the window length
            wire [10:0] cl  = hi[10:0] - lo[10:0];           // chunk length (< 2048)
            wire [16:0] plo = {1'b0, f_phys[g]} + {1'b0, off};
            wire [16:0] phi = plo + {6'd0, cl};
            wire [16:0] ram_end = phi - 17'h01000;
            wire        fits = (phi <= 17'h01000) ||
                               ((plo >= 17'h01000) && ({15'd0, ram_end} <= RAM_LIM));
            assign f_hr_n[g] = ovl && fits && f_rd[g] && is_rd;
            assign f_hw_n[g] = ovl && fits && f_wr[g] && is_wr;

            always @(posedge clk) begin
                if (rst) begin
                    hr[g] <= 1'b0;
                    hw[g] <= 1'b0;
                end else if (calc) begin
                    hr[g] <= f_hr_n[g];
                    hw[g] <= f_hw_n[g];
                end
            end
        end
    endgenerate

    // lookups: first matching FMMU per direction
    reg [31:0] x0, x1, d0, d1;
    integer    k;

    always @* begin
        x0 = dg_addr + {21'd0, q0_off};
        x1 = dg_addr + {21'd0, q1_off};
        q0_rd_hit = 1'b0; q0_wr_hit = 1'b0; q0_wr_addr = 16'd0;
        q1_rd_hit = 1'b0; q1_rd_addr = 16'd0;
        d0 = 32'd0; d1 = 32'd0;
        if (N_FMMU != 0) begin
            for (k = 0; k < NF; k = k + 1) begin
                d0 = x0 - f_start[k];
                d1 = x1 - f_start[k];
                if (!q0_rd_hit && hr[k] && x0 >= f_start[k] && d0 < {16'd0, f_len[k]}) begin
                    q0_rd_hit  = 1'b1;
                end
                if (!q0_wr_hit && hw[k] && x0 >= f_start[k] && d0 < {16'd0, f_len[k]}) begin
                    q0_wr_hit  = 1'b1;
                    q0_wr_addr = f_phys[k] + d0[15:0];
                end
                if (!q1_rd_hit && hr[k] && x1 >= f_start[k] && d1 < {16'd0, f_len[k]}) begin
                    q1_rd_hit  = 1'b1;
                    q1_rd_addr = f_phys[k] + d1[15:0];
                end
            end
        end
    end
endmodule
