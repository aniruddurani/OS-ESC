# silicon-flow: one target per stage. Run through ./flow, e.g. ./flow sim.
# Every stage writes $(REPORTS)/<stage>.json and fails unless status is pass.

DESIGN   ?= inv
# The container's login shell exports a default PDK (ihp-sg13g2), so these use
# plain `=` to win over the environment. Command-line PDK=... still overrides.
# The exports below mirror what the image's sak-pdk script sets for a PDK.
PDK      = sky130A
PDK_ROOT = /foss/pdks

PDKPATH  := $(PDK_ROOT)/$(PDK)
export PDK PDK_ROOT PDKPATH
export SPICE_USERINIT_DIR := $(PDKPATH)/libs.tech/ngspice
export KLAYOUT_PATH := /headless/.klayout:$(PDKPATH)/libs.tech/klayout
export STD_CELL_LIBRARY := sky130_fd_sc_hd
BUILD    ?= build/$(DESIGN)
REPORTS  := $(BUILD)/reports
LOGS     := $(BUILD)/logs

# PDK files. These are the only place PDK paths are written down.
MAGICRC      := $(PDKPATH)/libs.tech/magic/$(PDK).magicrc
NETGEN_SETUP := $(PDKPATH)/libs.tech/netgen/$(PDK)_setup.tcl
XSCHEMRC     := $(PDKPATH)/libs.tech/xschem/xschemrc
KLAYOUT_DRC  := $(PDKPATH)/libs.tech/klayout/drc/$(PDK)_mr.drc
KLAYOUT_LYP  := $(PDKPATH)/libs.tech/klayout/tech/$(PDK).lyp
NGSPICE_LIB  := $(PDKPATH)/libs.tech/combined/sky130.lib.spice

PY := PYTHONPATH=scripts python3 -m flowlib.cli

.PHONY: env clean test

test:
	PYTHONPATH=scripts python3 -m unittest discover -s tests/unit -v

env:
	bash scripts/check_env.sh build/env.json \
	  MAGICRC=$(MAGICRC) NETGEN_SETUP=$(NETGEN_SETUP) XSCHEMRC=$(XSCHEMRC) \
	  KLAYOUT_DRC=$(KLAYOUT_DRC) KLAYOUT_LYP=$(KLAYOUT_LYP) NGSPICE_LIB=$(NGSPICE_LIB)

clean:
	rm -rf $(BUILD)

# Each report rule removes its own outputs first, so a failed tool run can
# never leave an old pass behind, and runs the CLI even if the tool failed
# (leading '-') so the failure is recorded as a report.
SCH      := designs/$(DESIGN)/xschem/$(DESIGN).sch
SYM      := designs/$(DESIGN)/xschem/$(DESIGN).sym
NETLIST  := $(BUILD)/$(DESIGN).spice

TB     := designs/$(DESIGN)/tb/$(DESIGN)_tb.spice
SPEC   := designs/$(DESIGN)/spec.json
CORNER ?= tt

LAYOUT_SCRIPT ?= designs/$(DESIGN)/layout/$(DESIGN).tcl
GDS           := $(BUILD)/$(DESIGN).gds

.PHONY: netlist sim layout drc lvs pex
netlist: $(REPORTS)/netlist.json
sim: $(REPORTS)/sim.json
layout: $(REPORTS)/layout.json
drc: $(REPORTS)/drc.json
lvs: $(REPORTS)/lvs.json
pex: $(REPORTS)/pex.json

PEX_NETLIST := $(BUILD)/$(DESIGN)_pex.spice

# Post-layout simulation: the same testbench and limits as sim, on the netlist
# Magic extracts with parasitic capacitance. Only runs on an LVS-clean layout.
$(REPORTS)/pex.json: $(REPORTS)/lvs.json $(REPORTS)/sim.json $(TB) $(SPEC) scripts/magic_extract_pex.tcl Makefile
	@mkdir -p $(BUILD)/sim $(LOGS)
	@rm -f $@ $(PEX_NETLIST) $(BUILD)/sim/post.spice $(LOGS)/pex.log
	-OUT_DIR=$(BUILD) DESIGN=$(DESIGN) magic -dnull -noconsole -rcfile $(MAGICRC) \
	  scripts/magic_extract_pex.tcl > $(LOGS)/pex_extract.log 2>&1
	$(PY) deck --dut $(PEX_NETLIST) --tb $(TB) --lib $(NGSPICE_LIB) \
	  --corner $(CORNER) --design $(DESIGN) --out $(BUILD)/sim/post.spice
	-ngspice -b -o $(LOGS)/pex.log $(BUILD)/sim/post.spice
	$(PY) sim --stage pex --log $(LOGS)/pex.log --spec $(SPEC) \
	  --compare $(REPORTS)/sim.json --design $(DESIGN) --out $@

EXT_NETLIST := $(BUILD)/$(DESIGN)_ext.spice

