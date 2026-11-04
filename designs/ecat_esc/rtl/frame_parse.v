// Frame parser. Watches the received byte stream (valid/data/first/last/err,
// FCS included) and tags each byte with what it is, without delaying it:
//   o_kind  0 other (Ethernet header, EtherCAT header, pad, FCS)
//           1 datagram header byte   2 datagram data byte   3 working-counter byte
//   o_pos   index within the field (header 0..9, data 0..len-1, wkc 0..1)
// and decodes each datagram header as it arrives:
//   dg_start     one cycle after header byte 5: dg_cmd, dg_idx, dg_addr are valid
//   dg_len_done  one cycle after header byte 7: dg_len, dg_more, dg_circ are valid
//   dg_hdr_done  one cycle after header byte 9: the header is complete and the datagram
//                fits in the frame
// The values are held until the next datagram. At the end of every frame
// ev_end pulses together with o_last, with these flags:
//   ev_runt (< 64 bytes incl. FCS), ev_oversize (> 1518), ev_non_ethercat
//   (EtherType is not 0x88A4), ev_malformed (EtherCAT header or datagram lengths
//   disagree with the frame, or the frame ended inside a header/datagram),
//   ev_rx_err (the receiver flagged the frame).
module frame_parse (
    input             clk,
    input             rst,
    input             valid,
    input       [7:0] data,
    input             first,
    input             last,
    input             err,

    output reg        o_valid,
    output reg  [7:0] o_data,
    output reg        o_first,
    output reg        o_last,
    output reg        o_err,
    output reg  [1:0] o_kind,
    output reg [10:0] o_pos,

    output reg        dg_start,
    output reg        dg_len_done,
    output reg        dg_hdr_done,
    output reg  [7:0] dg_cmd,
    output reg  [7:0] dg_idx,
    output reg [31:0] dg_addr,
    output reg [10:0] dg_len,
    output reg        dg_more,
    output reg        dg_circ,

    output reg        ev_end,
    output reg        ev_runt,
    output reg        ev_oversize,
    output reg        ev_non_ethercat,
    output reg        ev_malformed,
    output reg        ev_rx_err
);
    localparam [2:0] S_ETH = 3'd0, S_ECH = 3'd1, S_DGH = 3'd2, S_DGD = 3'd3,
                     S_WKC = 3'd4, S_TAIL = 3'd5, S_SKIP = 3'd6;
    localparam [1:0] K_OTHER = 2'd0, K_HDR = 2'd1, K_DATA = 2'd2, K_WKC = 2'd3;
    localparam [10:0] MIN_FRAME = 11'd64, MAX_FRAME = 11'd1518;

    reg  [2:0]  state;
    reg  [10:0] pos;
    reg  [10:0] total;      // bytes of this frame so far (saturating)
    reg  [10:0] remain;     // EtherCAT area bytes not yet consumed
    reg         bad;        // lengths disagree
    reg         nonec;      // EtherType mismatch
    reg  [7:0]  eth_hi;     // EtherType high byte
    reg  [7:0]  len_lo;     // EtherCAT header length, low byte

    // The first byte of a frame always restarts the parse.
    wire [2:0]  cstate  = first ? S_ETH : state;
    wire [10:0] cpos    = first ? 11'd0 : pos;
    wire [10:0] ctotal  = first ? 11'd0 : total;
    wire [10:0] cremain = first ? 11'd0 : remain;
    wire        cbad    = first ? 1'b0 : bad;
    wire        cnonec  = first ? 1'b0 : nonec;

    wire [11:0] pos_next    = {1'b0, cpos} + 12'd1;
    wire [10:0] total_next  = (ctotal == 11'h7FF) ? ctotal : ctotal + 11'd1;
    wire [10:0] remain_next = (cremain == 11'd0) ? cremain : cremain - 11'd1;
    wire [11:0] left_after  = {1'b0, cremain} - 12'd1;          // area bytes left after this one
    wire [11:0] need        = {1'b0, dg_len} + 12'd2;           // data + working counter

    reg  [2:0]  n_state;
    reg  [10:0] n_pos;
    reg  [10:0] n_remain;
    reg         n_bad;
    reg         n_nonec;
    reg  [1:0]  kind;

    always @* begin
        n_state  = cstate;
        n_pos    = pos_next[10:0];
        n_remain = cremain;
        n_bad    = cbad;
        n_nonec  = cnonec;
        kind     = K_OTHER;
        case (cstate)
            S_ETH: begin
                if (cpos == 11'd13) begin
                    n_pos = 11'd0;
                    if ({eth_hi, data} == 16'h88A4) n_state = S_ECH;
                    else begin n_state = S_SKIP; n_nonec = 1'b1; end
                end
            end
            S_ECH: begin
                if (cpos == 11'd1) begin
                    n_pos    = 11'd0;
                    n_remain = {data[2:0], len_lo};
                    if (data[7:4] != 4'd1 || {data[2:0], len_lo} < 11'd12) begin
                        n_state = S_TAIL;
                        n_bad   = 1'b1;
                    end else begin
                        n_state = S_DGH;
                    end
                end
            end
            S_DGH: begin
                kind     = K_HDR;
                n_remain = remain_next;
                if (cpos == 11'd0 && cremain < 11'd12) begin
                    n_state = S_TAIL; n_bad = 1'b1; kind = K_OTHER;
                end else if (cpos == 11'd9) begin
                    n_pos = 11'd0;
                    if (left_after < need) begin
                        n_state = S_TAIL; n_bad = 1'b1;
                    end else begin
                        n_state = (dg_len == 11'd0) ? S_WKC : S_DGD;
                    end
                end
            end
            S_DGD: begin
                kind     = K_DATA;
                n_remain = remain_next;
                if (pos_next == {1'b0, dg_len}) begin
                    n_pos   = 11'd0;
                    n_state = S_WKC;
                end
            end
            S_WKC: begin
                kind     = K_WKC;
                n_remain = remain_next;
                if (cpos == 11'd1) begin
                    n_pos   = 11'd0;
                    n_state = (remain_next == 11'd0) ? S_TAIL : S_DGH;
                end
            end
            default: n_pos = 11'd0;     // S_TAIL, S_SKIP: pad and FCS
        endcase
    end

    wire ended_inside = (n_state == S_ECH) || (n_state == S_DGH) ||
                        (n_state == S_DGD) || (n_state == S_WKC);

    always @(posedge clk) begin
        o_valid         <= 1'b0;
        o_first         <= 1'b0;
        o_last          <= 1'b0;
        o_err           <= 1'b0;
        dg_start        <= 1'b0;
        dg_len_done     <= 1'b0;
        dg_hdr_done     <= 1'b0;
        ev_end          <= 1'b0;
        ev_runt         <= 1'b0;
        ev_oversize     <= 1'b0;
        ev_non_ethercat <= 1'b0;
        ev_malformed    <= 1'b0;
        ev_rx_err       <= 1'b0;
        if (rst) begin
            state   <= S_ETH;
            pos     <= 11'd0;
            total   <= 11'd0;
            remain  <= 11'd0;
            bad     <= 1'b0;
            nonec   <= 1'b0;
            eth_hi  <= 8'd0;
            len_lo  <= 8'd0;
            o_data  <= 8'd0;
            o_kind  <= K_OTHER;
            o_pos   <= 11'd0;
            dg_cmd  <= 8'd0;
            dg_idx  <= 8'd0;
            dg_addr <= 32'd0;
            dg_len  <= 11'd0;
            dg_more <= 1'b0;
            dg_circ <= 1'b0;
        end else if (valid) begin
            o_valid <= 1'b1;
            o_data  <= data;
            o_first <= first;
            o_last  <= last;
            o_err   <= err;
            o_kind  <= kind;
            o_pos   <= cpos;

            if (cstate == S_ETH && cpos == 11'd12) eth_hi <= data;
            if (cstate == S_ECH && cpos == 11'd0)  len_lo <= data;
            if (cstate == S_DGH && kind == K_HDR) begin
                case (cpos[3:0])
                    4'd0: dg_cmd <= data;
                    4'd1: dg_idx <= data;
                    4'd2: dg_addr[7:0]   <= data;
                    4'd3: dg_addr[15:8]  <= data;
                    4'd4: dg_addr[23:16] <= data;
                    4'd5: begin dg_addr[31:24] <= data; dg_start <= 1'b1; end
                    4'd6: dg_len[7:0] <= data;
                    4'd7: begin
                        dg_len[10:8] <= data[2:0];
                        dg_circ      <= data[6];
                        dg_more      <= data[7];
                        dg_len_done  <= 1'b1;
                    end
                    4'd9: dg_hdr_done <= (n_state != S_TAIL);
                    default: ;
                endcase
            end

            state  <= n_state;
            pos    <= n_pos;
            remain <= n_remain;
            bad    <= n_bad;
            nonec  <= n_nonec;
            total  <= total_next;

            if (last) begin
                state           <= S_ETH;
                pos             <= 11'd0;
                ev_end          <= 1'b1;
                ev_runt         <= (total_next < MIN_FRAME);
                ev_oversize     <= (total_next > MAX_FRAME);
                ev_non_ethercat <= n_nonec;
                ev_malformed    <= n_bad || ended_inside;
                ev_rx_err       <= err;
            end
        end
    end
endmodule
