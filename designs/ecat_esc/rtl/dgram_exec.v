// Datagram engine: physical and logical addressing.
//
// Sits on the parser's tagged byte stream and rewrites it one byte per input
// byte, one clock later. The decision for a datagram is made early:
//   dg_start     the command and address are known: is this slave addressed?
//   dg_len_done  the length is known: does the access fit the registers/RAM? The first
//                read is started here, two header bytes before the first data byte
//   dg_hdr_done  the header is complete and fits the frame: writes may land
// then each data byte is read from or written to memory on the byte-wide
// memory port as it passes (cut-through; see the ledger ruling on bad frames).
// Reads are prefetched one byte ahead so the byte is ready when it arrives:
// the design assumes at least 6 core clocks between stream bytes (a line-rate
// byte is 8 clocks at 100 MHz).
//
// Working counter: read +1, write +1, read-write +3 (read +1, write +2), added
// to the incoming value across the two WKC bytes. The position field of
// auto-increment and broadcast commands is incremented as it passes.
// `bad_frame` is sampled with the last byte of a frame and becomes the stream's
// `err` flag, which poisons the transmitted FCS.
module dgram_exec #(
    parameter N_FMMU    = 3,        // 0..16
    parameter RAM_BYTES = 1024
) (
    input             clk,
    input             rst,

    input             p_valid,
    input       [7:0] p_data,
    input             p_first,
    input             p_last,
    input       [1:0] p_kind,
    input      [10:0] p_pos,
    input             dg_start,
    input             dg_len_done,
    input             dg_hdr_done,
    input       [7:0] dg_cmd,
    input      [31:0] dg_addr,
    input      [10:0] dg_len,
    input      [15:0] station_addr,
    input             bad_frame,

    output reg        mem_req,
    output reg        mem_we,
    output reg [15:0] mem_addr,
    output reg  [7:0] mem_wdata,
    input       [7:0] mem_rdata,
    input             mem_rvalid,

    output reg        o_valid,
    output reg  [7:0] o_data,
    output reg        o_first,
    output reg        o_last,
    output reg        o_err,

    // the FMMU configuration (logical commands), 16 bytes per FMMU
    input  [8*16*((N_FMMU == 0) ? 1 : N_FMMU)-1:0] fmmu_cfg,

    output reg        ev_dg,          // a datagram finished (its WKC passed)
    output reg        ev_addressed,   // ... and this slave was addressed by it
    output reg  [1:0] ev_wkc_inc      // ... and added this to the working counter
);
    localparam [1:0] K_HDR = 2'd1, K_DATA = 2'd2, K_WKC = 2'd3;
    localparam [31:0] RAM_LIM = RAM_BYTES;

    // command decode: the parser holds dg_cmd steady for the whole datagram, from header
    // byte 0, many clocks before any of these is used, so the flags are registered
    wire d_ap = (dg_cmd >= 8'd1) && (dg_cmd <= 8'd3);
    wire d_fp = (dg_cmd >= 8'd4) && (dg_cmd <= 8'd6);
    wire d_b  = (dg_cmd >= 8'd7) && (dg_cmd <= 8'd9);
    wire d_l  = (dg_cmd >= 8'd10) && (dg_cmd <= 8'd12);
    wire d_rd = (dg_cmd == 8'd1) || (dg_cmd == 8'd3) || (dg_cmd == 8'd4) ||
                (dg_cmd == 8'd6) || (dg_cmd == 8'd7) || (dg_cmd == 8'd9) ||
                (dg_cmd == 8'd10) || (dg_cmd == 8'd12);
    wire d_wr = (dg_cmd == 8'd2) || (dg_cmd == 8'd3) || (dg_cmd == 8'd5) ||
                (dg_cmd == 8'd6) || (dg_cmd == 8'd8) || (dg_cmd == 8'd9) ||
                (dg_cmd == 8'd11) || (dg_cmd == 8'd12);
    reg cmd_l, cmd_b, is_rd, is_wr, incr;
    always @(posedge clk) begin
        cmd_l <= d_l;
        cmd_b <= d_b;
        is_rd <= d_rd;
        is_wr <= d_wr;
        incr  <= d_ap || d_b;
    end

    reg        addressed;
    reg        ok;              // addressed, and the whole access fits
    reg [15:0] ado;
    reg        carry;
    reg [7:0]  rbuf;            // prefetched memory byte
    reg        pf_go;
    reg [15:0] pf_addr;

    // Does [ado, ado + len) fit the registers or the RAM? Worked out in three registered
    // stages from ado and dg_len (both held for the whole datagram), one adder or compare
    // each: fits_r is valid three clocks after dg_len_done, and len_d[2] pulses then.
    reg [16:0] a_end_r, ram_end_r;
    reg        reg_ok_r, ram_lo_r, fits_r;
    reg [2:0]  len_d;
    always @(posedge clk) begin
        a_end_r   <= {1'b0, ado} + {6'd0, dg_len};
        reg_ok_r  <= (a_end_r <= 17'h01000);
        ram_end_r <= a_end_r - 17'h01000;
        ram_lo_r  <= (ado >= 16'h1000);
        fits_r    <= reg_ok_r || (ram_lo_r && ({15'd0, ram_end_r} <= RAM_LIM));
        len_d     <= {len_d[1:0], dg_len_done && !rst};
    end

    reg [15:0] wctr;            // physical commands: address of the data byte now passing
    reg [15:0] rctr;            // physical commands: address of the next byte to prefetch

    reg        rd_seen, wr_seen;

    wire rd_ok  = cmd_l ? rd_seen : (ok && is_rd);
    wire wr_ok  = cmd_l ? wr_seen : (ok && is_wr);
    wire [1:0] inc = {1'b0, rd_ok} + (wr_ok ? (is_rd ? 2'd2 : 2'd1) : 2'd0);

    // Logical commands: the access queue plans the datagram over the FMMUs, walks the
    // offsets ahead of the stream and fetches the read bytes; it gets the memory port
    // whenever a write is not using it.
    wire        acc_rd_req, acc_rd_hit, acc_wr_hit;
    wire [15:0] acc_rd_addr, acc_wr_addr;
    wire  [7:0] acc_rd_data;
    wire        data_now = p_valid && (p_kind == K_DATA) && cmd_l;
    wire        acc_ack  = acc_rd_req && !(data_now && acc_wr_hit);

    dg_access #(.N_FMMU(N_FMMU), .RAM_BYTES(RAM_BYTES)) u_acc (
        .clk(clk), .rst(rst), .cfg(fmmu_cfg),
        .flush(dg_start), .plan_len(dg_len_done && cmd_l),
        .dg_addr(dg_addr), .dg_len(dg_len), .is_rd(is_rd), .is_wr(is_wr),
        .rd_req(acc_rd_req), .rd_req_addr(acc_rd_addr), .rd_ack(acc_ack),
        .mem_rvalid(mem_rvalid), .mem_rdata(mem_rdata),
        .consume(data_now),
        .head_rd_hit(acc_rd_hit), .head_rd_data(acc_rd_data),
        .head_wr_hit(acc_wr_hit), .head_wr_addr(acc_wr_addr)
    );

    // this byte: does it read or write, and where (physical commands: the command and the
    // address counters; logical: the head of the access queue)
    wire        rd_hit_i  = cmd_l ? acc_rd_hit : (ok && is_rd);
    wire        wr_hit_i  = cmd_l ? acc_wr_hit : (ok && is_wr);
    wire [15:0] wr_addr_i = cmd_l ? acc_wr_addr : wctr;
    wire        rd_hit_n  = ok && is_rd;         // physical: prefetch the next byte
    wire [15:0] rd_addr_n = rctr;
    wire [8:0] wsum = {1'b0, p_data} + {7'd0, inc};

    wire [10:0] next_off = p_pos + 11'd1;

    always @(posedge clk) begin
        mem_req      <= 1'b0;
        mem_we       <= 1'b0;
        pf_go        <= 1'b0;
        o_valid      <= 1'b0;
        o_first      <= 1'b0;
        o_last       <= 1'b0;
        o_err        <= 1'b0;
        ev_dg        <= 1'b0;
        ev_addressed <= 1'b0;
        ev_wkc_inc   <= 2'd0;
        if (rst) begin
            addressed <= 1'b0;
            ok        <= 1'b0;
            ado       <= 16'd0;
            wctr      <= 16'd0;
            rctr      <= 16'd0;
            carry     <= 1'b0;
            rd_seen   <= 1'b0;
            wr_seen   <= 1'b0;
            rbuf      <= 8'd0;
            pf_addr   <= 16'd0;
            mem_addr  <= 16'd0;
            mem_wdata <= 8'd0;
            o_data    <= 8'd0;
        end else begin
            if (mem_rvalid) rbuf <= mem_rdata;

            if (pf_go) begin                       // issue a prefetch read
                mem_req  <= 1'b1;
                mem_addr <= pf_addr;
            end


            if (acc_ack) begin                     // logical: the next queued read
                mem_req  <= 1'b1;
                mem_addr <= acc_rd_addr;
            end

            if (dg_start) begin
                rd_seen   <= 1'b0;
                wr_seen   <= 1'b0;
                ado       <= dg_addr[31:16];
                addressed <= (d_ap && dg_addr[15:0] == 16'd0) ||
                             (d_fp && dg_addr[15:0] == station_addr) || d_b;
            end
            if (len_d[2] && addressed && fits_r && is_rd && dg_len != 11'd0) begin
                pf_go   <= 1'b1;                   // physical: start the first read early
                pf_addr <= ado;
                rctr    <= ado + 16'd1;
            end
            if (dg_hdr_done) begin
                ok   <= addressed && fits_r;
                wctr <= ado;
            end
            if (p_valid) begin
                o_valid <= 1'b1;
                o_first <= p_first;
                o_last  <= p_last;
                o_err   <= p_last && bad_frame;
                o_data  <= p_data;
                case (p_kind)
                    K_HDR: if (incr && p_pos == 11'd2) begin
                        o_data <= p_data + 8'd1;
                        carry  <= (p_data == 8'hFF);
                    end else if (incr && p_pos == 11'd3) begin
                        o_data <= p_data + {7'd0, carry};
                    end
                    K_DATA: begin
                        if (rd_hit_i) begin
                            o_data  <= cmd_b ? (rbuf | p_data) : (cmd_l ? acc_rd_data : rbuf);
                            rd_seen <= 1'b1;
                        end
                        if (wr_hit_i) begin
                            mem_req   <= 1'b1;
                            mem_we    <= 1'b1;
                            mem_addr  <= wr_addr_i;
                            mem_wdata <= p_data;
                            wr_seen   <= 1'b1;
                        end
                        wctr <= wctr + 16'd1;
                        if (rd_hit_n && (next_off < dg_len)) begin
                            pf_go   <= 1'b1;
                            pf_addr <= rd_addr_n;
                            rctr    <= rctr + 16'd1;
                        end
                    end
                    K_WKC: if (p_pos == 11'd0) begin
                        o_data <= wsum[7:0];
                        carry  <= wsum[8];
                    end else begin
                        o_data       <= p_data + {7'd0, carry};
                        ev_dg        <= 1'b1;
                        ev_addressed <= cmd_l ? (rd_seen || wr_seen) : addressed;
                        ev_wkc_inc   <= inc;
                    end
                    default: ;
                endcase
            end
        end
    end
endmodule
