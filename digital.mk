# Digital stages for RTL designs (designs/<DDESIGN>/rtl). Included by Makefile.
# Same rules as the analog stages: each stage writes a report, and its pass
# stamp only on pass, so later stages never run on a failing earlier one.
#   dlint -> dsim -> dsynth -> dpnr
# dsynth/dpnr run LibreLane on designs/<DDESIGN>/librelane/<LLCFG>.yaml (LLCFG = min,
# default or max); their stamps and reports are per configuration.

DDESIGN  ?= ecat_esc
DBUILD   ?= build/$(DDESIGN)
DREPORTS := $(DBUILD)/reports
DLOGS    := $(DBUILD)/logs
DSTAMPS  := $(DBUILD)/stamps
DRTL_DIR ?= designs/$(DDESIGN)/rtl
DRTL     ?= $(sort $(wildcard $(DRTL_DIR)/*.v))
DTB      := designs/$(DDESIGN)/tb
DTB_SRC  := $(wildcard $(DTB)/*.py) $(DTB)/manifest.txt
DLINT_EXTRA ?= $(sort $(wildcard $(DTB)/lint/*.v))   # declarations of macros the RTL instantiates

.PHONY: dlint dsim dsynth dpnr dall dconfigs dselftest
dlint: $(DSTAMPS)/dlint.pass
dsim: $(DSTAMPS)/dsim.pass

# Verilator lint with all warnings on; the stage passes only with none. Every
# module is linted as its own top (a library of blocks has no single top, and
# MULTITOP is not silenced); the log collects the output of all runs.
$(DSTAMPS)/dlint.pass: $(DRTL) Makefile digital.mk
	@mkdir -p $(DREPORTS) $(DLOGS) $(DSTAMPS)
	@rm -f $@ $(DREPORTS)/dlint.json $(DLOGS)/dlint.log
	@: > $(DLOGS)/dlint.log
	-@if [ -z "$(DRTL)" ]; then verilator --lint-only -Wall >> $(DLOGS)/dlint.log 2>&1; fi
	-@for f in $(DRTL); do m=$$(basename $$f .v); 	  verilator --lint-only -Wall --top-module $$m $(DRTL) $(DLINT_EXTRA) >> $(DLOGS)/dlint.log 2>&1; done
	$(PY) dlint --log $(DLOGS)/dlint.log --design $(DDESIGN) --out $(DREPORTS)/dlint.json
	@touch $@

# cocotb tests. tb/run.py runs every test module and writes one combined
# results.xml; the stage passes only if every test in tb/manifest.txt ran.
$(DSTAMPS)/dsim.pass: $(DSTAMPS)/dlint.pass $(DRTL) $(DTB_SRC) Makefile digital.mk
	@mkdir -p $(DBUILD)/sim $(DREPORTS) $(DLOGS) $(DSTAMPS)
	@rm -f $@ $(DREPORTS)/dsim.json $(DBUILD)/sim/results.xml $(DLOGS)/dsim.log
	-python3 $(DTB)/run.py --rtl $(DRTL_DIR) --build $(DBUILD)/sim \
	  --results $(DBUILD)/sim/results.xml > $(DLOGS)/dsim.log 2>&1
	$(PY) dsim --results $(DBUILD)/sim/results.xml --manifest $(DTB)/manifest.txt \
	  --design $(DDESIGN) --out $(DREPORTS)/dsim.json
	@touch $@

# --- LibreLane: synthesis and place-and-route with signoff --------------------------
LLCFG  ?= default
LLDIR  := designs/$(DDESIGN)/librelane
LLRUNS := $(LLDIR)/runs
LL      = librelane $(LLDIR)/$(LLCFG).yaml --pdk $(PDK) --pdk-root $(PDK_ROOT)           --scl $(STD_CELL_LIBRARY) --overwrite
LL_SRC  = $(LLDIR)/$(LLCFG).yaml $(LLDIR)/esc.sdc $(DRTL)
CORE_PERIOD := 10
# Metrics of a LibreLane run: final/metrics.json, or (if the flow stopped early) the
# newest step's state_out.json, so a failing run still reports what it recorded.
LASTMETRICS = $$(m=$(1); if [ -f $$m/final/metrics.json ]; then echo $$m/final/metrics.json; else ls $$m/*/state_out.json 2>/dev/null | sort | tail -1 | grep . || echo $$m/none.json; fi)

dsynth: $(DSTAMPS)/dsynth_$(LLCFG).pass
dpnr:   $(DSTAMPS)/dpnr_$(LLCFG).pass
dall: dlint dsim dsynth dpnr

# Yosys synthesis and its checks (no latches, no unmapped cells, no combinational loops).
$(DSTAMPS)/dsynth_$(LLCFG).pass: $(DSTAMPS)/dsim.pass $(LL_SRC) Makefile digital.mk
	@mkdir -p $(DREPORTS) $(DLOGS) $(DSTAMPS)
	@rm -f $@ $(DREPORTS)/dsynth_$(LLCFG).json
	-$(LL) --run-tag synth_$(LLCFG) --to Checker.YosysSynthChecks > $(DLOGS)/ll_synth_$(LLCFG).log 2>&1
	$(PY) dsynth --metrics $(call LASTMETRICS,$(LLRUNS)/synth_$(LLCFG)) --preset dsynth 	  --design $(DDESIGN) --out $(DREPORTS)/dsynth_$(LLCFG).json
	@touch $@

# The whole flow: place, clock tree, route, then DRC (Magic + KLayout), LVS, antenna and
# multi-corner static timing. Evidence is LibreLane's own metrics file.
$(DSTAMPS)/dpnr_$(LLCFG).pass: $(DSTAMPS)/dsynth_$(LLCFG).pass
	@mkdir -p $(DREPORTS) $(DLOGS) $(DSTAMPS)
	@rm -f $@ $(DREPORTS)/dpnr_$(LLCFG).json
	-$(LL) --run-tag pnr_$(LLCFG) > $(DLOGS)/ll_pnr_$(LLCFG).log 2>&1
	$(PY) dpnr --metrics $(call LASTMETRICS,$(LLRUNS)/pnr_$(LLCFG)) --preset dpnr 	  --design $(DDESIGN) --out $(DREPORTS)/dpnr_$(LLCFG).json
	@touch $@

# Area and frequency of every configuration (a report, not a gate): runs the three
# configurations through dpnr and tabulates their metrics.
dconfigs:
	@for c in min default max; do $(MAKE) --no-print-directory dpnr LLCFG=$$c || true; done
	$(PY) dconfigs --design $(DDESIGN) --clock-period $(CORE_PERIOD) 	  --metrics min=$(LLRUNS)/pnr_min/final/metrics.json 	  --metrics default=$(LLRUNS)/pnr_default/final/metrics.json 	  --metrics max=$(LLRUNS)/pnr_max/final/metrics.json 	  --out $(DREPORTS)/configs.json

# Proves the digital gates catch real faults (see scripts/selftest_digital.sh).
dselftest:
	bash scripts/selftest_digital.sh
