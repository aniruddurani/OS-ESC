# FPGA board stages: the ESC on a Terasic DE2-115. These run on the Windows host
# (Quartus, Npcap, SOEM), not in the container: ./flow runs `mingw32-make -f board.mk`.
# Same rules as digital.mk: each stage writes build/<DDESIGN>/reports/<stage>.json and its
# pass stamp only on pass, and a stage runs only on its prerequisite's stamp.
#   dsim (container) -> bbuild -> bprog -> bnet, bsoem

DDESIGN  ?= ecat_esc
DBUILD   ?= build/$(DDESIGN)
DREPORTS := $(DBUILD)/reports
DLOGS    := $(DBUILD)/logs
DSTAMPS  := $(DBUILD)/stamps
BOARD    := designs/$(DDESIGN)/board/de2_115
QDIR     := $(DBUILD)/board/de2_115
QBIN     ?= C:/altera_lite/25.1std/quartus/bin64
PY       := PYTHONPATH=scripts python -m flowlib.cli

DRTL_DIR ?= designs/$(DDESIGN)/rtl
PLL_MUL  ?= 8
PLL_DIV  ?= 5                                 # PLL_MUL/PLL_DIV other than 8/5 fail bbuild (self-test only)
BBUILD_SRC := $(wildcard $(DRTL_DIR)/*.v) $(wildcard $(BOARD)/rtl/*.v) $(BOARD)/esc_board.tcl \
              $(BOARD)/esc_board.sdc designs/$(DDESIGN)/librelane/default.yaml board.mk

.PHONY: bbuild
bbuild: $(DSTAMPS)/bbuild.pass

# Quartus compile (synthesis, fit, assembler, timing), judged from Quartus's own reports.
$(DSTAMPS)/bbuild.pass: $(DSTAMPS)/dsim.pass $(BBUILD_SRC)
	@mkdir -p $(DREPORTS) $(DLOGS) $(DSTAMPS) $(QDIR)
	@rm -f $@ $(DREPORTS)/bbuild.json $(QDIR)/esc_board.*.rpt $(QDIR)/esc_board.sof
	-cd $(QDIR) && "$(QBIN)/quartus_sh" -t "$(CURDIR)/$(BOARD)/esc_board.tcl" \
	  -pll_mul $(PLL_MUL) -pll_div $(PLL_DIV) -rtl_dir "$(abspath $(DRTL_DIR))" \
	  > "$(CURDIR)/$(DLOGS)/bbuild.log" 2>&1
	$(PY) bbuild --sta-rpt $(QDIR)/esc_board.sta.rpt --map-rpt $(QDIR)/esc_board.map.rpt \
	  --fit-rpt $(QDIR)/esc_board.fit.rpt --params-from designs/$(DDESIGN)/librelane/default.yaml \
	  --core-mhz 80 --design $(DDESIGN) --out $(DREPORTS)/bbuild.json
	@touch $@

# Load the build over JTAG (SW19 on RUN), then wait up to 5 s for the PHY link.
STP      := $(QBIN)/quartus_stp
BSTATUS  := python designs/$(DDESIGN)/board/board_status.py
.PHONY: bprog bstatus
bprog: $(DSTAMPS)/bprog.pass
$(DSTAMPS)/bprog.pass: $(DSTAMPS)/bbuild.pass
	@mkdir -p $(DREPORTS) $(DLOGS) $(DSTAMPS)
	@rm -f $@ $(DREPORTS)/bprog.json $(DLOGS)/bprog_status.json
	-"$(QBIN)/quartus_pgm" -c "USB-Blaster [USB-1]" -m JTAG -o "p;$(QDIR)/esc_board.sof@1" \
	  > $(DLOGS)/bprog.log 2>&1
	-$(BSTATUS) wait --stp "$(STP)" --seconds 5 --out $(DLOGS)/bprog_status.json
	$(PY) bprog --pgm-log $(DLOGS)/bprog.log --status-json $(DLOGS)/bprog_status.json \
	  --design $(DDESIGN) --out $(DREPORTS)/bprog.json
	@touch $@

# The board's status now (not a gate).
bstatus:
	@$(BSTATUS) read --stp "$(STP)"

# The netsim scenarios against the board, over the NIC cabled to its ETHERNET 0.
# BNET=soak for the long run; BNET_ARGS for run_board.py (--only, --frames, --sabotage).
BNET      ?= quick
BNET_ARGS ?=
BNET_TAG  := $(if $(filter soak,$(BNET)),bnet_soak,bnet)
BNET_OUT  := $(DBUILD)/board/net/$(BNET)
BOARD_NIC ?= Ethernet
BOARD_MAC ?= $(shell powershell -NoProfile -Command "(Get-NetAdapter -Name '$(BOARD_NIC)').MacAddress")
BMANIFEST := $(BOARD)/manifest.txt
.PHONY: bnet
bnet: $(DSTAMPS)/$(BNET_TAG).pass
$(DSTAMPS)/$(BNET_TAG).pass: $(DSTAMPS)/bprog.pass $(wildcard designs/$(DDESIGN)/netsim/*.py) \
                             $(wildcard designs/$(DDESIGN)/model/*.py) $(BMANIFEST) board.mk
	@mkdir -p $(DREPORTS) $(DLOGS) $(DSTAMPS) $(BNET_OUT)
	@rm -f $@ $(DREPORTS)/$(BNET_TAG).json $(BNET_OUT)/results.json $(BNET_OUT)/fail_*
	-python designs/$(DDESIGN)/netsim/run_board.py --mac "$(BOARD_MAC)" --manifest $(BMANIFEST) \
	  --size $(BNET) --out $(BNET_OUT) --stp "$(STP)" $(BNET_ARGS) > $(DLOGS)/$(BNET_TAG).log 2>&1
	$(PY) bnet --results $(BNET_OUT)/results.json --manifest $(BMANIFEST) --size $(BNET) \
	  --design $(DDESIGN) --out $(DREPORTS)/$(BNET_TAG).json
	@touch $@
