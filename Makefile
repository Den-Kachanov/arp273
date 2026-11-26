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

.PHONY: help lint struct unit synth sim compare regress fuzz release-check riscv-tests coremark bench area schematics schematics-all floorplan docs-img mutate spdx clean

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
	@echo "make release-check   the release build of the pipeline (DEBUG = 0) behaves like the debug build"
	@echo "make riscv-tests     riscv-tests rv64ui-p and rv64um-p on the pipelined core (git submodule third_party/riscv-tests)"
	@echo "make coremark        CoreMark/MHz on the pipelined core, release build (git submodule third_party/coremark; CM_ITER=10)"
	@echo "make bench           CoreMark, riscv-tests benchmarks, Embench-IoT: cycles (FP ones in soft-float; SOFTFP=0: integer only); BASELINE=<json> compares"
	@echo "make area            area and logic delay of the modules (scripts/area.py); RELEASE=1: core_pipe without debug parts"
	@echo "make schematics      schematics drawn from the RTL (scripts/schematic.py, needs npm --prefix tools ci)"
	@echo "make schematics-all  every module at every level, in parallel (J=8 to limit the jobs)"
	@echo "make floorplan       die floorplan estimate of core_pipe (TOP=core for the reference): block areas, placement, buses"
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
else ifeq ($(CORE),pipe-release)
SIM_BIN := $(BUILD)/core_pipe_rel_sim
else
SIM_BIN := $(BUILD)/core_sim
endif
export MEM_STALL MEM_SEED X_SEED END_ON_STORE

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

# the release build of the pipeline (DEBUG = 0): no commit trace
$(BUILD)/core_pipe_rel_sim: $(PKG_SRC) $(CORE_SRC) tb/core/main.cpp
	@mkdir -p $(BUILD)
	$(VERILATOR_SIM) --top-module core_pipe -GDEBUG=0 -CFLAGS "-DPIPE -DRELEASE" --Mdir $(BUILD)/obj_core_pipe_rel \
	  -o core_pipe_rel_sim $(PKG_SRC) $(CORE_SRC) tb/core/main.cpp
	cp $(BUILD)/obj_core_pipe_rel/core_pipe_rel_sim $@

# the release build must behave like the debug build: same exit code, cycles and instret at the
# store to tohost, for every program and fuzz seed (also with MEM_STALL); the stall and flush
# counters exist only in the debug build and are not compared
release-check: $(BUILD)/core_pipe_sim $(BUILD)/core_pipe_rel_sim
	@for p in $(PROGS) $(addprefix fuzz_,$(SEEDS)); do \
	  $(MAKE) --no-print-directory -s $(BUILD)/$$p.bin >/dev/null || exit 1; \
	  th=$$($(CROSS)nm $(BUILD)/$$p.elf | awk '$$3=="tohost"{print $$1}'); \
	  d=$$(END_ON_STORE=1 $(BUILD)/core_pipe_sim $(BUILD)/$$p.bin /dev/null $$th 2>&1 | grep -E "^(exit code|cycles)" | sed -E 's/ stalls.*//'); \
	  r=$$($(BUILD)/core_pipe_rel_sim $(BUILD)/$$p.bin /dev/null $$th 2>&1 | grep -E "^(exit code|cycles)" | sed -E 's/ stalls.*//'); \
	  if [ "$$d" = "$$r" ]; then echo "  same  $$p: $$(echo $$d | tr '\n' ' ')"; \
	  else echo "  DIFFERENT $$p"; echo "    debug:   $$d"; echo "    release: $$r"; exit 1; fi; \
	done; echo "release-check: OK"

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
	@mkdir -p $(BUILD)
	@for p in $(PROGS); do \
	  echo "== compare $$p"; \
	  $(MAKE) --no-print-directory compare PROG=$$p CORE=$(CORE) > $(BUILD)/$$p.$(CORE).regress.log 2>&1 || { tail -15 $(BUILD)/$$p.$(CORE).regress.log; exit 1; }; \
	  tail -1 $(BUILD)/$$p.$(CORE).regress.log; \
	done

SEEDS ?= 1 2 3 4 5 6 7 8
fuzz:
	@mkdir -p $(BUILD)
	@for n in $(SEEDS); do \
	  echo "== fuzz seed $$n"; \
	  $(MAKE) --no-print-directory compare PROG=fuzz_$$n CORE=$(CORE) > $(BUILD)/fuzz_$$n.$(CORE).regress.log 2>&1 || { tail -15 $(BUILD)/fuzz_$$n.$(CORE).regress.log; exit 1; }; \
	  tail -1 $(BUILD)/fuzz_$$n.$(CORE).regress.log; \
	done

