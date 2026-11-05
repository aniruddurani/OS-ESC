# Board status over JTAG, through esc_board's In-System Sources and Probes instance "ESC".
#   quartus_stp -t status.tcl read    prints "ISSP <probe value in hex>"
#   quartus_stp -t status.tcl reset   pulses the soft reset (source 0) for 10 ms, then reads
# board_status.py decodes the value: {BUILD_ID[7:0], 2'b0, PHY state[2:0], sticky[2:0]}.
set action [lindex $quartus(args) 0]
set hw ""
foreach h [get_hardware_names] { if {[string match "USB-Blaster*" $h]} { set hw $h; break } }
if {$hw eq ""} { error "no USB-Blaster found" }
set dev ""
# Quartus names the device by the first family sharing its JTAG ID, e.g.
# "@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)"; the ID is what identifies the EP4CE115.
foreach d [get_device_names -hardware_name $hw] { if {[string match "*(0x020F70DD)*" $d]} { set dev $d; break } }
if {$dev eq ""} { error "no device with JTAG ID 0x020F70DD (EP4CE115) on $hw" }

# The instance query opens its own session, so it runs before ours starts.
set index -1
foreach inst [get_insystem_source_probe_instance_info -hardware_name $hw -device_name $dev] {
    if {[lindex $inst 3] eq "ESC"} { set index [lindex $inst 0] }
}
if {$index < 0} { error "no ISSP instance ESC on $dev (is esc_board.sof loaded?)" }
start_insystem_source_probe -hardware_name $hw -device_name $dev
if {$action eq "reset"} {
    write_source_data -instance_index $index -value 1 -value_in_hex
    after 10
    write_source_data -instance_index $index -value 0 -value_in_hex
} elseif {$action ne "read"} {
    end_insystem_source_probe
    error "usage: status.tcl read|reset"
}
puts "ISSP [read_probe_data -instance_index $index -value_in_hex]"
end_insystem_source_probe
