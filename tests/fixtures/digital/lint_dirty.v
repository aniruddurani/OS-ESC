// Known-bad RTL for `make dselftest`: an unused signal and a silent truncation.
// dlint (Verilator, all warnings) must fail on it.
module lint_dirty (
    input  [7:0] a,
    output [3:0] y
);
    wire unused_signal = a[0];
    assign y = a;
endmodule
