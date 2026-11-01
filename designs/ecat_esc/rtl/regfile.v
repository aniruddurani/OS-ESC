// ESC register file, addresses 0x0000..0x0FFF, behind the byte-wide memory port.
//
//   mem_req/mem_we/mem_addr/mem_wdata   request, one byte per cycle
//   mem_rdata                           valid MEM_LAT = 3 clocks after the
//                                       request, flagged by mem_rvalid
// Implemented registers (everything else reads 0 and ignores writes):
//   0x0000 type, 0x0001 revision, 0x0004 FMMU count, 0x0005 SyncManager count,
//   0x0006 RAM size in KiB, 0x0007 port descriptor (port 0 = MII)   read only
//   0x0010..0x0011 configured station address                       read/write
//   0x0110..0x0111 DL status (the dl_status input)                  read only
//   0x0600.. FMMU configuration, 16 bytes each, bytes 0..12 writable
//   0x0800.. SyncManager configuration, 8 bytes each, status (+5) and PDI
//            activate (+7) read only
// Layout and access per the ESC register description (checked against TI's
// ESC register list; see the reference model).
module regfile #(
    parameter N_FMMU    = 3,        // 0..16
    parameter N_SM      = 4,        // 0..16
    parameter RAM_BYTES = 1024
) (
    input         clk,
    input         rst,

    input         mem_req,
    input         mem_we,
    input  [15:0] mem_addr,
    input  [7:0]  mem_wdata,
    output reg [7:0] mem_rdata,
    output reg       mem_rvalid,

    input  [15:0] dl_status,

    output [15:0] station_addr,
    // FMMU i occupies bits [128*i +: 128], SyncManager i bits [64*i +: 64];
    // one unused byte-block when the count is 0.
    output [8*16*((N_FMMU == 0) ? 1 : N_FMMU)-1:0] fmmu_cfg,
    output [8*8*((N_SM == 0) ? 1 : N_SM)-1:0]      sm_cfg
);
    localparam FW  = (N_FMMU == 0) ? 1 : N_FMMU;
    localparam SW  = (N_SM == 0) ? 1 : N_SM;
    localparam FAW = $clog2(16 * FW);
    localparam SAW = $clog2(8 * SW);
    localparam [15:0] FM_END = 16'h0600 + 16 * N_FMMU;
    localparam [15:0] SM_END = 16'h0800 + 8 * N_SM;
    localparam [31:0] RAM_KIB = RAM_BYTES / 1024;

    reg [15:0] sa;
    reg [7:0]  fm [0:16*FW-1];
    reg [7:0]  sm [0:8*SW-1];

    wire [FAW-1:0] fm_off = mem_addr[FAW-1:0];       // block bases are aligned
    wire [SAW-1:0] sm_off = mem_addr[SAW-1:0];
    wire        in_fm  = (N_FMMU != 0) && (mem_addr >= 16'h0600) && (mem_addr < FM_END);
    wire        in_sm  = (N_SM != 0)   && (mem_addr >= 16'h0800) && (mem_addr < SM_END);
    wire        fm_wr  = in_fm && (fm_off[3:0] <= 4'd12);
    wire        sm_wr  = in_sm && (sm_off[2:0] != 3'd5) && (sm_off[2:0] != 3'd7);

    assign station_addr = sa;

    genvar g;
    generate
        for (g = 0; g < 16 * FW; g = g + 1) begin : g_fm
            assign fmmu_cfg[8*g +: 8] = fm[g];
        end
        for (g = 0; g < 8 * SW; g = g + 1) begin : g_sm
            assign sm_cfg[8*g +: 8] = sm[g];
        end
    endgenerate

    reg [7:0] rd;
    always @* begin
        rd = 8'h00;
        case (mem_addr)
            16'h0000: rd = 8'h11;                       // ESC type (ET1100-class, see ecat.py)
            16'h0001: rd = 8'h02;                       // revision
            16'h0004: rd = N_FMMU[7:0];
            16'h0005: rd = N_SM[7:0];
            16'h0006: rd = RAM_KIB[7:0];
            16'h0007: rd = 8'h03;                       // port 0 = MII, others not implemented
            16'h0010: rd = sa[7:0];
            16'h0011: rd = sa[15:8];
            16'h0110: rd = dl_status[7:0];
            16'h0111: rd = dl_status[15:8];
            default: begin
                if (in_fm)      rd = fm[fm_off];
                else if (in_sm) rd = sm[sm_off];
            end
        endcase
    end

    reg       rv1, rv2;
    reg [7:0] rd1, rd2;
    integer   i;

    always @(posedge clk) begin
        if (rst) begin
            sa         <= 16'd0;
            rv1        <= 1'b0;
            rd1        <= 8'd0;
            rv2        <= 1'b0;
            rd2        <= 8'd0;
            mem_rvalid <= 1'b0;
            mem_rdata  <= 8'd0;
            for (i = 0; i < 16 * FW; i = i + 1) fm[i] <= 8'd0;
            for (i = 0; i < 8 * SW; i = i + 1)  sm[i] <= 8'd0;
        end else begin
            rv1        <= mem_req && !mem_we;
            rd1        <= rd;
            rv2        <= rv1;
            rd2        <= rd1;
            mem_rvalid <= rv2;
            mem_rdata  <= rd2;
            if (mem_req && mem_we) begin
                if (mem_addr == 16'h0010) sa[7:0]  <= mem_wdata;
                if (mem_addr == 16'h0011) sa[15:8] <= mem_wdata;
                if (fm_wr) fm[fm_off] <= mem_wdata;
                if (sm_wr) sm[sm_off] <= mem_wdata;
            end
        end
    end
endmodule
