VENV    ?= .venv
PYTHON  ?= $(VENV)/bin/python
PYTEST  ?= $(PYTHON) -m pytest

# Package
PKG_SRC := $(sort $(wildcard rtl/pkg/*.sv))

MOD_SRC := $(shell grep -l '^[[:space:]]*module[[:space:]]' $(sort $(wildcard rtl/core/*.sv rtl/vec/*.sv rtl/gpu/*.sv rtl/soc/*.sv)) 2>/dev/null)

XLENS   := 64 32
VLINT   := verilator --lint-only -Wall

.PHONY: help lint unit synth clean

help:
	@echo "make lint            лінт кожного модуля для XLEN = 64 і 32"
	@echo "make unit            усі тести cocotb (tb/unit)"
	@echo "make unit T=alu      лише tb/unit/test_alu.py"
	@echo "make synth           Yosys: кількість комірок для кожного модуля"
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
	    $(VLINT) -DXLEN=$$x --top-module $$top $(PKG_SRC) $$f || exit 1; \
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
	  yosys -q -p "read_verilog -sv -DXLEN=64 $(PKG_SRC) $$f; synth -top $$top; stat" || exit 1; \
	done

clean:
	rm -rf obj_dir sim_build tb/unit/sim_build tb/unit/__pycache__ .pytest_cache
	find . -path ./.venv -prune -o -name 'dump.vcd' -print -delete
