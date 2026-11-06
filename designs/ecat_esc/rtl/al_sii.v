// AL registers and the SII interface, behind the byte-wide memory port with the usual
// MEM_LAT = 3 read latency. The ESC has no local CPU, so it uses device emulation (0x0141
// bit 0): AL status follows the state written to AL control, with no transition checks.
//
//   0x0120..0x0121 AL control               read/write
//   0x0130..0x0131 AL status                read only; a write of AL control whose low four
//                                           bits are a defined state sets bits 3:0, reset INIT
//   0x0134..0x0135 AL status code           reads 0
//   0x0141         ESC configuration        reads 0x01, device emulation
//   0x0500         SII access               bit 0 reads back; the ROM has one reader
//   0x0502..0x0503 SII control and status   bit 13 NACK; BUSY and R64 read 0
//   0x0504..0x0507 SII word address         read/write
//   0x0508..0x050B SII data                 read only, the two words from the address
//
// A master writes command and address in one datagram with the command first, so the data
// register does not wait for a command: it follows the address, over at most five clocks, and
// the next datagram reads it. The read command therefore does nothing. The store is read only:
// any other command but NOP sets NACK, and a NOP clears it. A word past the table reads 0xFFFF,
// as a blank EEPROM does. Everything else in this block's range reads 0 and ignores writes.
module al_sii (
    input            clk,
    input            rst,

    input            mem_req,
    input            mem_we,
    input     [15:0] mem_addr,
    input     [7:0]  mem_wdata,
    output reg [7:0] mem_rdata,
    output reg       mem_rvalid
);
    localparam [6:0] LAST_WORD = 7'd127;

    // Request register, decode, output register, as in regfile.
    reg [15:0] a1;
    reg        rq1, wq1, rv2;
    reg [7:0]  wd1;

    reg [15:0] al_ctl;
    reg [3:0]  al_state;
    reg        owner, nack;
    reg [31:0] sii_addr, sii_data;

    // The data register follows the address, restarted by reset and by an address write.
    localparam [2:0] IDLE = 3'd0, LOW_ADDR = 3'd1, LOW_DATA = 3'd2, HIGH_ADDR = 3'd3, HIGH_DATA = 3'd4;
    reg  [2:0]  step;
    reg  [6:0]  rom_a;
    wire [15:0] rom_q;
    sii_rom u_rom (.addr(rom_a), .data(rom_q));
    wire past0 = |sii_addr[31:7];
    wire past1 = past0 || (sii_addr[6:0] == LAST_WORD);

    wire al_defined = (wd1[3:0] == 4'd1) || (wd1[3:0] == 4'd2) || (wd1[3:0] == 4'd3) ||
                      (wd1[3:0] == 4'd4) || (wd1[3:0] == 4'd8);

    reg [7:0] fix;
    always @* begin
        fix = 8'h00;
        case (a1)
            16'h0120: fix = al_ctl[7:0];
            16'h0121: fix = al_ctl[15:8];
            16'h0130: fix = {4'd0, al_state};
            16'h0141: fix = 8'h01;
            16'h0500: fix = {7'd0, owner};
            16'h0503: fix = {2'd0, nack, 5'd0};
            16'h0504: fix = sii_addr[7:0];
            16'h0505: fix = sii_addr[15:8];
            16'h0506: fix = sii_addr[23:16];
            16'h0507: fix = sii_addr[31:24];
            16'h0508: fix = sii_data[7:0];
            16'h0509: fix = sii_data[15:8];
            16'h050A: fix = sii_data[23:16];
            16'h050B: fix = sii_data[31:24];
            default: ;
        endcase
    end

    reg [7:0] c_fix;

    always @(posedge clk) begin
        a1  <= mem_addr;
        wd1 <= mem_wdata;
        if (rst) begin
            rq1        <= 1'b0;
            wq1        <= 1'b0;
            rv2        <= 1'b0;
            c_fix      <= 8'd0;
            mem_rvalid <= 1'b0;
            mem_rdata  <= 8'd0;
            al_ctl     <= 16'd0;
            al_state   <= 4'd1;
            owner      <= 1'b0;
            nack       <= 1'b0;
            sii_addr   <= 32'd0;
            sii_data   <= 32'd0;
            rom_a      <= 7'd0;
            step       <= LOW_ADDR;
        end else begin
            rq1        <= mem_req && !mem_we;
            wq1        <= mem_req && mem_we;
            rv2        <= rq1;
            c_fix      <= fix;
            mem_rvalid <= rv2;
            mem_rdata  <= c_fix;

            case (step)
                LOW_ADDR: begin
                    rom_a <= sii_addr[6:0];
                    step  <= LOW_DATA;
                end
                LOW_DATA: begin
                    sii_data[15:0] <= past0 ? 16'hFFFF : rom_q;
                    step           <= HIGH_ADDR;
                end
                HIGH_ADDR: begin
                    rom_a <= rom_a + 7'd1;
                    step  <= HIGH_DATA;
                end
                HIGH_DATA: begin
                    sii_data[31:16] <= past1 ? 16'hFFFF : rom_q;
                    step            <= IDLE;
                end
                default: ;
            endcase

            if (wq1) begin
                case (a1)
                    16'h0120: begin
                        al_ctl[7:0] <= wd1;
                        if (al_defined) al_state <= wd1[3:0];
                    end
                    16'h0121: al_ctl[15:8] <= wd1;
                    16'h0500: owner <= wd1[0];
                    16'h0503: begin
                        if (wd1[2:0] == 3'd0) nack <= 1'b0;
                        else if (wd1[2:0] != 3'd1) nack <= 1'b1;
                    end
                    16'h0504: begin sii_addr[7:0]   <= wd1; step <= LOW_ADDR; end
                    16'h0505: begin sii_addr[15:8]  <= wd1; step <= LOW_ADDR; end
                    16'h0506: begin sii_addr[23:16] <= wd1; step <= LOW_ADDR; end
                    16'h0507: begin sii_addr[31:24] <= wd1; step <= LOW_ADDR; end
                    default: ;
                endcase
            end
        end
    end
endmodule
