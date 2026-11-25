# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
import random

import cocotb
import common
import pytest
from asm import OPC, b_type, i_type, j_type, r_type, s_type, u_type
from cocotb.triggers import Timer

# Codes of the enums in riscv_pkg.sv: they must match
ALU = {
    "ADD": 0b0000, "SLL": 0b0001, "SLT": 0b0010, "SLTU": 0b0011, "XOR": 0b0100,
    "SRL": 0b0101, "OR": 0b0110, "AND": 0b0111, "SUB": 0b1000, "SRA": 0b1101,
}
IMM = {"I": 0b000, "S": 0b001, "B": 0b010, "U": 0b011, "J": 0b100}
A_RS1, A_PC, A_ZERO = 0b00, 0b01, 0b10
B_RS2, B_IMM = 0, 1
WB_ALU, WB_MEM, WB_PC4, WB_MD = 0b00, 0b01, 0b10, 0b11

SIDE_EFFECTS = ("reg_we", "mem_re", "mem_we", "branch", "jal", "jalr", "muldiv")


# Expected outputs. Only the listed signals are checked: a missing key means "don't care"

def alu_instr(alu, imm=None, word=0):
    """Expected signals of an instruction that writes the ALU result to rd"""
    exp = {"alu_op": ALU[alu], "word": word, "reg_we": 1, "mem_re": 0, "mem_we": 0,
           "branch": 0, "jal": 0, "jalr": 0, "wb_sel": WB_ALU, "a_sel": A_RS1, "illegal": 0}
    if imm is None:
        exp["b_sel"] = B_RS2
    else:
        exp["b_sel"] = B_IMM
        exp["imm_sel"] = IMM[imm]
    return exp


