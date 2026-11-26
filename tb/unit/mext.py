# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Reference model of the M extension for tests, written from the RISC-V specification
on Python integers (not from the structure of the RTL)

    expected(funct3, a, b, word)   -> XLEN-bit result
    iterations(funct3, a, b, word) -> iterations the unit spends (ADR 011), latency = iterations + 1

funct3: 0 MUL, 1 MULH, 2 MULHSU, 3 MULHU, 4 DIV, 5 DIVU, 6 REM, 7 REMU.
word=1 selects the W instructions (RV64): operands are the low 32 bits, the 32-bit result
is sign-extended
"""

import common

NAMES = {0: "MUL", 1: "MULH", 2: "MULHSU", 3: "MULHU", 4: "DIV", 5: "DIVU", 6: "REM", 7: "REMU"}
WORD_FUNCT3 = (0, 4, 5, 6, 7)    # MULW, DIVW, DIVUW, REMW, REMUW


def _trunc_div(n: int, d: int) -> int:
    """Signed division that rounds toward zero (Python's // rounds down)"""
    q = abs(n) // abs(d)
    return q if (n < 0) == (d < 0) else -q


def expected(funct3: int, a: int, b: int, word: int = 0) -> int:
    """
    Result of one M instruction

    Dependencies: common.xlen, common.mask, common.to_signed
    """
    width = 32 if word else common.xlen()
    m = (1 << width) - 1
    a &= m
    b &= m
    sa = common.to_signed(a, width)
    sb = common.to_signed(b, width)
    smin = -(1 << (width - 1))

    if funct3 == 0:
        r = a * b
    elif funct3 == 1:
        r = (sa * sb) >> width
    elif funct3 == 2:
        r = (sa * b) >> width
    elif funct3 == 3:
        r = (a * b) >> width
    elif funct3 == 4:
        if b == 0:
            r = -1
        elif sa == smin and sb == -1:
            r = smin
        else:
            r = _trunc_div(sa, sb)
    elif funct3 == 5:
        r = m if b == 0 else a // b
    elif funct3 == 6:
        if b == 0:
            r = sa
        elif sa == smin and sb == -1:
            r = 0
        else:
            r = sa - _trunc_div(sa, sb) * sb
    else:
        r = a if b == 0 else a % b

    r &= m
    if word:
        r = common.to_signed(r, 32)
    return r & common.mask()


def iterations(funct3: int, a: int, b: int, word: int = 0, mul_bits: int = 1) -> int:
    """
    Iterations of the multiplier/divider for one instruction (the rule of ADR 011, not the RTL):
    XLEN / 2 when the operands fit in half a word, otherwise XLEN.

    Short: MUL (also MULW) with a multiplier whose upper half is zero; DIV, DIVU, REM, REMU (also W)
    with a dividend magnitude below 2^(XLEN/2) and a non-zero divisor. Operands are taken as the unit
    sees them: a W instruction uses the low 32 bits, extended by the signedness of the instruction.
    A multiplication handles mul_bits multiplier bits per iteration (parameter MUL_BITS, ADR 017);
    division always one bit

    Dependencies: common.xlen, common.to_signed
    """
    x = common.xlen()
    h = x // 2
    a_signed = funct3 in (1, 2, 4, 6)
    b_signed = funct3 in (1, 4, 6)

    def operand(v, signed):
        if word:
            v = common.to_signed(v & 0xFFFFFFFF, 32) if signed else v & 0xFFFFFFFF
        else:
            v = common.to_signed(v & common.mask(), x) if signed else v & common.mask()
        return v

    a_val = operand(a, a_signed)
    b_val = operand(b, b_signed)
    if funct3 == 0:
        short = (b_val & common.mask()) >> h == 0
    elif funct3 >= 4:
        short = abs(a_val) < (1 << h) and b_val != 0
    else:
        short = False
    n = h if short else x
    return n if funct3 >= 4 else n // mul_bits