# riscv-tests (git submodule, with its env submodule): self-checking ISA tests; the core takes their
# exceptions itself (TRAP_STOP=0 in the script). MEM_STALL works here too
RISCV_TESTS ?= third_party/riscv-tests
RT_SUITES   ?= rv64ui rv64um
riscv-tests: $(BUILD)/core_pipe_sim
	$(PYTHON) scripts/riscv_tests.py --sim $(BUILD)/core_pipe_sim --src $(RISCV_TESTS) --cross $(CROSS) \
	  --suites $(RT_SUITES) --out $(BUILD)/riscv-tests

# CoreMark (git submodule): its benchmark sources with our port in sw/coremark; one tick = one cycle.
# Runs the release build (no commit log: millions of instructions); CORE_SIM picks another build,
# MEM_STALL works here too
COREMARK  ?= third_party/coremark
CM_ITER   ?= 10
CM_CFLAGS := $(CFLAGS)
CM_SRC    := $(addprefix $(COREMARK)/,core_list_join.c core_main.c core_matrix.c core_state.c core_util.c) \
             sw/coremark/core_portme.c sw/lib/printf.c
CORE_SIM  ?= $(BUILD)/core_pipe_rel_sim

$(BUILD)/coremark.elf: $(CM_SRC) sw/coremark/core_portme.h sw/lib/libc.c sw/crt0.S sw/link.ld
	@mkdir -p $(BUILD)
	$(CC) $(CM_CFLAGS) -Isw/coremark -I$(COREMARK) -DPERFORMANCE_RUN=1 -DITERATIONS=$(CM_ITER) \
	  -DFLAGS_STR='"$(CM_CFLAGS)"' -Wl,--no-warn-rwx-segments -T sw/link.ld sw/crt0.S $(CM_SRC) sw/lib/libc.c -o $@
	$(CROSS)objdump -d $@ > $(BUILD)/coremark.dis

# the ELF depends on CM_ITER: rebuild every time
.PHONY: $(BUILD)/coremark.elf
coremark: $(CORE_SIM) $(BUILD)/coremark.bin
	$(PYTHON) scripts/coremark.py --sim $(CORE_SIM) --elf $(BUILD)/coremark.elf --bin $(BUILD)/coremark.bin --cross $(CROSS)

# all benchmark suites (scripts/bench.py): CoreMark, riscv-tests benchmarks, Embench-IoT 1.0 (git
# submodules); results in build/bench/results.json, BASELINE=<earlier json> prints the speedups
# The floating-point benchmarks use a soft-float library (scripts/softfp.py: compiler-rt and fdlibm
# from pinned release archives, downloaded once); SOFTFP=0 runs only the integer ones
EMBENCH ?= third_party/embench-iot
SOFTFP  ?= 1
SOFTFP_LIB := $(BUILD)/softfp/libsoftfp.a
$(SOFTFP_LIB): scripts/softfp.py sw/softfp/libm_shim.c
	$(PYTHON) scripts/softfp.py --out $(BUILD)/softfp --cross $(CROSS)

bench: $(CORE_SIM) $(if $(filter 1,$(SOFTFP)),$(SOFTFP_LIB))
	$(PYTHON) scripts/bench.py --sim $(CORE_SIM) --cross $(CROSS) --coremark $(COREMARK) \
	  --riscv-tests $(RISCV_TESTS) --embench $(EMBENCH) --cm-iter $(CM_ITER) \
	  $(if $(filter 1,$(SOFTFP)),--softfp $(SOFTFP_LIB)) \
	  --json $(BUILD)/bench/results.json $(if $(BASELINE),--baseline $(BASELINE)) \
	  $(if $(CORE_NAME),--core "$(CORE_NAME)") $(if $(COMMIT),--commit $(COMMIT))

area:
	@mkdir -p $(BUILD)
	$(PYTHON) scripts/area.py --xc7 $(if $(RELEASE),--release) $(if $(COMMIT),--commit $(COMMIT)) --json $(BUILD)/area.json

schematics:
	$(PYTHON) scripts/schematic.py $(if $(M),$(M) --level $(or $(LEVEL),rtl)) $(if $(XLEN),--xlen $(XLEN))

schematics-all:
	$(PYTHON) scripts/schematic.py --all $(if $(J),-j $(J)) $(if $(XLEN),--xlen $(XLEN))

floorplan:
	$(PYTHON) scripts/floorplan.py --top $(or $(TOP),core_pipe) $(if $(J),-j $(J)) $(if $(XLEN),--xlen $(XLEN))

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
