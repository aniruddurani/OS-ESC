// Per-FMMU plan for a logical datagram, as a six-stage pipeline with one adder or
// compare per stage (the original fmmu.v did all of it in one cycle).
//
// `cfg` is the regfile's FMMU configuration bus, 16 bytes per FMMU (see fmmu.v). The
// datagram's address (`dg_addr`, held from the parser's dg_start on) and length (`dg_len`,
// valid when `plan_len` pulses and held until the next datagram) and the direction flags
// go in; the stages run free from the held inputs. `cfg` and `dg_addr` must be stable at least
// four clocks before `plan_len` (the parser holds the address two header bytes ahead of the
// length). Seven clocks after the `plan_len` edge
// `plan_done` pulses and the segments below are loaded and held until the next plan:
//   hr[i], hw[i]            FMMU i serves this datagram's reads / writes: it is active, has
//                           a nonzero length, overlaps the datagram, allows the direction,
//                           and the mapped chunk fits the registers or the RAM
//   seg_start[11i +: 11]    first datagram byte offset in the chunk
//   seg_end[11i +: 11]      one past the last offset in the chunk
//   seg_base[16i +: 16]     physical address of offset 0 (the chunk start minus seg_start), so
//                           the address of offset n is seg_base + n
module fmmu_plan #(
    parameter N_FMMU    = 3,        // 0..16
    parameter RAM_BYTES = 1024
) (
    input         clk,
    input         rst,
    input  [8*16*((N_FMMU == 0) ? 1 : N_FMMU)-1:0] cfg,

    input         plan_len,
    input  [31:0] dg_addr,
    input  [10:0] dg_len,
    input         is_rd,
    input         is_wr,

    output reg    plan_done,
    output reg [((N_FMMU == 0) ? 1 : N_FMMU)-1:0]      hr,
    output reg [((N_FMMU == 0) ? 1 : N_FMMU)-1:0]      hw,
    output reg [11*((N_FMMU == 0) ? 1 : N_FMMU)-1:0]   seg_start,
    output reg [11*((N_FMMU == 0) ? 1 : N_FMMU)-1:0]   seg_end,
    output reg [16*((N_FMMU == 0) ? 1 : N_FMMU)-1:0]   seg_base
);
    localparam NF = (N_FMMU == 0) ? 1 : N_FMMU;
    localparam [31:0] RAM_TOP = 32'h0000_1000 + RAM_BYTES;

    reg [5:0] pd;               // plan_len delayed through the pipeline
    always @(posedge clk) begin
        pd        <= {pd[4:0], plan_len && !rst};
        plan_done <= pd[5] && !rst;
    end

    // Local copy of the datagram address (it also fans out to every FMMU) and the increment of its
    // upper bits, ready before the length arrives: the datagram end is then a short add of the
    // low 11 bits and a select, not a 32-bit carry chain.
    reg [31:0] da0;
    reg [21:0] dai;
    always @(posedge clk) begin
        da0 <= dg_addr;
        dai <= {1'b0, da0[31:11]} + 22'd1;
    end

    genvar g;
    generate
        for (g = 0; g < NF; g = g + 1) begin : g_f
            wire [31:0] f_start = cfg[128*g + 0 +: 32];
            wire [15:0] f_len   = cfg[128*g + 32 +: 16];
            wire [15:0] f_phys  = cfg[128*g + 64 +: 16];
            wire        f_rd    = cfg[128*g + 88];
            wire        f_wr    = cfg[128*g + 89];
            wire        f_act   = cfg[128*g + 96];
            // not used: start/stop bit bytes, physical start bit, reserved bits
            wire _unused_ok = &{1'b0, cfg[128*g + 48 +: 16], cfg[128*g + 80 +: 8],
                                cfg[128*g + 90 +: 6], cfg[128*g + 97 +: 31]};

            // stage 1: window end, datagram end, which start is later
            reg [32:0] we1, de1;
            reg        gt1, act1, rd1, wr1;
            reg [31:0] ds1, ws1;
            reg [15:0] ph1;
            // Is the datagram end before the window end? de < we is dg_len < we - dg_addr. The
            // difference needs only the window and the address, both known two header bytes
            // before the length, so it is worked out ahead; stage 1 compares the length.
            wire       [34:0] wd;                 // we - dg_addr, two clocks after the operands
            reg               wd_neg, wd_big, lt1;
            reg        [10:0] wd_lo;
            // Physical start of the overlap: the window's plus (dg_addr - window start) when the
            // datagram starts later; the sum is worked out ahead of the length.
            wire       [15:0] dw;                 // dg_addr - window start
            wire       [16:0] pp;                 // window physical start + dw
            reg        [16:0] pp2, pp3;
            // stage 2: overlap start and end
            reg [32:0] lo2, hi2;
            reg [10:0] ds2;
            reg [15:0] ph2;
            reg        gt2;
            reg        act2, rd2, wr2;
            // stage 3: overlap test, chunk offsets
            reg        ovl3, rd3, wr3;
            reg [10:0] ss3, sl3;
            reg [15:0] ph3;
            reg        gt3;
            // stage 4: chunk end offset, physical start
            reg        ovl4, rd4, wr4;
            reg [10:0] ss4, sl4, se4;
            reg [16:0] plo4;
            // stage 5: physical end
            reg        ovl5, rd5, wr5;
            reg [10:0] ss5, se5;
            reg [16:0] plo5, phi5;
            // stage 6: fits, direction
            reg        hr6, hw6;
            reg [10:0] ss6, se6;
            reg [15:0] bs5, bs6;

            addsub_cs #(.W(35), .SUB(1)) u_wd (.clk(clk), .a({2'b00, we1}), .b({3'b000, da0}), .y(wd));
            addsub_cs #(.W(16), .SUB(1)) u_dw (.clk(clk), .a(da0[15:0]), .b(f_start[15:0]), .y(dw));
            addsub_cs #(.W(17), .SUB(0)) u_pp (.clk(clk), .a({1'b0, f_phys}), .b({1'b0, dw}), .y(pp));
            wire [11:0] dsum = {1'b0, da0[10:0]} + {1'b0, dg_len};

            wire fits5 = (phi5 <= 17'h01000) ||
                         ((plo5 >= 17'h01000) && (phi5 >= 17'h01000) && ({15'd0, phi5} <= RAM_TOP));

            always @(posedge clk) begin
                we1  <= {1'b0, f_start} + {17'd0, f_len};
                de1  <= {dsum[11] ? dai : {1'b0, da0[31:11]}, dsum[10:0]};
                gt1  <= (da0 > f_start);
                wd_neg <= wd[34];
                wd_big <= !wd[34] && (|wd[33:11]);
                wd_lo  <= wd[10:0];
                lt1    <= !wd_neg && (wd_big || (dg_len < wd_lo));
                ds1  <= da0;
                ws1  <= f_start;
                act1 <= f_act && (f_len != 16'd0);
                rd1  <= f_rd && is_rd;
                wr1  <= f_wr && is_wr;
                ph1  <= f_phys;

                lo2  <= gt1 ? {1'b0, ds1} : {1'b0, ws1};
                hi2  <= lt1 ? de1 : we1;
                ds2  <= ds1[10:0];
                pp2  <= pp;
                gt2  <= gt1;
                act2 <= act1;
                rd2  <= rd1;
                wr2  <= wr1;
                ph2  <= ph1;

                ovl3 <= act2 && (lo2 < hi2);
                ss3  <= lo2[10:0] - ds2;
                sl3  <= hi2[10:0] - lo2[10:0];
                pp3  <= pp2;
                gt3  <= gt2;
                rd3  <= rd2;
                wr3  <= wr2;
                ph3  <= ph2;

                ovl4 <= ovl3;
                se4  <= ss3 + sl3;
                ss4  <= ss3;
                sl4  <= sl3;
                plo4 <= gt3 ? pp3 : {1'b0, ph3};
                rd4  <= rd3;
                wr4  <= wr3;

                ovl5 <= ovl4;
                ss5  <= ss4;
                se5  <= se4;
                plo5 <= plo4;
                phi5 <= plo4 + {6'd0, sl4};
                bs5  <= plo4[15:0] - {5'd0, ss4};
                rd5  <= rd4;
                wr5  <= wr4;

                hr6  <= ovl5 && fits5 && rd5;
                hw6  <= ovl5 && fits5 && wr5;
                ss6  <= ss5;
                se6  <= se5;
                bs6  <= bs5;
            end

            // the segment is held from plan_done until the next plan
            always @(posedge clk) begin
                if (rst) begin
                    hr[g] <= 1'b0;
                    hw[g] <= 1'b0;
                end else if (pd[5]) begin
                    hr[g] <= (N_FMMU != 0) && hr6;
                    hw[g] <= (N_FMMU != 0) && hw6;
                end
                if (pd[5]) begin
                    seg_start[11*g +: 11] <= ss6;
                    seg_end[11*g +: 11]   <= se6;
                    seg_base[16*g +: 16]  <= bs6;
                end
            end
        end
    endgenerate
endmodule
