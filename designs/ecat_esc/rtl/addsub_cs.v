// Two-clock carry-select adder / subtractor: y = a + b or a - b (modulo 2^W) two clocks
// after the operands.
//   clock 1  every 8-bit chunk is added or subtracted twice, with and without the carry
//            (borrow) from the chunk below
//   clock 2  the carries ripple through one select per chunk, which picks each chunk's result
// An 8-bit adder is what the slow corner finishes in a clock at 100 MHz; a 16-bit or wider
// one is not. Used where the operands are known well before the result is needed.
module addsub_cs #(
    parameter W   = 32,
    parameter SUB = 0           // 1: a - b
) (
    input              clk,
    input  [W-1:0]     a,
    input  [W-1:0]     b,
    output reg [W-1:0] y
);
    localparam NC = (W + 7) / 8;
    localparam PW = 8 * NC;

    wire [PW-1:0] ap = {{(PW - W){1'b0}}, a};
    wire [PW-1:0] bp = {{(PW - W){1'b0}}, b};

    reg  [PW-1:0] s0, s1;       // chunk results without / with the incoming carry
    reg  [NC-1:0] c0, c1;       // carry (borrow) out of each

    genvar i;
    generate
        for (i = 0; i < NC; i = i + 1) begin : g_c
            wire [8:0] x0 = SUB ? ({1'b0, ap[8*i +: 8]} - {1'b0, bp[8*i +: 8]})
                                : ({1'b0, ap[8*i +: 8]} + {1'b0, bp[8*i +: 8]});
            wire [8:0] x1 = SUB ? ({1'b0, ap[8*i +: 8]} - {1'b0, bp[8*i +: 8]} - 9'd1)
                                : ({1'b0, ap[8*i +: 8]} + {1'b0, bp[8*i +: 8]} + 9'd1);
            always @(posedge clk) begin
                s0[8*i +: 8] <= x0[7:0];
                c0[i]        <= x0[8];
                s1[8*i +: 8] <= x1[7:0];
                c1[i]        <= x1[8];
            end
        end
    endgenerate

    wire [NC:0]   cin;
    wire [PW:0]   res;
    assign cin[0] = 1'b0;
    assign res[PW] = 1'b0;
    generate
        for (i = 0; i < NC; i = i + 1) begin : g_s
            assign cin[i+1]          = cin[i] ? c1[i] : c0[i];
            assign res[8*i +: 8]     = cin[i] ? s1[8*i +: 8] : s0[8*i +: 8];
        end
    endgenerate

    always @(posedge clk) y <= res[W-1:0];

    wire _unused_ok = &{1'b0, res[PW:W], cin[NC]};
endmodule
