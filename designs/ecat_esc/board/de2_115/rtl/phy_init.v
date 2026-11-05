// Brings the DE2-115's Marvell 88E1111 up at 100 Mbit full duplex over MDIO (clause 22),
// so its MII clocks can drive esc_top directly. After reset:
//   1. hold phy_rst_n low for RST_CYCLES, then wait WAIT_CYCLES
//   2. read reg 2 (PHY ID 1); anything but 0x0141 (Marvell), or a PHY that does not
//      drive the turnaround bit low, is mdio_error, which holds until reset
//   3. write reg 9 = 0x0000 (no 1000BASE-T), reg 4 = 0x0101 (100BASE-TX full duplex,
//      selector 802.3), reg 0 = 0x9200 (software reset, auto-negotiation on, restart)
//   4. read reg 17 (PHY-specific status) every POLL_CYCLES: resolved (bit 11) and link
//      (bit 10) at 100 Mbit (bits 15:14 = 01) full duplex (bit 13) is link_100fd,
//      resolved and up otherwise is wrong_link, anything else is configuring
//
// clk is 50 MHz; MDC is clk / (2 * CLK_DIV). The controller changes MDIO after the
// falling MDC edge and samples it on the clock edge that raises MDC. The pad is outside:
// mdio_o/mdio_oe drive it, mdio_i is the line.
module phy_init #(
    parameter [4:0] PHY_ADDR = 5'd16,
    parameter CLK_DIV     = 10,         // 2.5 MHz MDC
    parameter RST_CYCLES  = 600000,     // 12 ms
    parameter WAIT_CYCLES = 250000,     // 5 ms
    parameter POLL_CYCLES = 50000       // 1 ms
) (
    input            clk,
    input            rst,               // asynchronous, active high
    output reg       phy_rst_n,
    output reg       mdc,
    input            mdio_i,
    output reg       mdio_o,
    output reg       mdio_oe,
    output reg [2:0] state,             // 0 idle, 1 configuring, 2 link_100fd, 3 wrong_link, 4 mdio_error
    output           link_ok
);
    localparam [2:0] IDLE = 3'd0, CONFIGURING = 3'd1, LINK_100FD = 3'd2, WRONG_LINK = 3'd3,
                     MDIO_ERROR = 3'd4;
    assign link_ok = state == LINK_100FD;

    // --- MDC and one MDIO frame: 32 preamble bits, then ST OP PHYAD REGAD TA DATA -------
    localparam DIV_W = $clog2(CLK_DIV);
    reg [DIV_W-1:0] div;
    wire            tick = div == CLK_DIV[DIV_W-1:0] - 1'b1;
    wire            fall = tick && mdc;    // MDC goes low on this edge
    wire            rise = tick && !mdc;   // MDC goes high on this edge

    reg        go, busy, rd;
    reg  [4:0] reg_a;
    reg [15:0] wdata, rdata;
    reg        ta_bad;
    reg  [6:0] bitn;                       // bit being driven, 0..63; 64 = frame done
    reg [31:0] frame;                      // ST OP PHYAD REGAD TA DATA, MSB first

    always @(posedge clk or posedge rst)
        if (rst) begin
            div <= 0; mdc <= 1'b0; busy <= 1'b0; bitn <= 7'd0;
            mdio_o <= 1'b1; mdio_oe <= 1'b0; frame <= 32'd0; rdata <= 16'd0; ta_bad <= 1'b0;
        end else begin
            div <= tick ? {DIV_W{1'b0}} : div + 1'b1;
            if (tick) mdc <= !mdc;
            if (go && !busy) begin
                busy   <= 1'b1;
                bitn   <= 7'd0;
                ta_bad <= 1'b0;
                frame  <= {2'b01, rd ? 2'b10 : 2'b01, PHY_ADDR, reg_a, 2'b10, rd ? 16'd0 : wdata};
            end else if (busy && fall) begin
                if (bitn == 7'd64) begin
                    busy    <= 1'b0;
                    mdio_oe <= 1'b0;
                end else begin
                    // a read hands the line to the PHY from the turnaround on
                    mdio_oe <= !(rd && bitn >= 7'd46);
                    mdio_o  <= bitn < 7'd32 ? 1'b1 : frame[31];
                    if (bitn >= 7'd32) frame <= {frame[30:0], 1'b0};
                    bitn    <= bitn + 7'd1;
                end
            end else if (busy && rise && rd && bitn != 7'd0) begin
                // bitn - 1 is the bit on the line now
                if (bitn == 7'd48 && mdio_i) ta_bad <= 1'b1;
                if (bitn > 7'd48) rdata <= {rdata[14:0], mdio_i};
            end
        end

    // --- Sequence ----------------------------------------------------------------------
    localparam [3:0] S_RST = 4'd0, S_WAIT = 4'd1, S_ID = 4'd2, S_W9 = 4'd3, S_W4 = 4'd4,
                     S_W0 = 4'd5, S_PAUSE = 4'd6, S_POLL = 4'd7, S_ERR = 4'd8;
    reg  [3:0] seq;
    reg [31:0] count;
    reg        issued;                     // the current step's frame has started

    // One step: start the frame once, then wait for it to finish.
    wire frame_done = issued && !busy && !go;

    always @(posedge clk or posedge rst)
        if (rst) begin
            seq <= S_RST; count <= 32'd0; phy_rst_n <= 1'b0; state <= IDLE;
            go <= 1'b0; rd <= 1'b0; reg_a <= 5'd0; wdata <= 16'd0; issued <= 1'b0;
        end else begin
            go <= 1'b0;
            if (go) issued <= 1'b1;
            case (seq)
                S_RST:
                    if (count == RST_CYCLES - 1) begin
                        count <= 32'd0; phy_rst_n <= 1'b1; seq <= S_WAIT;
                    end else count <= count + 32'd1;
                S_WAIT:
                    if (count == WAIT_CYCLES - 1) begin
                        count <= 32'd0; state <= CONFIGURING; seq <= S_ID;
                    end else count <= count + 32'd1;
                S_ID, S_W9, S_W4, S_W0, S_POLL:
                    if (!issued && !go && !busy) begin
                        go    <= 1'b1;
                        rd    <= seq == S_ID || seq == S_POLL;
                        reg_a <= seq == S_ID ? 5'd2 : seq == S_W9 ? 5'd9 : seq == S_W4 ? 5'd4 :
                                 seq == S_W0 ? 5'd0 : 5'd17;
                        wdata <= seq == S_W4 ? 16'h0101 : seq == S_W0 ? 16'h9200 : 16'h0000;
                    end else if (frame_done) begin
                        issued <= 1'b0;
                        if (rd && ta_bad) begin
                            state <= MDIO_ERROR; seq <= S_ERR;
                        end else case (seq)
                            S_ID:    if (rdata == 16'h0141) seq <= S_W9;
                                     else begin state <= MDIO_ERROR; seq <= S_ERR; end
                            S_W9:    seq <= S_W4;
                            S_W4:    seq <= S_W0;
                            S_W0:    seq <= S_PAUSE;
                            default: begin             // S_POLL
                                seq <= S_PAUSE;
                                if (rdata[11] && rdata[10])
                                    state <= rdata[15:14] == 2'b01 && rdata[13] ? LINK_100FD : WRONG_LINK;
                                else
                                    state <= CONFIGURING;
                            end
                        endcase
                    end
                S_PAUSE:
                    if (count == POLL_CYCLES - 1) begin
                        count <= 32'd0; seq <= S_POLL;
                    end else count <= count + 32'd1;
                default: ;                             // S_ERR holds until reset
            endcase
        end
endmodule
