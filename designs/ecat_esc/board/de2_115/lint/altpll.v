// Declaration of Altera's altpll for `make dlint` only (the ports and parameters esc_board
// uses). Quartus supplies the real block; this is never synthesised or simulated.
/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off UNUSEDPARAM */
module altpll #(
    parameter intended_device_family = "Cyclone IV E",
    parameter lpm_type               = "altpll",
    parameter operation_mode         = "NORMAL",
    parameter inclk0_input_frequency = 20000,
    parameter clk0_multiply_by       = 1,
    parameter clk0_divide_by         = 1,
    parameter clk0_duty_cycle        = 50,
    parameter clk0_phase_shift       = "0",
    parameter port_clk0              = "PORT_USED",
    parameter port_locked            = "PORT_USED",
    parameter port_inclk0            = "PORT_USED"
) (
    input  [1:0] inclk,
    output [4:0] clk,
    output       locked
);
    assign clk    = 5'd0;
    assign locked = 1'b0;
endmodule
/* verilator lint_on UNUSEDPARAM */
/* verilator lint_on UNUSEDSIGNAL */
