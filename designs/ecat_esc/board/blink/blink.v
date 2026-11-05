// DE2-115 smoke test: LEDR[0] blinks at about 1.5 Hz from the 50 MHz oscillator.
// Proves the Quartus compile and JTAG load path before the ESC goes on the board.
module blink (
    input  wire CLOCK_50,
    output wire LEDR0
);
    reg [24:0] count = 25'd0;

    always @(posedge CLOCK_50)
        count <= count + 25'd1;

    assign LEDR0 = count[24];
endmodule
