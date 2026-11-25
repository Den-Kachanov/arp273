# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
VENV    ?= .venv
PYTHON  ?= $(VENV)/bin/python
PYTEST  ?= $(PYTHON) -m pytest

# Package
PKG_SRC := $(sort $(wildcard rtl/pkg/*.sv))

MOD_SRC := $(shell grep -l '^[[:space:]]*module[[:space:]]' $(sort $(wildcard rtl/core/*.sv rtl/vec/*.sv rtl/gpu/*.sv rtl/soc/*.sv)) 2>/dev/null)

XLENS   := 64 32
VLINT   := verilator --lint-only -Wall

.PHONY: help lint struct unit synth sim compare regress fuzz area schematics schematics-all floorplan docs-img mutate spdx clean

help:
	@echo "make lint            lint every module for XLEN = 64 and 32"
	@echo "make struct          Yosys: no latches, async resets, loops or multiple drivers (chip rules)"
	@echo "make unit            all cocotb tests (tb/unit)"
	@echo "make unit T=alu      only tb/unit/test_alu.py"
	@echo "make synth           Yosys: cell count of every module"
	@echo "make sim PROG=fib    build sw/fib.c and run it on the core (commit log and counters)"
	@echo "make compare PROG=fib  compare the core commit log with Spike"
	@echo "make regress           compare every program in sw/ with Spike (CORE=pipe: the pipelined core)"
	@echo "make fuzz SEEDS='1 2 3'  random programs (scripts/gen_prog.py) against Spike"
	@echo "make area            area and logic delay of the modules (scripts/area.py)"
	@echo "make schematics      schematics drawn from the RTL (scripts/schematic.py, needs npm --prefix tools ci)"
	@echo "make schematics-all  every module at every level, in parallel (J=8 to limit the jobs)"
	@echo "make floorplan       die floorplan estimate: block areas, placement, buses (scripts/floorplan.py)"
	@echo "make docs-img        regenerate docs/img/generated/ (committed, shown on GitHub by docs/diagrams.md)"
	@echo "make mutate M=alu    mutation check of a module's tests (scripts/mutate.py)"
	@echo "make spdx            check the SPDX headers"
	@echo "make clean           remove build artefacts"

lint:
	@if [ -z "$(MOD_SRC)" ]; then echo "lint: no modules yet, checking the package only"; fi
	@for x in $(XLENS); do \
	  for f in $(PKG_SRC); do \
	    echo "== lint XLEN=$$x $$f"; \
	    $(VLINT) -Wno-UNUSEDPARAM -DXLEN=$$x $$f || exit 1; \
	  done; \
	  for f in $(MOD_SRC); do \
	    top=$$(basename $$f .sv); \
	    echo "== lint XLEN=$$x $$top"; \
	    $(VLINT) -Wno-UNUSEDPARAM -DXLEN=$$x --top-module $$top $(PKG_SRC) $(MOD_SRC) || exit 1; \
	  done; \
	done
	@echo "lint: OK"

# Structural rules that a chip flow needs (ADR 011): after `proc` no latches, no asynchronous
# set/reset flip-flops (the project uses synchronous rst_ni), no combinational loops, no signal
# with several drivers, no undriven wire used. `check -assert` fails on loops and multiple drivers
struct:
	@for top in core core_pipe; do for x in $(XLENS); do \
	  echo "== struct XLEN=$$x $$top"; \
	  yosys -q -p "read_verilog -sv -DXLEN=$$x $(PKG_SRC) $(CORE_SRC); hierarchy -check -top $$top; proc; flatten; \
	    check -assert; select -assert-none t:\$$dlatch t:\$$adlatch t:\$$sr t:\$$dffsr t:\$$adff t:\$$aldff t:\$$adffe t:\$$dffsre" || exit 1; \
	done; done
	@echo "struct: OK"

unit:
	@if ls tb/unit/test_*.py >/dev/null 2>&1; then \
	  $(PYTEST) -v $(if $(T),tb/unit/test_$(T).py,tb/unit); \
	else echo "unit: no tests in tb/unit yet"; fi

synth:
	@for f in $(MOD_SRC); do \
	  top=$$(basename $$f .sv); \
	  echo "== synth $$top"; \
	  yosys -q -p "read_verilog -sv -DXLEN=64 $(PKG_SRC) $(MOD_SRC); synth -top $$top; stat" || exit 1; \
	done

# Software and simulation (RV64I: one ELF runs on Spike and on the core)
BUILD    := build
CROSS    ?= riscv64-elf-
CC       := $(CROSS)gcc
CFLAGS   := -march=rv64im_zicsr -mabi=lp64 -mcmodel=medany -O2 -ffreestanding -nostdlib -nostartfiles
SPIKE    := spike
PROGS    := $(basename $(notdir $(wildcard sw/*.c)))
CORE_SRC := $(sort $(wildcard rtl/core/*.sv))

# keep the ELF: Spike and nm need it after the .bin is built
.PRECIOUS: $(BUILD)/%.elf $(BUILD)/fuzz_%.S

$(BUILD)/%.elf: sw/%.c sw/crt0.S sw/link.ld
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -Wl,--no-warn-rwx-segments -T sw/link.ld sw/crt0.S $< -o $@
	$(CROSS)objdump -d $@ > $(BUILD)/$*.dis

# generated programs: build/fuzz_<seed>.S from scripts/gen_prog.py
$(BUILD)/fuzz_%.S: scripts/gen_prog.py
	@mkdir -p $(BUILD)
	$(PYTHON) scripts/gen_prog.py --seed $* -o $@

$(BUILD)/%.elf: $(BUILD)/%.S sw/crt0.S sw/link.ld
	$(CC) $(CFLAGS) -Wl,--no-warn-rwx-segments -T sw/link.ld sw/crt0.S $< -o $@
	$(CROSS)objdump -d $@ > $(BUILD)/$*.dis

$(BUILD)/%.bin: $(BUILD)/%.elf
	$(CROSS)objcopy -O binary $< $@

# CORE=single (default): the single-cycle core; CORE=pipe: the pipelined core (ADR 013).
# For the pipeline, env MEM_STALL (0..1) makes the memory stall at random, MEM_SEED picks the run
CORE ?= single
ifeq ($(CORE),pipe)
SIM_BIN := $(BUILD)/core_pipe_sim
else
SIM_BIN := $(BUILD)/core_sim
endif
export MEM_STALL MEM_SEED X_SEED

VERILATOR_SIM = verilator --cc --exe --build -j 4 -O2 -Wno-fatal -DXLEN=64 --prefix Vtop \
  --x-assign unique --x-initial unique

$(BUILD)/core_sim: $(PKG_SRC) $(CORE_SRC) tb/core/main.cpp
	@mkdir -p $(BUILD)
	$(VERILATOR_SIM) --top-module core --Mdir $(BUILD)/obj_core -o core_sim $(PKG_SRC) $(CORE_SRC) tb/core/main.cpp
	cp $(BUILD)/obj_core/core_sim $@

$(BUILD)/core_pipe_sim: $(PKG_SRC) $(CORE_SRC) tb/core/main.cpp
	@mkdir -p $(BUILD)
	$(VERILATOR_SIM) --top-module core_pipe -CFLAGS -DPIPE --Mdir $(BUILD)/obj_core_pipe -o core_pipe_sim \
	  $(PKG_SRC) $(CORE_SRC) tb/core/main.cpp
	cp $(BUILD)/obj_core_pipe/core_pipe_sim $@

ifneq ($(filter sim compare,$(MAKECMDGOALS)),)
ifeq ($(PROG),)
$(error set PROG=<name from sw/>, for example: make compare PROG=fib)
endif
endif

sim: $(SIM_BIN) $(BUILD)/$(PROG).bin
	$(SIM_BIN) $(BUILD)/$(PROG).bin $(BUILD)/$(PROG).$(CORE).log \
	  $$($(CROSS)nm $(BUILD)/$(PROG).elf | awk '$$3=="tohost"{print $$1}')

compare: sim
	-$(SPIKE) --isa=rv64im -m0x80000000:0x100000 --log-commits $(BUILD)/$(PROG).elf 2> $(BUILD)/$(PROG).spike.log
	$(PYTHON) scripts/compare.py $(BUILD)/$(PROG).$(CORE).log $(BUILD)/$(PROG).spike.log

regress:
	@for p in $(PROGS); do \
	  echo "== compare $$p"; \
	  $(MAKE) --no-print-directory compare PROG=$$p CORE=$(CORE) > $(BUILD)/$$p.$(CORE).regress.log 2>&1 || { tail -15 $(BUILD)/$$p.$(CORE).regress.log; exit 1; }; \
	  tail -1 $(BUILD)/$$p.$(CORE).regress.log; \
	done

SEEDS ?= 1 2 3 4 5 6 7 8
fuzz:
	@for n in $(SEEDS); do \
	  echo "== fuzz seed $$n"; \
	  $(MAKE) --no-print-directory compare PROG=fuzz_$$n CORE=$(CORE) > $(BUILD)/fuzz_$$n.$(CORE).regress.log 2>&1 || { tail -15 $(BUILD)/fuzz_$$n.$(CORE).regress.log; exit 1; }; \
	  tail -1 $(BUILD)/fuzz_$$n.$(CORE).regress.log; \
	done

area:
	@mkdir -p $(BUILD)
	$(PYTHON) scripts/area.py --xc7 $(if $(COMMIT),--commit $(COMMIT)) --json $(BUILD)/area.json

schematics:
	$(PYTHON) scripts/schematic.py $(if $(M),$(M) --level $(or $(LEVEL),rtl)) $(if $(XLEN),--xlen $(XLEN))

schematics-all:
	$(PYTHON) scripts/schematic.py --all $(if $(J),-j $(J)) $(if $(XLEN),--xlen $(XLEN))

floorplan:
	$(PYTHON) scripts/floorplan.py $(if $(J),-j $(J)) $(if $(XLEN),--xlen $(XLEN))

# Three simple generated diagrams that are committed and shown on GitHub (docs/diagrams.md); the dense
# ones (block diagrams, gate-level, netlist graphs) stay local: make schematics-all.
# Every file is deterministic (fixed seeds, no dates), so it only changes when the RTL changes.
# The numbers depend on the Yosys version: regenerate with the same tools before comparing
DOCS_GEN := docs/img/generated
DOCS_TMP := $(BUILD)/docs-img
docs-img:
	@rm -rf $(DOCS_TMP)
	$(PYTHON) scripts/schematic.py core_pipe --level map --out $(DOCS_TMP)
	$(PYTHON) scripts/schematic.py branch_unit --level rtl --out $(DOCS_TMP)
	$(PYTHON) scripts/floorplan.py --top core_pipe --out $(DOCS_TMP)/floorplan
	@rm -rf $(DOCS_GEN)
	@mkdir -p $(DOCS_GEN)
	cp $(DOCS_TMP)/floorplan/core_pipe_xlen64.svg     $(DOCS_GEN)/core_pipe_floorplan.svg
	cp $(DOCS_TMP)/map/core_pipe_xlen64.svg           $(DOCS_GEN)/core_pipe_gate_map.svg
	cp $(DOCS_TMP)/rtl/branch_unit_xlen64.svg         $(DOCS_GEN)/branch_unit_rtl.svg
	@ls -l $(DOCS_GEN)

mutate:
	@test -n "$(M)" || { echo "mutate: set M=<name from scripts/mutants>"; exit 1; }
	$(PYTHON) scripts/mutate.py $(M)

spdx:
	$(PYTHON) scripts/check_spdx.py

clean:
	rm -rf $(BUILD) obj_dir sim_build tb/unit/sim_build tb/unit/__pycache__ .pytest_cache
	find . -path ./.venv -prune -o -name 'dump.vcd' -print -delete
