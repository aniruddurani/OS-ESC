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

.PHONY: netlist
netlist: $(REPORTS)/netlist.json

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
