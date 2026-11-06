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
# The build's identity (a hash of its sources), compiled into the image and read back from
# the board, so every later stage knows it is testing what bbuild judged.
BUILD_ID_FILE ?= $(QDIR)/build_id.txt
BBUILD_SRC := $(wildcard $(DRTL_DIR)/*.v) $(wildcard $(BOARD)/rtl/*.v) $(BOARD)/esc_board.tcl \
              $(BOARD)/esc_board.sdc designs/$(DDESIGN)/librelane/default.yaml board.mk

.PHONY: bbuild
bbuild: $(DSTAMPS)/bbuild.pass

# Quartus compile (synthesis, fit, assembler, timing), judged from Quartus's own reports.
$(DSTAMPS)/bbuild.pass: $(DSTAMPS)/dsim.pass $(BBUILD_SRC)
	@mkdir -p $(DREPORTS) $(DLOGS) $(DSTAMPS) $(QDIR)
	@rm -f $@ $(DREPORTS)/bbuild.json $(QDIR)/esc_board.*.rpt $(QDIR)/esc_board.sof
	$(PY) buildid --dir $(DRTL_DIR) --dir $(BOARD)/rtl --dir $(BOARD) \
	  --settings $(PLL_MUL)/$(PLL_DIV) --out $(QDIR)/build_id.txt
	-cd $(QDIR) && "$(QBIN)/quartus_sh" -t "$(CURDIR)/$(BOARD)/esc_board.tcl" \
	  -pll_mul $(PLL_MUL) -pll_div $(PLL_DIV) -rtl_dir "$(abspath $(DRTL_DIR))" \
	  -build_id $$(cat build_id.txt) > "$(CURDIR)/$(DLOGS)/bbuild.log" 2>&1
	$(PY) bbuild --sta-rpt $(QDIR)/esc_board.sta.rpt --map-rpt $(QDIR)/esc_board.map.rpt \
	  --fit-rpt $(QDIR)/esc_board.fit.rpt --params-from designs/$(DDESIGN)/librelane/default.yaml \
	  --core-mhz 80 --design $(DDESIGN) --out $(DREPORTS)/bbuild.json
	@touch $@

# Load the build over JTAG (SW19 on RUN), then wait up to 5 s for the PHY link. Cable 1 is
# the first one Quartus lists: the USB-Blaster's port number changes across reboots.
STP      := $(QBIN)/quartus_stp
BSTATUS  := python designs/$(DDESIGN)/board/board_status.py
.PHONY: bprog bstatus
bprog: $(DSTAMPS)/bprog.pass
$(DSTAMPS)/bprog.pass: $(DSTAMPS)/bbuild.pass
	@mkdir -p $(DREPORTS) $(DLOGS) $(DSTAMPS)
	@rm -f $@ $(DREPORTS)/bprog.json $(DLOGS)/bprog_status.json
	-"$(QBIN)/quartus_pgm" -c 1 -m JTAG -o "p;$(QDIR)/esc_board.sof@1" \
	  > $(DLOGS)/bprog.log 2>&1
	-$(BSTATUS) wait --stp "$(STP)" --seconds 5 --out $(DLOGS)/bprog_status.json
	$(PY) bprog --pgm-log $(DLOGS)/bprog.log --status-json $(DLOGS)/bprog_status.json \
	  --build-id-file $(BUILD_ID_FILE) --design $(DDESIGN) --out $(DREPORTS)/bprog.json
	@touch $@

# The board's status now (not a gate).
bstatus:
	@$(BSTATUS) read --stp "$(STP)"

# The live view of netsim/gui on the board (not a gate): http://127.0.0.1:$(BWATCH_PORT).
# The container holds 8765. SCENARIO, GUI_K as for ./flow dnet GUI=1.
SCENARIO    ?= cyclic
GUI_K       ?= 3
BWATCH_PORT ?= 8766
.PHONY: bwatch
bwatch:
	python designs/$(DDESIGN)/netsim/run_board.py --watch --mac "$(BOARD_MAC)" --stp "$(STP)" \
	  --scenario $(SCENARIO) --k $(GUI_K) --port $(BWATCH_PORT) --out $(DBUILD)/board/gui

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
	  --size $(BNET) --out $(BNET_OUT) --stp "$(STP)" --build-id $$(cat $(BUILD_ID_FILE)) \
	  $(BNET_ARGS) > $(DLOGS)/$(BNET_TAG).log 2>&1
	$(PY) bnet --results $(BNET_OUT)/results.json --manifest $(BMANIFEST) --size $(BNET) \
	  --build-id-file $(BUILD_ID_FILE) --design $(DDESIGN) --out $(DREPORTS)/$(BNET_TAG).json
	@touch $@

# Two SOEM programs against the board: its low-level calls (BRD, addressing, FMMU,
# SyncManagers, cyclic LRW), and its standard start-up through to OP and cyclic process data,
# each after a soft reset. SOEM is fetched by CMake at a pinned tag; the Npcap SDK is at NPCAP_SDK.
NPCAP_SDK ?= C:/npcap-sdk
SOEM_DIR  ?= $(DBUILD)/board/soem
SOEM_EXE  := $(SOEM_DIR)/soem_test.exe
SOEM_CONFIG_EXE := $(SOEM_DIR)/soem_config.exe
BOARD_NPF ?= $(shell powershell -NoProfile -Command "'\Device\NPF_' + (Get-NetAdapter -Name '$(BOARD_NIC)').InterfaceGuid")
.PHONY: bsoem
bsoem: $(DSTAMPS)/bsoem.pass
$(SOEM_EXE) $(SOEM_CONFIG_EXE): $(wildcard $(BOARD)/../soem_test/*.c) $(BOARD)/../soem_test/CMakeLists.txt
	cmake -G "MinGW Makefiles" -S designs/$(DDESIGN)/board/soem_test -B $(SOEM_DIR) \
	  -DNPCAP_SDK=$(NPCAP_SDK) > $(DLOGS)/soem_build.log 2>&1
	mingw32-make -C $(SOEM_DIR) >> $(DLOGS)/soem_build.log 2>&1
$(DSTAMPS)/bsoem.pass: $(DSTAMPS)/bprog.pass $(SOEM_EXE) $(SOEM_CONFIG_EXE)
	@mkdir -p $(DREPORTS) $(DLOGS) $(DSTAMPS)
	@rm -f $@ $(DREPORTS)/bsoem.json $(DLOGS)/bsoem.out $(DLOGS)/bsoem_config.out \
	  $(DLOGS)/bsoem_before.json $(DLOGS)/bsoem_after.json
	-$(BSTATUS) reset --stp "$(STP)" > $(DLOGS)/bsoem_before.json
	-"$(SOEM_EXE)" '$(BOARD_NPF)' > $(DLOGS)/bsoem.out 2> $(DLOGS)/bsoem.err
	-$(BSTATUS) reset --stp "$(STP)" > /dev/null
	-"$(SOEM_CONFIG_EXE)" '$(BOARD_NPF)' > $(DLOGS)/bsoem_config.out 2> $(DLOGS)/bsoem_config.err
	-$(BSTATUS) read --stp "$(STP)" > $(DLOGS)/bsoem_after.json
	$(PY) bsoem --result $(DLOGS)/bsoem.out --config-result $(DLOGS)/bsoem_config.out \
	  --before $(DLOGS)/bsoem_before.json \
	  --after $(DLOGS)/bsoem_after.json --build-id-file $(BUILD_ID_FILE) \
	  --design $(DDESIGN) --out $(DREPORTS)/bsoem.json
	@touch $@

# Records SOEM's real traffic for the replay test (a tool, not a gate). The capture is
# refused unless its frames pair up as request and reply.
CAPTURES := designs/$(DDESIGN)/captures
.PHONY: bcapture
bcapture: $(SOEM_CONFIG_EXE)
	$(BSTATUS) reset --stp "$(STP)"
	python designs/$(DDESIGN)/board/capture_soem.py --mac "$(BOARD_MAC)" \
	  --out $(CAPTURES)/board_soem_start.pcap -- "$(abspath $(SOEM_CONFIG_EXE))" '$(BOARD_NPF)' 200

# Proves the board gates catch real faults (scripts/selftest_board.sh). STEP=nolink, with
# the board's cable unplugged, checks that bnet without a link is an error.
STEP ?= main
.PHONY: bselftest
bselftest:
	bash scripts/selftest_board.sh $(STEP)
