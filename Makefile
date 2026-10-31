# silicon-flow: one target per stage. Run through ./flow, e.g. ./flow sim.
# Every stage writes $(REPORTS)/<stage>.json and fails unless status is pass.

DESIGN   ?= inv
PDK      ?= sky130A
PDK_ROOT ?= /foss/pdks
export PDK PDK_ROOT

PDKPATH  := $(PDK_ROOT)/$(PDK)
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
