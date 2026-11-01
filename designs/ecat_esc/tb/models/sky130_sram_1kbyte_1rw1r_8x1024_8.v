// Behavioural model of the PDK macro sky130_sram_1kbyte_1rw1r_8x1024_8 (OpenRAM, 1 KB,
// byte-wide, one read/write port and one read port), for simulation only. Same module
// name and ports as the PDK model, so the RTL instantiates the real name in both worlds.
// Port 0: inputs are latched on the rising edge of clk0; the write or the read happens on
// the falling edge, and dout0 holds the read data until the next read. The PDK model also
// drives dout0 unknown shortly after the next rising edge; a consumer that samples dout0
// on that rising edge (as the RTL does) sees the same data in both. tb/test_sram_model.py
// and tb/test_sram_pdk.py run the same traffic against this model and the PDK model.
module sky130_sram_1kbyte_1rw1r_8x1024_8 (
    input        clk0,
    input        csb0,
    input        web0,
    input        wmask0,
    input  [9:0] addr0,
    input  [7:0] din0,
    output reg [7:0] dout0,
    input        clk1,
    input        csb1,
    input  [9:0] addr1,
    output reg [7:0] dout1
);
    reg [7:0] mem [0:1023];
    reg       csb0_r, web0_r, wmask0_r;
    reg [9:0] addr0_r;
    reg [7:0] din0_r;
    reg       csb1_r;
    reg [9:0] addr1_r;

    always @(posedge clk0) begin
        csb0_r   <= csb0;
        web0_r   <= web0;
        wmask0_r <= wmask0;
        addr0_r  <= addr0;
        din0_r   <= din0;
    end

    always @(negedge clk0) begin
        if (!csb0_r && !web0_r && wmask0_r) mem[addr0_r] <= din0_r;
        if (!csb0_r && web0_r) dout0 <= mem[addr0_r];
    end

    always @(posedge clk1) begin
        csb1_r  <= csb1;
        addr1_r <= addr1;
    end

    always @(negedge clk1) begin
        if (!csb1_r) dout1 <= mem[addr1_r];
    end
endmodule