# Layout (extracted by Magic) versus the schematic netlist, compared by Netgen.
$(REPORTS)/lvs.json: $(REPORTS)/drc.json $(REPORTS)/netlist.json scripts/magic_extract_lvs.tcl Makefile
	@mkdir -p $(REPORTS) $(LOGS)
	@rm -f $@ $(EXT_NETLIST) $(BUILD)/*.ext $(BUILD)/lvs_netgen.out
	-OUT_DIR=$(BUILD) DESIGN=$(DESIGN) magic -dnull -noconsole -rcfile $(MAGICRC) \
	  scripts/magic_extract_lvs.tcl > $(LOGS)/lvs_extract.log 2>&1
	-netgen -batch lvs "$(EXT_NETLIST) $(DESIGN)" "$(NETLIST) $(DESIGN)" \
	  $(NETGEN_SETUP) $(BUILD)/lvs_netgen.out > $(LOGS)/lvs.log 2>&1
	$(PY) lvs --report $(BUILD)/lvs_netgen.out --log $(LOGS)/lvs.log \
	  --design $(DESIGN) --out $@

# Two independent DRC engines must both be clean: Magic (full rule style) and
# the PDK's KLayout deck.
$(REPORTS)/drc.json: $(REPORTS)/layout.json scripts/magic_drc.tcl Makefile
	@mkdir -p $(REPORTS) $(LOGS)
	@rm -f $@ $(BUILD)/drc_klayout.lyrdb $(LOGS)/drc_magic.log $(LOGS)/drc_klayout.log
	-OUT_DIR=$(BUILD) DESIGN=$(DESIGN) magic -dnull -noconsole -rcfile $(MAGICRC) \
	  scripts/magic_drc.tcl > $(LOGS)/drc_magic.log 2>&1
	@# The deck prefixes the input's directory onto a relative report path, so
	@# both paths are absolute. It runs no front/back-end rules unless
	@# feol/beol are set (drc checks the log for them).
	-klayout -b -r $(KLAYOUT_DRC) -rd input=$(CURDIR)/$(GDS) \
	  -rd report=$(CURDIR)/$(BUILD)/drc_klayout.lyrdb -rd top_cell=$(DESIGN) \
	  -rd feol=true -rd beol=true -rd offgrid=true \
	  > $(LOGS)/drc_klayout.log 2>&1
	$(PY) drc --magic-log $(LOGS)/drc_magic.log \
	  --klayout-report $(BUILD)/drc_klayout.lyrdb --klayout-log $(LOGS)/drc_klayout.log \
	  --design $(DESIGN) --out $@

$(REPORTS)/layout.json: $(LAYOUT_SCRIPT) Makefile
	@mkdir -p $(REPORTS) $(LOGS)
	@# The generator also saves its device cells here; stale ones make gencell
	@# stop and ask a question, so clear every .mag, not only the top cell.
	@rm -f $@ $(GDS) $(BUILD)/*.mag
	-OUT_DIR=$(BUILD) magic -dnull -noconsole -rcfile $(MAGICRC) $(LAYOUT_SCRIPT) \
	  > $(LOGS)/layout.log 2>&1
	$(PY) layout --gds $(GDS) --log $(LOGS)/layout.log --design $(DESIGN) --out $@

$(REPORTS)/sim.json: $(REPORTS)/netlist.json $(TB) $(SPEC) Makefile
	@mkdir -p $(BUILD)/sim $(LOGS)
	@rm -f $@ $(BUILD)/sim/pre.spice $(LOGS)/sim.log
	$(PY) deck --dut $(NETLIST) --tb $(TB) --lib $(NGSPICE_LIB) \
	  --corner $(CORNER) --design $(DESIGN) --out $(BUILD)/sim/pre.spice
	-ngspice -b -o $(LOGS)/sim.log $(BUILD)/sim/pre.spice
	$(PY) sim --stage sim --log $(LOGS)/sim.log --spec $(SPEC) \
	  --design $(DESIGN) --out $@

$(REPORTS)/netlist.json: $(SCH) $(SYM) Makefile
	@mkdir -p $(REPORTS) $(LOGS)
	@rm -f $@ $(NETLIST)
	-xschem --rcfile $(XSCHEMRC) -n -s -q -x \
	  -o $(BUILD) -N $(DESIGN).spice $(SCH) > $(LOGS)/netlist.log 2>&1
	@# xschem writes a top-level schematic's header as '**.subckt' and footer
	@# as '**.ends' (comments); setting top_is_subckt makes it drop the devices.
	@# A bare '.end' would stop ngspice reading when this file is .included.
	-sed -i -e 's/^\*\*\.subckt /.subckt /' -e 's/^\*\*\.ends/.ends/' -e '/^\.end$$/d' $(NETLIST)
	$(PY) netlist --netlist $(NETLIST) --log $(LOGS)/netlist.log \
	  --design $(DESIGN) --out $@
