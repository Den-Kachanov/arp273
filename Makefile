VENV    ?= .venv
PYTHON  ?= $(VENV)/bin/python
PYTEST  ?= $(PYTHON) -m pytest

# Package
PKG_SRC := $(sort $(wildcard rtl/pkg/*.sv))

MOD_SRC := $(shell grep -l '^[[:space:]]*module[[:space:]]' $(sort $(wildcard rtl/core/*.sv rtl/vec/*.sv rtl/gpu/*.sv rtl/soc/*.sv)) 2>/dev/null)

XLENS   := 64 32
VLINT   := verilator --lint-only -Wall

.PHONY: help lint unit synth sim compare regress clean

help:
	@echo "make lint            лінт кожного модуля для XLEN = 64 і 32"
	@echo "make unit            усі тести cocotb (tb/unit)"
	@echo "make unit T=alu      лише tb/unit/test_alu.py"
	@echo "make synth           Yosys: кількість комірок для кожного модуля"
	@echo "make sim PROG=fib    зібрати sw/fib.c і прогнати на ядрі (журнал комітів і лічильники)"
	@echo "make compare PROG=fib  звірити журнал ядра зі Spike"
	@echo "make regress           звірити зі Spike усі програми з sw/"
	@echo "make clean           прибрати артефакти збірки"

lint:
	@if [ -z "$(MOD_SRC)" ]; then echo "lint: модулів ще немає, перевіряю лише package"; fi
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

unit:
	@if ls tb/unit/test_*.py >/dev/null 2>&1; then \
	  $(PYTEST) -v $(if $(T),tb/unit/test_$(T).py,tb/unit); \
	else echo "unit: у tb/unit ще немає тестів"; fi

synth:
	@for f in $(MOD_SRC); do \
	  top=$$(basename $$f .sv); \
	  echo "== synth $$top"; \
	  yosys -q -p "read_verilog -sv -DXLEN=64 $(PKG_SRC) $(MOD_SRC); synth -top $$top; stat" || exit 1; \
	done

# Software and simulation (RV64I: one ELF runs on Spike and on the core)
BUILD    := build
CC       := riscv64-elf-gcc
CFLAGS   := -march=rv64i_zicsr -mabi=lp64 -mcmodel=medany -O2 -ffreestanding -nostdlib -nostartfiles
SPIKE    := spike
PROGS    := $(basename $(notdir $(wildcard sw/*.c)))
CORE_SRC := $(sort $(wildcard rtl/core/*.sv))

# keep the ELF: Spike and nm need it after the .bin is built
.PRECIOUS: $(BUILD)/%.elf

$(BUILD)/%.elf: sw/%.c sw/crt0.S sw/link.ld
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -Wl,--no-warn-rwx-segments -T sw/link.ld sw/crt0.S $< -o $@
	riscv64-elf-objdump -d $@ > $(BUILD)/$*.dis

$(BUILD)/%.bin: $(BUILD)/%.elf
	riscv64-elf-objcopy -O binary $< $@

$(BUILD)/core_sim: $(PKG_SRC) $(CORE_SRC) tb/core/main.cpp
	@mkdir -p $(BUILD)
	verilator --cc --exe --build -j 4 -O2 -Wno-fatal -DXLEN=64 --top-module core \
	  --Mdir $(BUILD)/obj_core -o core_sim $(PKG_SRC) $(CORE_SRC) tb/core/main.cpp
	cp $(BUILD)/obj_core/core_sim $@

sim: $(BUILD)/core_sim $(BUILD)/$(PROG).bin
	@test -n "$(PROG)" || { echo "sim: задайте PROG=<ім'я з sw/>"; exit 1; }
	$(BUILD)/core_sim $(BUILD)/$(PROG).bin $(BUILD)/$(PROG).core.log \
	  $$(riscv64-elf-nm $(BUILD)/$(PROG).elf | awk '$$3=="tohost"{print $$1}')

compare: sim
	-$(SPIKE) --isa=rv64i -m0x80000000:0x100000 --log-commits $(BUILD)/$(PROG).elf 2> $(BUILD)/$(PROG).spike.log
	$(PYTHON) scripts/compare.py $(BUILD)/$(PROG).core.log $(BUILD)/$(PROG).spike.log

regress:
	@for p in $(PROGS); do \
	  echo "== compare $$p"; \
	  $(MAKE) --no-print-directory compare PROG=$$p > $(BUILD)/$$p.regress.log 2>&1 || { tail -15 $(BUILD)/$$p.regress.log; exit 1; }; \
	  tail -1 $(BUILD)/$$p.regress.log; \
	done

clean:
	rm -rf $(BUILD) obj_dir sim_build tb/unit/sim_build tb/unit/__pycache__ .pytest_cache
	find . -path ./.venv -prune -o -name 'dump.vcd' -print -delete
