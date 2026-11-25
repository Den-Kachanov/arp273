# SPDX-License-Identifier: CERN-OHL-S-2.0
VENV    ?= .venv
PYTHON  ?= $(VENV)/bin/python
PYTEST  ?= $(PYTHON) -m pytest

# Package
PKG_SRC := $(sort $(wildcard rtl/pkg/*.sv))

MOD_SRC := $(shell grep -l '^[[:space:]]*module[[:space:]]' $(sort $(wildcard rtl/core/*.sv rtl/vec/*.sv rtl/gpu/*.sv rtl/soc/*.sv)) 2>/dev/null)

XLENS   := 64 32
VLINT   := verilator --lint-only -Wall

.PHONY: help lint struct unit synth sim compare regress fuzz area mutate spdx clean

help:
	@echo "make lint            lint every module for XLEN = 64 and 32"
	@echo "make struct          Yosys: no latches, async resets, loops or multiple drivers (chip rules)"
	@echo "make unit            all cocotb tests (tb/unit)"
	@echo "make unit T=alu      only tb/unit/test_alu.py"
	@echo "make synth           Yosys: cell count of every module"
	@echo "make sim PROG=fib    build sw/fib.c and run it on the core (commit log and counters)"
	@echo "make compare PROG=fib  compare the core commit log with Spike"
	@echo "make regress           compare every program in sw/ with Spike"
	@echo "make fuzz SEEDS='1 2 3'  random programs (scripts/gen_prog.py) against Spike"
	@echo "make area            area and logic delay of the modules (scripts/area.py)"
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
	@for x in $(XLENS); do \
	  echo "== struct XLEN=$$x core"; \
	  yosys -q -p "read_verilog -sv -DXLEN=$$x $(PKG_SRC) $(CORE_SRC); hierarchy -check -top core; proc; flatten; \
	    check -assert; select -assert-none t:\$$dlatch t:\$$adlatch t:\$$sr t:\$$dffsr t:\$$adff t:\$$aldff t:\$$adffe t:\$$dffsre" || exit 1; \
	done
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

$(BUILD)/core_sim: $(PKG_SRC) $(CORE_SRC) tb/core/main.cpp
	@mkdir -p $(BUILD)
	verilator --cc --exe --build -j 4 -O2 -Wno-fatal -DXLEN=64 --top-module core --x-assign unique --x-initial unique \
	  --Mdir $(BUILD)/obj_core -o core_sim $(PKG_SRC) $(CORE_SRC) tb/core/main.cpp
	cp $(BUILD)/obj_core/core_sim $@

ifneq ($(filter sim compare,$(MAKECMDGOALS)),)
ifeq ($(PROG),)
$(error set PROG=<name from sw/>, for example: make compare PROG=fib)
endif
endif

sim: $(BUILD)/core_sim $(BUILD)/$(PROG).bin
	$(BUILD)/core_sim $(BUILD)/$(PROG).bin $(BUILD)/$(PROG).core.log \
	  $$($(CROSS)nm $(BUILD)/$(PROG).elf | awk '$$3=="tohost"{print $$1}')

compare: sim
	-$(SPIKE) --isa=rv64im -m0x80000000:0x100000 --log-commits $(BUILD)/$(PROG).elf 2> $(BUILD)/$(PROG).spike.log
	$(PYTHON) scripts/compare.py $(BUILD)/$(PROG).core.log $(BUILD)/$(PROG).spike.log

regress:
	@for p in $(PROGS); do \
	  echo "== compare $$p"; \
	  $(MAKE) --no-print-directory compare PROG=$$p > $(BUILD)/$$p.regress.log 2>&1 || { tail -15 $(BUILD)/$$p.regress.log; exit 1; }; \
	  tail -1 $(BUILD)/$$p.regress.log; \
	done

SEEDS ?= 1 2 3 4 5 6 7 8
fuzz:
	@for n in $(SEEDS); do \
	  echo "== fuzz seed $$n"; \
	  $(MAKE) --no-print-directory compare PROG=fuzz_$$n > $(BUILD)/fuzz_$$n.regress.log 2>&1 || { tail -15 $(BUILD)/fuzz_$$n.regress.log; exit 1; }; \
	  tail -1 $(BUILD)/fuzz_$$n.regress.log; \
	done

area:
	@mkdir -p $(BUILD)
	$(PYTHON) scripts/area.py --xc7 $(if $(COMMIT),--commit $(COMMIT)) --json $(BUILD)/area.json

mutate:
	@test -n "$(M)" || { echo "mutate: set M=<name from scripts/mutants>"; exit 1; }
	$(PYTHON) scripts/mutate.py $(M)

spdx:
	$(PYTHON) scripts/check_spdx.py

clean:
	rm -rf $(BUILD) obj_dir sim_build tb/unit/sim_build tb/unit/__pycache__ .pytest_cache
	find . -path ./.venv -prune -o -name 'dump.vcd' -print -delete
