// Declaration of Altera's altsource_probe (In-System Sources and Probes) for `make dlint`
// only, with the ports and parameters esc_board uses. Quartus supplies the real block.
/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off UNUSEDPARAM */
module altsource_probe #(
    parameter lpm_type                = "altsource_probe",
    parameter lpm_hint                = "UNUSED",
    parameter sld_auto_instance_index = "YES",
    parameter sld_instance_index      = 0,
    parameter instance_id             = "NONE",
    parameter probe_width             = 1,
    parameter source_width            = 1,
    parameter source_initial_value    = "0",
    parameter enable_metastability    = "NO"
) (
    input  [probe_width-1:0]  probe,
    output [source_width-1:0] source
);
    assign source = {source_width{1'b0}};
endmodule
/* verilator lint_on UNUSEDPARAM */
/* verilator lint_on UNUSEDSIGNAL */
