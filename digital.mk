# Digital stages for RTL designs (designs/<DDESIGN>/rtl). Included by Makefile.
# Same rules as the analog stages: each stage writes a report, and its pass
# stamp only on pass, so later stages never run on a failing earlier one.
#   dlint -> dsim -> dsynth -> dpnr   (dsynth/dpnr rules arrive with the LibreLane config)

DDESIGN  ?= ecat_esc
DBUILD   ?= build/$(DDESIGN)
DREPORTS := $(DBUILD)/reports
DLOGS    := $(DBUILD)/logs
DSTAMPS  := $(DBUILD)/stamps
DRTL     := $(sort $(wildcard designs/$(DDESIGN)/rtl/*.v))
DTB      := designs/$(DDESIGN)/tb
DTB_SRC  := $(wildcard $(DTB)/*.py) $(DTB)/manifest.txt

.PHONY: dlint dsim
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
	-@for f in $(DRTL); do m=$$(basename $$f .v); 	  verilator --lint-only -Wall --top-module $$m $(DRTL) >> $(DLOGS)/dlint.log 2>&1; done
	$(PY) dlint --log $(DLOGS)/dlint.log --design $(DDESIGN) --out $(DREPORTS)/dlint.json
	@touch $@

# cocotb tests. tb/run.py runs every test module and writes one combined
# results.xml; the stage passes only if every test in tb/manifest.txt ran.
$(DSTAMPS)/dsim.pass: $(DSTAMPS)/dlint.pass $(DRTL) $(DTB_SRC) Makefile digital.mk
	@mkdir -p $(DBUILD)/sim $(DREPORTS) $(DLOGS) $(DSTAMPS)
	@rm -f $@ $(DREPORTS)/dsim.json $(DBUILD)/sim/results.xml $(DLOGS)/dsim.log
	-python3 $(DTB)/run.py --rtl designs/$(DDESIGN)/rtl --build $(DBUILD)/sim \
	  --results $(DBUILD)/sim/results.xml > $(DLOGS)/dsim.log 2>&1
	$(PY) dsim --results $(DBUILD)/sim/results.xml --manifest $(DTB)/manifest.txt \
	  --design $(DDESIGN) --out $(DREPORTS)/dsim.json
	@touch $@
