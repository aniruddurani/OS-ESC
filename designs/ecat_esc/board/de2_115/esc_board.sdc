# Timing constraints for esc_board (the ESC on a DE2-115).
create_clock -name CLOCK_50     -period 20.000 [get_ports CLOCK_50]
create_clock -name ENET0_RX_CLK -period 40.000 [get_ports ENET0_RX_CLK]
create_clock -name ENET0_TX_CLK -period 40.000 [get_ports ENET0_TX_CLK]
derive_pll_clocks
derive_clock_uncertainty

# JTAG (the In-System Sources and Probes hub)
if {[get_collection_size [get_ports -nowarn altera_reserved_tck]] > 0} {
    create_clock -name altera_reserved_tck -period 100.000 [get_ports altera_reserved_tck]
    set_input_delay  -clock altera_reserved_tck 0 [get_ports {altera_reserved_tdi altera_reserved_tms}]
    set_output_delay -clock altera_reserved_tck 0 [get_ports altera_reserved_tdo]
    set_false_path -from [get_ports {altera_reserved_tdi altera_reserved_tms}]
    set_false_path -to   [get_ports altera_reserved_tdo]
}

# The RTL crosses between these domains only through its asynchronous FIFOs and reset
# synchronisers, and board_flags/phy_init cross into CLOCK_50 through two-flop
# synchronisers.
set_clock_groups -asynchronous \
    -group [get_clocks CLOCK_50] \
    -group [get_clocks {*u_pll*}] \
    -group [get_clocks ENET0_RX_CLK] \
    -group [get_clocks ENET0_TX_CLK] \
    -group [get_clocks -nowarn altera_reserved_tck]

# MII, IEEE 802.3 clause 22: RXD/RX_DV/RX_ER valid from 10 ns before to 10 ns after the
# rising RX_CLK; TXD/TX_EN change 0 to 25 ns after the rising TX_CLK.
set mii_rx [get_ports {ENET0_RX_DV ENET0_RX_ER ENET0_RX_DATA[*]}]
set_input_delay -clock ENET0_RX_CLK -max 30.000 $mii_rx
set_input_delay -clock ENET0_RX_CLK -min 10.000 $mii_rx
set mii_tx [get_ports {ENET0_TX_EN ENET0_TX_ER ENET0_TX_DATA[*]}]
set_output_delay -clock ENET0_TX_CLK -max 15.000 $mii_tx
set_output_delay -clock ENET0_TX_CLK -min 0.000 $mii_tx

# Slow board signals: the button, LEDs, PHY reset and MDIO (MDC is 2.5 MHz, generated
# and sampled by phy_init on CLOCK_50 with a whole MDC half-period of margin).
set slow_in  [get_ports {KEY0 ENET0_MDIO}]
set slow_out [get_ports {LEDR[*] ENET0_RST_N ENET0_MDC ENET0_MDIO}]
set_input_delay  -clock CLOCK_50 0 $slow_in
set_output_delay -clock CLOCK_50 0 $slow_out
set_false_path -from $slow_in
set_false_path -to   $slow_out