def build_instructions(r, xlen):
    """
    List of (name, instruction word generator, expected signals, rv64_only)

    A generator takes no arguments and returns a word with random registers and immediates,
    so the decoder is checked to ignore rd, rs1, rs2 and immediate bits

    Dependencies: common.xlen
    """
    reg = lambda: r.randrange(32)
    imm12 = lambda: r.randrange(-2048, 2048)
    shamt = lambda: r.randrange(xlen)
    out = []

    # R-type and OP-32
    r_ops = {"ADD": (0, 0, "ADD"), "SUB": (0b0100000, 0, "SUB"), "SLL": (0, 1, "SLL"),
             "SLT": (0, 2, "SLT"), "SLTU": (0, 3, "SLTU"), "XOR": (0, 4, "XOR"),
             "SRL": (0, 5, "SRL"), "SRA": (0b0100000, 5, "SRA"), "OR": (0, 6, "OR"),
             "AND": (0, 7, "AND")}
    for name, (f7, f3, alu) in r_ops.items():
        out.append((name, lambda f7=f7, f3=f3: r_type(f7, reg(), reg(), f3, reg(), OPC["OP"]),
                    alu_instr(alu), False))
    for name in ("ADD", "SUB", "SLL", "SRL", "SRA"):
        f7, f3, alu = r_ops[name]
        out.append((name + "W", lambda f7=f7, f3=f3: r_type(f7, reg(), reg(), f3, reg(), OPC["OP_32"]),
                    alu_instr(alu, word=1), True))

    # M extension: funct7 = 1; the result comes from the multiplier/divider, not from the ALU
    m_exp = {"alu_op": ALU["ADD"], "reg_we": 1, "mem_re": 0, "mem_we": 0, "branch": 0, "jal": 0,
             "jalr": 0, "wb_sel": WB_MD, "a_sel": A_RS1, "b_sel": B_RS2, "muldiv": 1, "illegal": 0}
    m_ops = {"MUL": 0, "MULH": 1, "MULHSU": 2, "MULHU": 3, "DIV": 4, "DIVU": 5, "REM": 6, "REMU": 7}
    for name, f3 in m_ops.items():
        out.append((name, lambda f3=f3: r_type(1, reg(), reg(), f3, reg(), OPC["OP"]),
                    dict(m_exp, word=0, funct3=f3), False))
    for name in ("MUL", "DIV", "DIVU", "REM", "REMU"):
        f3 = m_ops[name]
        out.append((name + "W", lambda f3=f3: r_type(1, reg(), reg(), f3, reg(), OPC["OP_32"]),
                    dict(m_exp, word=1, funct3=f3), True))

    # I-type ALU: inst[30] belongs to the immediate, so the ALU op must not become SUB
    i_ops = {"ADDI": (0, "ADD"), "SLTI": (2, "SLT"), "SLTIU": (3, "SLTU"), "XORI": (4, "XOR"),
             "ORI": (6, "OR"), "ANDI": (7, "AND")}
    for name, (f3, alu) in i_ops.items():
        out.append((name, lambda f3=f3: i_type(imm12(), reg(), f3, reg(), OPC["OP_IMM"]),
                    alu_instr(alu, imm="I"), False))
    shifts = {"SLLI": (0b000000, 1, "SLL"), "SRLI": (0b000000, 5, "SRL"), "SRAI": (0b010000, 5, "SRA")}
    for name, (f6, f3, alu) in shifts.items():
        out.append((name, lambda f6=f6, f3=f3: i_type((f6 << 6) | shamt(), reg(), f3, reg(), OPC["OP_IMM"]),
                    alu_instr(alu, imm="I"), False))
    out.append(("ADDIW", lambda: i_type(imm12(), reg(), 0, reg(), OPC["OP_IMM_32"]),
                alu_instr("ADD", imm="I", word=1), True))
    w_shifts = {"SLLIW": (0b0000000, 1, "SLL"), "SRLIW": (0b0000000, 5, "SRL"), "SRAIW": (0b0100000, 5, "SRA")}
    for name, (f7, f3, alu) in w_shifts.items():
        out.append((name, lambda f7=f7, f3=f3: r_type(f7, reg(), reg(), f3, reg(), OPC["OP_IMM_32"]),
                    alu_instr(alu, imm="I", word=1), True))

    # LUI, AUIPC
    lui = {"alu_op": ALU["ADD"], "word": 0, "reg_we": 1, "mem_re": 0, "mem_we": 0, "branch": 0,
           "jal": 0, "jalr": 0, "wb_sel": WB_ALU, "imm_sel": IMM["U"], "b_sel": B_IMM, "illegal": 0}
    out.append(("LUI", lambda: u_type(r.getrandbits(20), reg(), OPC["LUI"]), dict(lui, a_sel=A_ZERO), False))
    out.append(("AUIPC", lambda: u_type(r.getrandbits(20), reg(), OPC["AUIPC"]), dict(lui, a_sel=A_PC), False))

    # loads and stores: address = rs1 + imm
    mem = {"alu_op": ALU["ADD"], "word": 0, "a_sel": A_RS1, "b_sel": B_IMM, "branch": 0,
           "jal": 0, "jalr": 0, "illegal": 0}
    loads = {"LB": (0, False), "LH": (1, False), "LW": (2, False), "LD": (3, True),
             "LBU": (4, False), "LHU": (5, False), "LWU": (6, True)}
    for name, (f3, rv64) in loads.items():
        out.append((name, lambda f3=f3: i_type(imm12(), reg(), f3, reg(), OPC["LOAD"]),
                    dict(mem, reg_we=1, mem_re=1, mem_we=0, wb_sel=WB_MEM, imm_sel=IMM["I"], funct3=f3), rv64))
    stores = {"SB": (0, False), "SH": (1, False), "SW": (2, False), "SD": (3, True)}
    for name, (f3, rv64) in stores.items():
        out.append((name, lambda f3=f3: s_type(imm12(), reg(), reg(), f3, OPC["STORE"]),
                    dict(mem, reg_we=0, mem_re=0, mem_we=1, imm_sel=IMM["S"], funct3=f3), rv64))

    # branches: the ALU computes pc + imm
    br = {"alu_op": ALU["ADD"], "word": 0, "a_sel": A_PC, "b_sel": B_IMM, "imm_sel": IMM["B"],
          "reg_we": 0, "mem_re": 0, "mem_we": 0, "branch": 1, "jal": 0, "jalr": 0, "illegal": 0}
    for name, f3 in {"BEQ": 0, "BNE": 1, "BLT": 4, "BGE": 5, "BLTU": 6, "BGEU": 7}.items():
        out.append((name, lambda f3=f3: b_type(r.randrange(-4096, 4096) & ~1, reg(), reg(), f3, OPC["BRANCH"]),
                    dict(br, funct3=f3), False))

    # jumps: rd = pc + 4
    jmp = {"alu_op": ALU["ADD"], "word": 0, "b_sel": B_IMM, "reg_we": 1, "mem_re": 0, "mem_we": 0,
           "branch": 0, "wb_sel": WB_PC4, "illegal": 0}
    out.append(("JAL", lambda: j_type(r.randrange(-(1 << 20), 1 << 20) & ~1, reg(), OPC["JAL"]),
                dict(jmp, a_sel=A_PC, imm_sel=IMM["J"], jal=1, jalr=0), False))
    out.append(("JALR", lambda: i_type(imm12(), reg(), 0, reg(), OPC["JALR"]),
                dict(jmp, a_sel=A_RS1, imm_sel=IMM["I"], jal=0, jalr=1), False))

    # FENCE: legal, does nothing
    out.append(("FENCE", lambda: i_type(r.getrandbits(12), reg(), 0, reg(), OPC["MISC_MEM"]),
                {"reg_we": 0, "mem_re": 0, "mem_we": 0, "branch": 0, "jal": 0, "jalr": 0, "illegal": 0}, False))
    return out


