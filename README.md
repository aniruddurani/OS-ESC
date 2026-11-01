# OS-ESC
Open Source ESC (EtherCAT Slave Controller) to beat every ESC the industry uses.

This repository is the silicon-flow design flow (below), which the EtherCAT slave
controller in `designs/ecat_esc` is built with.

# silicon-flow

An open-source IC design flow (sky130A). One command, `./flow all`,
takes a CMOS inverter from schematic to post-layout simulation, gating every
stage on a JSON report.

Start with [docs/FLOW.md](docs/FLOW.md): how to run it, the flow order, the rules, and
the non-obvious details. The design is in
[docs/specs/](docs/specs/).
