// Diagnostics: counters and a datagram trace buffer. It only watches (it has no
// connection to the datapath's data and never drives it) and is read through the
// byte-wide memory port at 0x0E00..0x0EFF with the usual 2-clock read latency.
// Writes are ignored. Layout (counters are 16-bit little-endian slots, the value
// zero-extended from CNT_W bits, saturating):
//   0x0E00 + 2*k   k = 0 frames, 1 rx_errors, 2 crc_failures, 3 runts, 4 oversize,
//                  5 non_ethercat, 6 malformed, 7 datagrams, 8 datagrams addressed
//                  to this slave, 9 working-counter increments, 10 forwarding latency
//                  (core clocks from first byte in to first byte out), 11 trace entries
//                  written (16-bit, wraps)
//   0x0E40 + 16*e  trace entry e of the last TRACE_DEPTH datagrams (circular):
//                  +0 command, +1..4 address (as received), +5..6 length,
//                  +7 {5'b0, addressed, wkc increment[1:0]}, +8..9 core-clock
//                  timestamp, +10..15 zero.
module diag #(
    parameter TRACE_DEPTH = 8,      // power of two, at most 8
    parameter CNT_W       = 16      // counter width, at most 16
) (
    input         clk,
    input         rst,

    input         in_first_seen,    // first byte of a frame enters the core
    input         out_first_seen,   // first byte of a frame leaves it
    input         ev_end,           // frame events, all with ev_end
    input         ev_runt,
    input         ev_oversize,
    input         ev_non_ethercat,
    input         ev_malformed,
    input         ev_rx_err,
    input         ev_crc_bad,
    input         ev_dg,            // datagram events
    input         ev_addressed,
    input   [1:0] ev_wkc_inc,
    input   [7:0] dg_cmd,           // the datagram's header, held until the next one
    input  [31:0] dg_addr,
    input  [10:0] dg_len,

    input         mem_req,
    input         mem_we,
    input   [7:0] mem_addr,         // low byte of the address; the top level decodes 0x0E
    output reg [7:0] mem_rdata,
    output reg       mem_rvalid
);
    localparam TAW = $clog2(TRACE_DEPTH);
    localparam [CNT_W-1:0] MAXV = {CNT_W{1'b1}};

    reg [CNT_W-1:0] c_frames, c_rx_errors, c_crc_failures, c_runts, c_oversize;
    reg [CNT_W-1:0] c_non_ethercat, c_malformed, c_datagrams, c_datagrams_addressed;
    reg [CNT_W-1:0] c_wkc_increments;
    reg [15:0]      latency;
    reg [15:0]      trace_count;
    reg [15:0]      stamp;
    reg [15:0]      lat_cnt;
    reg             lat_run;
    reg [127:0]     trace_mem [0:TRACE_DEPTH-1];
    reg [TAW-1:0]   wr_idx;

    // saturating add of a 2-bit amount
    function [CNT_W-1:0] sat_add(input [CNT_W-1:0] c, input [1:0] n);
        reg [CNT_W+1:0] sum;
        begin
            sum = {2'b00, c} + {{CNT_W{1'b0}}, n};
            sat_add = (sum > {2'b00, MAXV}) ? MAXV : sum[CNT_W-1:0];
        end
    endfunction

    always @(posedge clk) begin
        if (rst) begin
            c_frames <= {CNT_W{1'b0}}; c_rx_errors <= {CNT_W{1'b0}};
            c_crc_failures <= {CNT_W{1'b0}}; c_runts <= {CNT_W{1'b0}};
            c_oversize <= {CNT_W{1'b0}}; c_non_ethercat <= {CNT_W{1'b0}};
            c_malformed <= {CNT_W{1'b0}}; c_datagrams <= {CNT_W{1'b0}};
            c_datagrams_addressed <= {CNT_W{1'b0}}; c_wkc_increments <= {CNT_W{1'b0}};
            latency <= 16'd0; trace_count <= 16'd0; stamp <= 16'd0; lat_cnt <= 16'd0;
            lat_run <= 1'b0; wr_idx <= {TAW{1'b0}};
        end else begin
            stamp <= stamp + 16'd1;
            if (ev_end)          c_frames       <= sat_add(c_frames, 2'd1);
            if (ev_end && ev_rx_err)       c_rx_errors    <= sat_add(c_rx_errors, 2'd1);
            if (ev_end && ev_crc_bad)      c_crc_failures <= sat_add(c_crc_failures, 2'd1);
            if (ev_end && ev_runt)         c_runts        <= sat_add(c_runts, 2'd1);
            if (ev_end && ev_oversize)     c_oversize     <= sat_add(c_oversize, 2'd1);
            if (ev_end && ev_non_ethercat) c_non_ethercat <= sat_add(c_non_ethercat, 2'd1);
            if (ev_end && ev_malformed)    c_malformed    <= sat_add(c_malformed, 2'd1);
            if (ev_dg) begin
                c_datagrams <= sat_add(c_datagrams, 2'd1);
                if (ev_addressed) c_datagrams_addressed <= sat_add(c_datagrams_addressed, 2'd1);
                c_wkc_increments <= sat_add(c_wkc_increments, ev_wkc_inc);
                trace_mem[wr_idx] <= {48'd0, stamp, 5'd0, ev_addressed, ev_wkc_inc,
                                      5'd0, dg_len, dg_addr, dg_cmd};
                wr_idx      <= wr_idx + {{(TAW - 1){1'b0}}, 1'b1};
                trace_count <= trace_count + 16'd1;
            end
            // forwarding latency: first byte in to first byte out
            if (in_first_seen) begin
                lat_cnt <= 16'd1;
                lat_run <= 1'b1;
            end else if (lat_run) begin
                lat_cnt <= lat_cnt + 16'd1;
            end
            if (out_first_seen && lat_run) begin
                latency <= lat_cnt;
                lat_run <= 1'b0;
            end
        end
    end

    // --- read side -------------------------------------------------------------
    reg [15:0] slot;
    reg [7:0]  rd;
    wire [3:0] entry_byte = mem_addr[3:0];
    // entry index = (addr - 0x40) / 16, which for 0x40..0xBF is the top bit flipped
    wire [2:0] entry_idx  = {~mem_addr[6], mem_addr[5:4]};
    generate
        if (TAW < 3) begin : g_idx_unused
            wire _unused_ok = &{1'b0, entry_idx[2:TAW]};
        end
    endgenerate
    wire       in_trace   = (mem_addr >= 8'h40) && (mem_addr < 8'h40 + 8'd16 * TRACE_DEPTH);

    always @* begin
        slot = 16'd0;
        case (mem_addr[5:1])
            5'd0:  slot = {{(16 - CNT_W){1'b0}}, c_frames};
            5'd1:  slot = {{(16 - CNT_W){1'b0}}, c_rx_errors};
            5'd2:  slot = {{(16 - CNT_W){1'b0}}, c_crc_failures};
            5'd3:  slot = {{(16 - CNT_W){1'b0}}, c_runts};
            5'd4:  slot = {{(16 - CNT_W){1'b0}}, c_oversize};
            5'd5:  slot = {{(16 - CNT_W){1'b0}}, c_non_ethercat};
            5'd6:  slot = {{(16 - CNT_W){1'b0}}, c_malformed};
            5'd7:  slot = {{(16 - CNT_W){1'b0}}, c_datagrams};
            5'd8:  slot = {{(16 - CNT_W){1'b0}}, c_datagrams_addressed};
            5'd9:  slot = {{(16 - CNT_W){1'b0}}, c_wkc_increments};
            5'd10: slot = latency;
            5'd11: slot = trace_count;
            default: slot = 16'd0;
        endcase
        if (in_trace)
            rd = trace_mem[entry_idx[TAW-1:0]][8*entry_byte +: 8];
        else if (mem_addr < 8'h40)
            rd = mem_addr[0] ? slot[15:8] : slot[7:0];
        else
            rd = 8'h00;
    end

    reg       rv1, rv2;
    reg [7:0] rd1, rd2;
    always @(posedge clk) begin
        rv1        <= rst ? 1'b0 : (mem_req && !mem_we);
        rd1        <= rd;
        rv2        <= rst ? 1'b0 : rv1;
        rd2        <= rd1;
        mem_rvalid <= rst ? 1'b0 : rv2;
        mem_rdata  <= rd2;
    end
endmodule