async def apply(dut, inst):
    """Drive the instruction, await, return all outputs as a dict"""
    dut.inst_i.value = inst
    await Timer(1, "ns")
    return {
        "alu_op": int(dut.alu_op_o.value), "word": int(dut.word_o.value),
        "imm_sel": int(dut.imm_sel_o.value), "a_sel": int(dut.a_sel_o.value),
        "b_sel": int(dut.b_sel_o.value), "wb_sel": int(dut.wb_sel_o.value),
        "reg_we": int(dut.reg_we_o.value), "mem_re": int(dut.mem_re_o.value),
        "mem_we": int(dut.mem_we_o.value), "branch": int(dut.branch_o.value),
        "jal": int(dut.jal_o.value), "jalr": int(dut.jalr_o.value),
        "muldiv": int(dut.muldiv_o.value),
        "funct3": int(dut.funct3_o.value), "illegal": int(dut.illegal_o.value),
    }


def check_illegal(name, inst, got):
    """An illegal instruction must be flagged and have no side effects"""
    assert got["illegal"] == 1, f"{name} {inst:#010x} must be illegal"
    for key in SIDE_EFFECTS:
        assert got[key] == 0, f"{name} {inst:#010x} is illegal but {key} = 1"


@cocotb.test()
async def test_all_instructions(dut):
    """Every RV64I instruction with random registers and immediates (RV64-only ones are illegal at XLEN=32)"""
    xlen = common.xlen()
    r = random.Random(1)
    for name, make, expected, rv64_only in build_instructions(r, xlen):
        for _ in range(20):
            inst = make()
            got = await apply(dut, inst)
            if rv64_only and xlen == 32:
                check_illegal(name, inst, got)
                continue
            expected.setdefault("muldiv", 0)
            for key, value in expected.items():
                assert got[key] == value, f"{name} {inst:#010x}: {key} expected {value:#x}, got {got[key]:#x}"


@cocotb.test()
async def test_op_imm_does_not_become_sub(dut):
    """inst[30] is an immediate bit in OP-IMM: ADDI with a negative immediate must stay ADD"""
    for f3, alu in ((0, "ADD"), (2, "SLT"), (3, "SLTU"), (4, "XOR"), (6, "OR"), (7, "AND")):
        for imm in (-1, -2048, 0x400, 0x7FF):
            inst = i_type(imm, 1, f3, 2, OPC["OP_IMM"])
            got = await apply(dut, inst)
            assert got["illegal"] == 0
            assert got["alu_op"] == ALU[alu], f"{alu}I with imm {imm}: alu_op {got['alu_op']:#06b}"


