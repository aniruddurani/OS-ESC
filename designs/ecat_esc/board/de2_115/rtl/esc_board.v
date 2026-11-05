// The ESC on a Terasic DE2-115: the unchanged esc_top (tapeout default configuration) on
// ENET0's Marvell 88E1111 in MII mode.
//   CLOCK_50 -> PLL (x PLL_MUL / PLL_DIV) -> core clock, 80 MHz by default
//   ENET0 RX_CLK / TX_CLK (25 MHz from the PHY) -> esc_top rx_clk / tx_clk
//   phy_init brings the PHY up at 100 Mbit full duplex over MDIO
//   esc_top is held in reset at power-on, while KEY0 is pressed, while the PLL is
//   unlocked, until the link is up at 100 Mbit full duplex, and while the JTAG soft reset
//   (ISSP source 0) is set
//   board_flags keeps the FIFO flags sticky; ISSP probes = {BUILD_ID, 2'b0, PHY state,
//   sticky flags}
// LEDR[2:0] sticky flags, LEDR[5:3] PHY state, LEDR6 link, LEDR7 PLL locked, LEDR17
// heartbeat.
module esc_board #(
    parameter PLL_MUL        = 8,
    parameter PLL_DIV        = 5,
    parameter [7:0] BUILD_ID = 8'h01
) (
    input         CLOCK_50,
    input         KEY0,              // active low
    output [17:0] LEDR,

    input         ENET0_RX_CLK,
    input         ENET0_RX_DV,
    input         ENET0_RX_ER,
    input  [3:0]  ENET0_RX_DATA,
    input         ENET0_TX_CLK,
    output        ENET0_TX_EN,
    output        ENET0_TX_ER,
    output [3:0]  ENET0_TX_DATA,
    output        ENET0_RST_N,
    output        ENET0_MDC,
    inout         ENET0_MDIO
);
    // --- Core clock --------------------------------------------------------------------
    /* verilator lint_off UNUSEDSIGNAL */
    wire [4:0] pll_clk;                           // the PLL has five outputs; c0 is used
    /* verilator lint_on UNUSEDSIGNAL */
    wire       locked;
    altpll #(
        .intended_device_family("Cyclone IV E"),
        .lpm_type("altpll"),
        .operation_mode("NORMAL"),
        .inclk0_input_frequency(20000),
        .clk0_multiply_by(PLL_MUL),
        .clk0_divide_by(PLL_DIV),
        .clk0_duty_cycle(50),
        .clk0_phase_shift("0"),
        .port_clk0("PORT_USED"),
        .port_locked("PORT_USED"),
        .port_inclk0("PORT_USED")
    ) u_pll (
        .inclk({1'b0, CLOCK_50}),
        .clk(pll_clk),
        .locked(locked)
    );
    wire clk_core = pll_clk[0];

    // --- Resets ------------------------------------------------------------------------
    // Power-up values come from the FPGA configuration (initial blocks).
    reg [20:0] por;                               // 2^20 cycles of CLOCK_50, about 21 ms
    reg  [1:0] key_s;                             // KEY0 synchronised (1 = released)
    initial begin
        por   = 21'd0;
        key_s = 2'b11;
    end
    always @(posedge CLOCK_50) begin
        if (!por[20]) por <= por + 21'd1;
        key_s <= {key_s[0], KEY0};
    end
    wire board_rst = !por[20] || !key_s[1];

    wire [2:0] phy_state;
    wire       link_ok, mdio_o, mdio_oe;
    phy_init u_phy (
        .clk(CLOCK_50), .rst(board_rst),
        .phy_rst_n(ENET0_RST_N), .mdc(ENET0_MDC),
        .mdio_i(ENET0_MDIO), .mdio_o(mdio_o), .mdio_oe(mdio_oe),
        .state(phy_state), .link_ok(link_ok)
    );
    assign ENET0_MDIO = mdio_oe ? mdio_o : 1'bz;

    wire [0:0] soft_rst;
    wire       esc_rst = board_rst || !locked || !link_ok || soft_rst[0];

    // --- The ESC -----------------------------------------------------------------------
    wire rx_overflow, tx_overflow, tx_underrun;
    esc_top #(
        .N_FMMU(3), .N_SM(4), .RAM_BYTES(256), .DIAG_EN(1), .FIFO_DEPTH(16)
    ) u_esc (
        .rst(esc_rst),
        .rx_clk(ENET0_RX_CLK), .rx_dv(ENET0_RX_DV), .rx_er(ENET0_RX_ER), .rxd(ENET0_RX_DATA),
        .tx_clk(ENET0_TX_CLK), .tx_en(ENET0_TX_EN), .txd(ENET0_TX_DATA),
        .clk(clk_core),
        .rx_overflow(rx_overflow), .tx_overflow(tx_overflow), .tx_underrun(tx_underrun)
    );
    assign ENET0_TX_ER = 1'b0;

    // --- Status ------------------------------------------------------------------------
    wire [2:0] sticky;
    board_flags u_flags (
        .clk50(CLOCK_50), .tx_clk(ENET0_TX_CLK), .rst(esc_rst),
        .rx_overflow(rx_overflow), .tx_overflow(tx_overflow), .tx_underrun(tx_underrun),
        .sticky(sticky)
    );

    altsource_probe #(
        .lpm_type("altsource_probe"),
        .lpm_hint("UNUSED"),
        .sld_auto_instance_index("YES"),
        .sld_instance_index(0),
        .instance_id("ESC"),
        .probe_width(16),
        .source_width(1),
        .source_initial_value("0"),
        .enable_metastability("NO")
    ) u_issp (
        .probe({BUILD_ID, 2'b00, phy_state, sticky}),
        .source(soft_rst)
    );

    reg [26:0] beat;
    initial beat = 27'd0;
    always @(posedge clk_core) beat <= beat + 27'd1;

    assign LEDR = {beat[26], 9'd0, locked, link_ok, phy_state, sticky};
endmodule
