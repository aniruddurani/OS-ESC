// MII transmitter: byte stream to 4-bit nibbles (low nibble first).
//
// A frame starts when `valid` and `first` are seen while idle. The block sends
// 7 x 0x55 + SFD 0xD5, then the bytes, then the CRC-32 (low byte first), then a
// 12-byte inter-frame gap. `err` raised with a frame's last byte inverts its FCS. `ready` is high in the cycle a byte may be taken
// (valid && ready); the sender must hold `valid` until then. If a byte is not
// there when needed the frame is finished with an inverted FCS and `underrun`
// stays high until the next frame starts.
module mii_tx (
    input            tx_clk,
    input            rst,
    input            valid,
    input      [7:0] data,
    input            first,
    input            last,
    input            err,       // with `last`: send this frame with a wrong FCS
    output           ready,
    output           tx_en,
    output     [3:0] txd,
    output reg       underrun
);
    localparam [2:0] IDLE = 3'd0, PRE = 3'd1, DATA = 3'd2, FCS = 3'd3, IFG = 3'd4;

    reg [2:0]  state;
    reg [4:0]  cnt;
    reg        phase;       // 0: low nibble of cur, 1: high nibble
    reg [7:0]  cur;
    reg        last_flag;
    reg        bad;
    reg [31:0] fcs;

    wire [31:0] crc;
    wire        crc_residue;
    wire        crc_residue_next;
    wire        pre_last = (state == PRE) && (cnt == 5'd15);
    // a frame starts from idle, or straight from the end of the gap if one is waiting
    wire        start    = valid && first && ((state == IDLE) || ((state == IFG) && (cnt == 5'd23)));

    assign ready = pre_last || ((state == DATA) && phase && !last_flag);

    crc32 u_crc (
        .clk(tx_clk), .rst(rst),
        .init(start),
        .valid(ready && valid),
        .data(data),
        .crc(crc),
        .residue_ok(crc_residue),
        .residue_next(crc_residue_next)
    );
    wire _unused_ok = &{1'b0, crc_residue, crc_residue_next};

    wire [31:0] fcs_out = bad ? ~fcs : fcs;
    wire [3:0]  fcs_nib = fcs_out[{cnt[2:0], 2'b00} +: 4];

    assign tx_en = (state == PRE) || (state == DATA) || (state == FCS);
    assign txd   = (state == PRE)  ? (cnt == 5'd15 ? 4'hD : 4'h5) :
                   (state == DATA) ? (phase ? cur[7:4] : cur[3:0]) :
                   (state == FCS)  ? fcs_nib : 4'h0;

    always @(posedge tx_clk) begin
        if (rst) begin
            state     <= IDLE;
            cnt       <= 5'd0;
            phase     <= 1'b0;
            cur       <= 8'd0;
            last_flag <= 1'b0;
            bad       <= 1'b0;
            fcs       <= 32'd0;
            underrun  <= 1'b0;
        end else begin
            case (state)
                IDLE: if (start) begin
                    state    <= PRE;
                    cnt      <= 5'd0;
                    bad      <= 1'b0;
                    underrun <= 1'b0;
                end
                PRE: begin
                    if (cnt == 5'd15) begin
                        if (valid) begin
                            cur       <= data;
                            last_flag <= last;
                            if (last && err) bad <= 1'b1;
                            phase     <= 1'b0;
                            state     <= DATA;
                        end else begin
                            underrun <= 1'b1;
                            bad      <= 1'b1;
                            fcs      <= crc;
                            cnt      <= 5'd0;
                            state    <= FCS;
                        end
                    end else begin
                        cnt <= cnt + 5'd1;
                    end
                end
                DATA: begin
                    if (!phase) begin
                        phase <= 1'b1;
                    end else begin
                        phase <= 1'b0;
                        if (last_flag) begin
                            fcs   <= crc;
                            cnt   <= 5'd0;
                            state <= FCS;
                        end else if (valid) begin
                            cur       <= data;
                            last_flag <= last;
                            if (last && err) bad <= 1'b1;
                        end else begin
                            underrun <= 1'b1;
                            bad      <= 1'b1;
                            fcs      <= crc;
                            cnt      <= 5'd0;
                            state    <= FCS;
                        end
                    end
                end
                FCS: begin
                    if (cnt == 5'd7) begin
                        cnt   <= 5'd0;
                        state <= IFG;
                    end else begin
                        cnt <= cnt + 5'd1;
                    end
                end
                default: begin   // IFG: 24 nibble times with tx_en low
                    if (start) begin
                        state    <= PRE;
                        cnt      <= 5'd0;
                        bad      <= 1'b0;
                        underrun <= 1'b0;
                    end else if (cnt == 5'd23) begin
                        state <= IDLE;
                    end else begin
                        cnt <= cnt + 5'd1;
                    end
                end
            endcase
        end
    end
endmodule