@cocotb.test()
async def test_illegal_encodings(dut):
    """Reserved and unsupported encodings are flagged and have no side effects"""
    xlen = common.xlen()
    cases = {
        "all zeros": 0x00000000,
        "all ones": 0xFFFFFFFF,
        "OP with funct7 = 0000010": r_type(0b0000010, 3, 2, 0, 1, OPC["OP"]),
        "OP with funct7 = 1000001": r_type(0b1000001, 3, 2, 0, 1, OPC["OP"]),
        "OP with funct7 = 0100001 (SUB and M mixed)": r_type(0b0100001, 3, 2, 0, 1, OPC["OP"]),
        "OP-32 funct7 = 1 with funct3 = 1 (no MULHW-like)": r_type(1, 3, 2, 1, 1, OPC["OP_32"]),
        "OP-32 funct7 = 1 with funct3 = 2": r_type(1, 3, 2, 2, 1, OPC["OP_32"]),
        "OP-32 funct7 = 1 with funct3 = 3": r_type(1, 3, 2, 3, 1, OPC["OP_32"]),
        "SLLIW with funct7 = 1 (not an M instruction)": r_type(1, 3, 2, 1, 1, OPC["OP_IMM_32"]),
        "SLL with funct7 = 0100000": r_type(0b0100000, 3, 2, 1, 1, OPC["OP"]),
        "ADD with funct7 = 0100001": r_type(0b0100001, 3, 2, 0, 1, OPC["OP"]),
        "SLLI with funct6 != 0": i_type((0b000001 << 6) | 1, 2, 1, 1, OPC["OP_IMM"]),
        "SRLI with funct6 = 100000": i_type((0b100000 << 6) | 1, 2, 5, 1, OPC["OP_IMM"]),
        "LOAD funct3 = 7": i_type(0, 2, 7, 1, OPC["LOAD"]),
        "STORE funct3 = 4": s_type(0, 3, 2, 4, OPC["STORE"]),
        "BRANCH funct3 = 2": b_type(8, 3, 2, 2, OPC["BRANCH"]),
        "BRANCH funct3 = 3": b_type(8, 3, 2, 3, OPC["BRANCH"]),
        "JALR funct3 = 1": i_type(0, 2, 1, 1, OPC["JALR"]),
        "FENCE.I": i_type(0, 0, 1, 0, OPC["MISC_MEM"]),
        "ECALL (not yet supported)": 0x00000073,
        "EBREAK (not yet supported)": 0x00100073,
        "CSRRW (not yet supported)": i_type(0x300, 2, 1, 1, OPC["SYSTEM"]),
        "SLLI with shamt[5] = 1 is RV64 only" if xlen == 32 else "SLTW does not exist":
            i_type((1 << 5) | 1, 2, 1, 1, OPC["OP_IMM"]) if xlen == 32
            else r_type(0, 3, 2, 2, 1, OPC["OP_32"]),
        "OP-IMM-32 funct3 = 1 with funct7 = 0100000": r_type(0b0100000, 3, 2, 1, 1, OPC["OP_IMM_32"]),
        "OP-32 funct3 = 4": r_type(0, 3, 2, 4, 1, OPC["OP_32"]),
    }
    for name, inst in cases.items():
        check_illegal(name, inst, await apply(dut, inst))


@cocotb.test()
async def test_random_words_invariants(dut):
    """On random words: illegal means no side effects; word only for ADD, SUB, SLL, SRL, SRA (M uses ADD); no two actions at once"""
    r = random.Random(2)
    word_ops = {ALU["ADD"], ALU["SUB"], ALU["SLL"], ALU["SRL"], ALU["SRA"]}
    for _ in range(5000):
        inst = r.getrandbits(32)
        got = await apply(dut, inst)
        if got["illegal"]:
            check_illegal("random", inst, got)
            continue
        if got["word"]:
            assert got["alu_op"] in word_ops, f"{inst:#010x}: word with alu_op {got['alu_op']:#06b}"
        assert got["mem_re"] + got["mem_we"] + got["branch"] + got["jal"] + got["jalr"] <= 1, \
            f"{inst:#010x}: several actions at once"
        if got["mem_re"]:
            assert got["reg_we"] == 1 and got["wb_sel"] == WB_MEM, f"{inst:#010x}: bad load"
        # the multiplier/divider is selected by muldiv and by nothing else
        assert (got["wb_sel"] == WB_MD) == bool(got["muldiv"]), f"{inst:#010x}: wb_sel and muldiv disagree"
        if got["muldiv"]:
            assert got["reg_we"] == 1 and got["a_sel"] == A_RS1 and got["b_sel"] == B_RS2, f"{inst:#010x}: bad M op"
            assert got["mem_re"] + got["mem_we"] + got["branch"] + got["jal"] + got["jalr"] == 0
            assert (inst >> 25) == 1, f"{inst:#010x}: M op needs funct7 = 1"


@pytest.mark.parametrize("xlen", common.XLENS)
def test_decoder_runner(xlen):
    """Build the decoder for this XLEN and run the cocotb tests"""
    common.run("decoder", __file__, xlen)
