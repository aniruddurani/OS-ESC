// MII receiver: 4-bit nibbles (low nibble first) to a byte stream.
//
// Waits for at least two preamble nibbles (0x5) followed by the SFD nibble
// (0xD); a frame without that, or with any other nibble in the preamble, is
// skipped until rx_dv falls. Output is registered and delayed by one byte so
// `last` can be raised when rx_dv falls:
//   valid/data  one byte per pulse; first on the first byte, last on the final one
//   err         with `last`: RX_ER was seen during the frame, or it ended on an
//               odd nibble
module mii_rx (
    input            rx_clk,
    input            rst,
    input            rx_dv,
    input            rx_er,
    input      [3:0] rxd,
    output reg       valid,
    output reg [7:0] data,
    output reg       first,
    output reg       last,
    output reg       err
);
    localparam [1:0] IDLE = 2'd0, PRE = 2'd1, DATA = 2'd2, IGNORE = 2'd3;

    reg [1:0] state;
    reg [3:0] fives;        // consecutive preamble nibbles seen (saturating)
    reg [3:0] lo;           // low nibble waiting for its high nibble
    reg       have_lo;
    reg [7:0] pend;         // byte held back until the next one (or the end) arrives
    reg       pend_valid;
    reg       pend_first;
    reg       bad;          // RX_ER seen in this frame
    reg       tail;         // the frame ended: the last byte leaves on the next clock
    reg       tail_err;

    always @(posedge rx_clk) begin
        valid <= 1'b0;
        first <= 1'b0;
        last  <= 1'b0;
        err   <= 1'b0;
        if (rst) begin
            state      <= IDLE;
            fives      <= 4'd0;
            lo         <= 4'd0;
            have_lo    <= 1'b0;
            pend       <= 8'd0;
            pend_valid <= 1'b0;
            pend_first <= 1'b0;
            bad        <= 1'b0;
            tail       <= 1'b0;
            tail_err   <= 1'b0;
            data       <= 8'd0;
        end else begin
            // One clock after rx_dv falls, so the last byte keeps the two-clock spacing of the
            // others: the core relies on it (a frame cut inside a datagram ends on a data byte).
            tail <= 1'b0;
            if (tail) begin
                valid <= 1'b1;
                data  <= pend;
                first <= pend_first;
                last  <= 1'b1;
                err   <= tail_err;
            end
            case (state)
                IDLE: if (rx_dv) begin
                    bad <= rx_er;
                    if (rxd == 4'h5) begin
                        fives <= 4'd1;
                        state <= PRE;
                    end else begin
                        state <= IGNORE;
                    end
                end
                PRE: begin
                    if (!rx_dv) begin
                        state <= IDLE;
                    end else begin
                        if (rx_er) bad <= 1'b1;
                        if (rxd == 4'h5) begin
                            if (fives != 4'd15) fives <= fives + 4'd1;
                        end else if (rxd == 4'hD && fives >= 4'd2) begin
                            state      <= DATA;
                            have_lo    <= 1'b0;
                            pend_valid <= 1'b0;
                            pend_first <= 1'b1;
                        end else begin
                            state <= IGNORE;
                        end
                    end
                end
                DATA: begin
                    if (rx_dv) begin
                        if (rx_er) bad <= 1'b1;
                        if (!have_lo) begin
                            lo      <= rxd;
                            have_lo <= 1'b1;
                        end else begin
                            have_lo <= 1'b0;
                            if (pend_valid) begin
                                valid      <= 1'b1;
                                data       <= pend;
                                first      <= pend_first;
                                pend_first <= 1'b0;
                            end
                            pend       <= {rxd, lo};
                            pend_valid <= 1'b1;
                        end
                    end else begin
                        tail     <= pend_valid;
                        tail_err <= bad | have_lo;
                        state    <= IDLE;
                    end
                end
                default: if (!rx_dv) state <= IDLE;   // IGNORE: wait for the frame to end
            endcase
        end
    end
endmodule
