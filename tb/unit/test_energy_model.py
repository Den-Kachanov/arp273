# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""The energy model of scripts/energy.py on a tiny made-up netlist (ADR 016); no simulation"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import energy  # noqa: E402

# bits: 1 = clk_i (drives the 2 flip-flops), 2 = rst_ni, 3 = a data net with fanout 3
BIT_OF = {"clk_i": 1, "rst_ni": 2, "d[0]": 3, "d_alias[0]": 3}
LOAD = {1: 2, 2: 2, 3: 3}


def test_clock_counted_once():
    """the clock net is the clock term only; it must not also appear in the logic sum"""
    cycles = 100
    counts = {"clk_i": 2 * cycles, "rst_ni": 1, "d[0]": 10, "d_alias[0]": 10}
    e = energy.energy(cycles, counts, ffs=2, load=LOAD, bit_of=BIT_OF)
    assert e["toggle_load"] == 10 * (3 + 1), "only the data net; an alias counts once"
    assert e["clock"] == energy.CLOCK_WEIGHT * 2 * cycles


def test_partial_coverage_is_caught():
    """the check fails when the coverage file misses most net bits (as without --coverage-underscore)"""
    names = {f"n{i}": i for i in range(100)} | {"perf_cycles_o[0]": 100, "perf_cycles_o[1]": 101}
    full = dict.fromkeys(names, 1) | {"perf_cycles_o[0]": 50, "perf_cycles_o[1]": 25}
    energy.check_coverage("full", 50, full, names)
    partial = {"perf_cycles_o[0]": 50, "perf_cycles_o[1]": 25, "n1": 3}
    with pytest.raises(SystemExit):
        energy.check_coverage("partial", 50, partial, names)


def test_unknown_names_are_caught():
    """names in the coverage file that the netlist does not know mean the two netlists differ"""
    names = {f"n{i}": i for i in range(100)} | {"perf_cycles_o[0]": 100, "perf_cycles_o[1]": 101}
    counts = dict.fromkeys(names, 1) | {"perf_cycles_o[0]": 50, "perf_cycles_o[1]": 25}
    counts |= {f"stray{i}": 1 for i in range(20)}
    with pytest.raises(SystemExit):
        energy.check_coverage("stray", 50, counts, names)


def test_verilator_names_decoded():
    """Verilator writes [ and ] inside a name as __05b and __05d; the netlist has the plain name"""
    assert energy.coverage_name("fq_inst_q__05b0__05d[7]") == "fq_inst_q[0][7]"
    assert energy.coverage_name("u_regfile.wdata_i[9]") == "u_regfile.wdata_i[9]"


def test_constant_bits_are_known_but_free():
    """a net bit tied to 0 or 1 is in the netlist (bit_of maps it to None): known, and costs nothing"""
    bit_of = {"clk_i": 1, "d[0]": 3, "addr[0]": None}
    e = energy.energy(10, {"clk_i": 20, "d[0]": 4, "addr[0]": 0}, ffs=1, load={1: 1, 3: 1}, bit_of=bit_of)
    assert e["toggle_load"] == 4 * 2


def test_flip_flop_regs_are_aliases():
    """write_verilog gives a flip-flop its own reg and assigns it to the netlist's net: the same bit"""
    text = ("  reg n68070;\n  assign mem_cause_q[1] = n68070;\n  assign x = y;\n"
            "  assign \\u_muldiv.div_hi [0] = n69138;\n")
    bit_of = {"mem_cause_q[1]": 7, "x": 8, "y": 9, "u_muldiv.div_hi[0]": 10}
    energy.add_reg_aliases(text, bit_of)
    assert bit_of["n68070"] == 7 and bit_of["x"] == 8
    assert bit_of["n69138"] == 10, "an escaped name (backslash, space before the index)"
