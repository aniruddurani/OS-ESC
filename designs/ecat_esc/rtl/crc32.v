// Ethernet CRC-32, one byte per clock, reflected form (polynomial 0xEDB88320).
//   init     load the start value 0xFFFFFFFF (also done by rst)
//   valid    consume `data` on this clock
//   crc      the finished CRC of the bytes so far (what zlib.crc32 returns);
//            transmit it least-significant byte first as the FCS
//   residue_ok  1 after a frame has been fed together with its own FCS
//            (register residue 0xDEBB20E3, the reflected form of 0xC704DD7B)
module crc32 (
    input         clk,
    input         rst,
    input         init,
    input         valid,
    input  [7:0]  data,
    output [31:0] crc,
    output        residue_ok
);
    reg [31:0] r;

    function [31:0] step(input [31:0] c, input [7:0] d);
        integer i;
        reg [31:0] x;
        begin
            x = c ^ {24'b0, d};
            for (i = 0; i < 8; i = i + 1)
                x = x[0] ? ((x >> 1) ^ 32'hEDB88320) : (x >> 1);
            step = x;
        end
    endfunction

    always @(posedge clk) begin
        if (rst || init)
            r <= 32'hFFFFFFFF;
        else if (valid)
            r <= step(r, data);
    end

    assign crc        = ~r;
    assign residue_ok = (r == 32'hDEBB20E3);
endmodule
