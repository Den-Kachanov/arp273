# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Shared helpers for module tests: Verilator build, cocotb run, small utilities

A test file (tb/unit/test_<module>.py) has two parts:

1. cocotb tests (@cocotb.test) that run inside the simulator.
   They can call xlen(), mask(), to_signed(), edge_values().
2. A pytest function that builds the module and runs those tests per XLEN:

       @pytest.mark.parametrize("xlen", common.XLENS)
       def test_alu_runner(xlen):
           common.run("alu", __file__, xlen)
"""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
XLENS = (64, 32)


# Part 1: called from cocotb tests (inside the simulator)

def xlen() -> int:
    """
    XLEN of the current simulation

    Dependencies: env XLEN (set by run)
    """
    return int(os.environ["XLEN"])


def mask(width: int | None = None) -> int:
    """
    All-ones mask, 2**width - 1 (default width: XLEN)

    Dependencies: xlen
    """
    return (1 << (width if width is not None else xlen())) - 1


def to_signed(value: int, width: int | None = None) -> int:
    """
    Read an unsigned value as two's complement (default width: XLEN)

    Dependencies: xlen
    """
    w = width if width is not None else xlen()
    value &= (1 << w) - 1
    return value - (1 << w) if value >> (w - 1) else value


def edge_values(width: int | None = None) -> list[int]:
    """
    Edge cases: 0, 1, -1, largest positive, smallest negative (default width: XLEN)

    Dependencies: xlen
    """
    w = width if width is not None else xlen()
    return [0, 1, (1 << w) - 1, (1 << (w - 1)) - 1, 1 << (w - 1)]


# Part 2: called from pytest (outside the simulator)

def _find_source(top: str) -> Path:
    """
    Find rtl/**/<top>.sv

    Dependencies: REPO
    """
    found = sorted((REPO / "rtl").glob(f"**/{top}.sv"))
    if not found:
        raise FileNotFoundError(f"rtl/**/{top}.sv not found")
    return found[0]


def run(top: str, test_file: str, xlen_value: int, extra_sources: list[str] | None = None,
        build_args: list[str] | None = None, extra_env: dict[str, str] | None = None) -> None:
    """
    Build module `top` for one XLEN and run the cocotb tests from `test_file`

    build_args may set parameters (-GNAME=VALUE); each set of them gets its own build directory.
    extra_env is passed to the tests (for example the parameter values they need to know)

    Waves (dump.vcd in the build directory) are enabled with WAVES=1.
    A failed cocotb test becomes a pytest failure.

    Registers start with random values, not with zeros (Verilator --x-initial unique,
    +verilator+rand+reset+2): a chip or an FPGA does not clear flip-flops that have no reset, so a
    design must not depend on it. The seed is fixed (env X_SEED, default 1), so a failure repeats

    Dependencies: _find_source, REPO
    """
    from cocotb_tools.check_results import get_results
    from cocotb_tools.runner import get_runner

    test_path = Path(test_file).resolve()
    params = "".join("_" + a[2:].replace("=", "") for a in (build_args or []) if a.startswith("-G"))
    build_dir = test_path.parent / "sim_build" / f"{top}_xlen{xlen_value}{params}"
    waves = os.environ.get("WAVES") == "1"

    sources = sorted((REPO / "rtl" / "pkg").glob("*.sv"))
    sources.append(_find_source(top))
    sources += [Path(s) for s in (extra_sources or [])]

    runner = get_runner("verilator")
    runner.build(
        sources=sources,
        hdl_toplevel=top,
        defines={"XLEN": xlen_value},
        build_args=["--x-assign", "unique", "--x-initial", "unique"] + list(build_args or []),
        build_dir=build_dir,
        always=True,
        waves=waves,
    )
    results = runner.test(
        hdl_toplevel=top,
        test_module=test_path.stem,
        test_dir=build_dir,
        build_dir=build_dir,
        extra_env={"XLEN": str(xlen_value), "PYTHONPATH": str(test_path.parent)} | (extra_env or {}),
        plusargs=["+verilator+rand+reset+2", f"+verilator+seed+{os.environ.get('X_SEED', '1')}"],
        waves=waves,
    )
    total, failed = get_results(results)
    assert total > 0, f"{top}: no cocotb tests found in {test_path.name}"
    assert failed == 0, f"{top} (XLEN={xlen_value}): {failed} of {total} cocotb tests failed"
