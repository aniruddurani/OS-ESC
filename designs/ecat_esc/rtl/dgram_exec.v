// Datagram engine, physical addressing (logical addressing is added with the FMMUs).
//
// Sits on the parser's tagged byte stream and rewrites it one byte per input
// byte, one clock later. The decision for a datagram is made early:
//   dg_start     the command and address are known: is this slave addressed?
//   dg_hdr_done  the length is known: does the access fit the registers/RAM?
// then each data byte is read from or written to memory on the byte-wide
// memory port as it passes (cut-through; see the ledger ruling on bad frames).
// Reads are prefetched one byte ahead so the byte is ready when it arrives:
// the design assumes at least 5 core clocks between stream bytes (a line-rate
// byte is 8 clocks at 100 MHz).
//
// Working counter: read +1, write +1, read-write +3 (read +1, write +2), added
// to the incoming value across the two WKC bytes. The position field of
// auto-increment and broadcast commands is incremented as it passes.
// `bad_frame` is sampled with the last byte of a frame and becomes the stream's
// `err` flag, which poisons the transmitted FCS.
module dgram_exec #(
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

    output reg        ev_dg,          // a datagram finished (its WKC passed)
    output reg        ev_addressed,   // ... and this slave was addressed by it
    output reg  [1:0] ev_wkc_inc      // ... and added this to the working counter
);
    localparam [1:0] K_HDR = 2'd1, K_DATA = 2'd2, K_WKC = 2'd3;
    localparam [31:0] RAM_LIM = RAM_BYTES;

    // command decode (the parser holds dg_cmd steady for the whole datagram)
    wire cmd_ap = (dg_cmd >= 8'd1) && (dg_cmd <= 8'd3);
    wire cmd_fp = (dg_cmd >= 8'd4) && (dg_cmd <= 8'd6);
    wire cmd_b  = (dg_cmd >= 8'd7) && (dg_cmd <= 8'd9);
    wire is_rd  = (dg_cmd == 8'd1) || (dg_cmd == 8'd3) || (dg_cmd == 8'd4) ||
                  (dg_cmd == 8'd6) || (dg_cmd == 8'd7) || (dg_cmd == 8'd9);
    wire is_wr  = (dg_cmd == 8'd2) || (dg_cmd == 8'd3) || (dg_cmd == 8'd5) ||
                  (dg_cmd == 8'd6) || (dg_cmd == 8'd8) || (dg_cmd == 8'd9);
    wire incr   = cmd_ap || cmd_b;

    reg        addressed;
    reg        ok;              // addressed, and the whole access fits
    reg [15:0] ado;
    reg        carry;
    reg [7:0]  rbuf;            // prefetched memory byte
    reg        pf_go;
    reg [15:0] pf_addr;

    // does [ado, ado + len) fit the registers or the RAM?
    wire [16:0] a_end   = {1'b0, ado} + {6'd0, dg_len};
    wire        reg_ok  = (a_end <= 17'h01000);
    wire [16:0] ram_end = a_end - 17'h01000;
    wire        ram_ok  = (ado >= 16'h1000) && ({15'd0, ram_end} <= RAM_LIM);
    wire        fits    = reg_ok || ram_ok;

    wire [1:0] inc = {1'b0, ok && is_rd} + (ok && is_wr ? (is_rd ? 2'd2 : 2'd1) : 2'd0);
    wire [8:0] wsum = {1'b0, p_data} + {7'd0, inc};

    wire [15:0] next_off = {5'd0, p_pos} + 16'd1;

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
            carry     <= 1'b0;
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

            if (dg_start) begin
                ado       <= dg_addr[31:16];
                addressed <= (cmd_ap && dg_addr[15:0] == 16'd0) ||
                             (cmd_fp && dg_addr[15:0] == station_addr) || cmd_b;
            end
            if (dg_hdr_done) begin
                ok <= addressed && fits;
                if (addressed && fits && is_rd && dg_len != 11'd0) begin
                    pf_go   <= 1'b1;
                    pf_addr <= ado;
                end
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
                        if (ok && is_rd) o_data <= cmd_b ? (rbuf | p_data) : rbuf;
                        if (ok && is_wr) begin
                            mem_req   <= 1'b1;
                            mem_we    <= 1'b1;
                            mem_addr  <= ado + {5'd0, p_pos};
                            mem_wdata <= p_data;
                        end
                        if (ok && is_rd && ({1'b0, next_off[10:0]} < {1'b0, dg_len})) begin
                            pf_go   <= 1'b1;
                            pf_addr <= ado + next_off;
                        end
                    end
                    K_WKC: if (p_pos == 11'd0) begin
                        o_data <= wsum[7:0];
                        carry  <= wsum[8];
                    end else begin
                        o_data       <= p_data + {7'd0, carry};
                        ev_dg        <= 1'b1;
                        ev_addressed <= addressed;
                        ev_wkc_inc   <= inc;
                    end
                    default: ;
                endcase
            end
        end
    end
endmodule
