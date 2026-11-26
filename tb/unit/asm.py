# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Tiny assembler for tests: instruction encoders (formats from the RISC-V specification)
and mnemonic helpers that return a 32-bit instruction word
"""

OPC = {
    "OP": 0b0110011, "OP_IMM": 0b0010011, "OP_32": 0b0111011, "OP_IMM_32": 0b0011011,
    "LUI": 0b0110111, "AUIPC": 0b0010111, "LOAD": 0b0000011, "STORE": 0b0100011,
    "BRANCH": 0b1100011, "JAL": 0b1101111, "JALR": 0b1100111, "MISC_MEM": 0b0001111,
    "SYSTEM": 0b1110011, "AMO": 0b0101111,
}


# Formats

def r_type(f7, rs2, rs1, f3, rd, opc):
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | opc


def i_type(imm, rs1, f3, rd, opc):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | opc


def s_type(imm, rs2, rs1, f3, opc):
    return (((imm >> 5) & 0x7F) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | ((imm & 0x1F) << 7) | opc


def b_type(imm, rs2, rs1, f3, opc):
    return (((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25) | (rs2 << 20) | (rs1 << 15) \
        | (f3 << 12) | (((imm >> 1) & 0xF) << 8) | (((imm >> 11) & 1) << 7) | opc


def u_type(imm20, rd, opc):
    return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | opc


def j_type(imm, rd, opc):
    return (((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21) | (((imm >> 11) & 1) << 20) \
        | (((imm >> 12) & 0xFF) << 12) | (rd << 7) | opc


# Mnemonics (rd, rs1, rs2, imm, offset in bytes)

def _op(f7, f3, opc):
    return lambda rd, rs1, rs2: r_type(f7, rs2, rs1, f3, rd, OPC[opc])


def _op_imm(f3, opc="OP_IMM"):
    return lambda rd, rs1, imm: i_type(imm, rs1, f3, rd, OPC[opc])


def _load(f3):
    return lambda rd, imm, rs1: i_type(imm, rs1, f3, rd, OPC["LOAD"])


def _store(f3):
    return lambda rs2, imm, rs1: s_type(imm, rs2, rs1, f3, OPC["STORE"])


def _branch(f3):
    return lambda rs1, rs2, off: b_type(off, rs2, rs1, f3, OPC["BRANCH"])


add, sub, sll, slt, sltu = _op(0, 0, "OP"), _op(0b0100000, 0, "OP"), _op(0, 1, "OP"), _op(0, 2, "OP"), _op(0, 3, "OP")
xor, srl, sra, or_, and_ = _op(0, 4, "OP"), _op(0, 5, "OP"), _op(0b0100000, 5, "OP"), _op(0, 6, "OP"), _op(0, 7, "OP")
addw, subw = _op(0, 0, "OP_32"), _op(0b0100000, 0, "OP_32")

# M extension: funct7 = 1
mul, mulh, mulhsu, mulhu = _op(1, 0, "OP"), _op(1, 1, "OP"), _op(1, 2, "OP"), _op(1, 3, "OP")
div, divu, rem, remu = _op(1, 4, "OP"), _op(1, 5, "OP"), _op(1, 6, "OP"), _op(1, 7, "OP")
mulw, divw, divuw, remw, remuw = (_op(1, 0, "OP_32"), _op(1, 4, "OP_32"), _op(1, 5, "OP_32"),
                                  _op(1, 6, "OP_32"), _op(1, 7, "OP_32"))

addi, slti, sltiu, xori, ori, andi = (_op_imm(0), _op_imm(2), _op_imm(3), _op_imm(4), _op_imm(6), _op_imm(7))
addiw = _op_imm(0, "OP_IMM_32")


def slli(rd, rs1, shamt):
    return i_type(shamt, rs1, 1, rd, OPC["OP_IMM"])


def srli(rd, rs1, shamt):
    return i_type(shamt, rs1, 5, rd, OPC["OP_IMM"])


def srai(rd, rs1, shamt):
    return i_type((0b010000 << 6) | shamt, rs1, 5, rd, OPC["OP_IMM"])


lb, lh, lw, ld, lbu, lhu, lwu = (_load(0), _load(1), _load(2), _load(3), _load(4), _load(5), _load(6))
sb, sh, sw, sd = _store(0), _store(1), _store(2), _store(3)
beq, bne, blt, bge, bltu, bgeu = (_branch(0), _branch(1), _branch(4), _branch(5), _branch(6), _branch(7))


def lui(rd, imm20):
    return u_type(imm20, rd, OPC["LUI"])


def auipc(rd, imm20):
    return u_type(imm20, rd, OPC["AUIPC"])


def jal(rd, off):
    return j_type(off, rd, OPC["JAL"])


def jalr(rd, imm, rs1):
    return i_type(imm, rs1, 0, rd, OPC["JALR"])


# A extension: rd, rs2 (the operand or the SC data), rs1 (the address, no offset); aq and rl are bits 26:25
AMO_FUNCT5 = {"add": 0b00000, "swap": 0b00001, "lr": 0b00010, "sc": 0b00011, "xor": 0b00100, "or": 0b01000,
              "and": 0b01100, "min": 0b10000, "max": 0b10100, "minu": 0b11000, "maxu": 0b11100}


def amo(op, width, rd, rs2, rs1, aq=0, rl=0):
    """AMO<op>.W / .D, LR, SC (width "w" or "d"; for LR rs2 must be 0)"""
    f7 = (AMO_FUNCT5[op] << 2) | (aq << 1) | rl
    return r_type(f7, rs2, rs1, {"w": 2, "d": 3}[width], rd, OPC["AMO"])


def lr(width, rd, rs1):
    return amo("lr", width, rd, 0, rs1)


def sc(width, rd, rs2, rs1):
    return amo("sc", width, rd, rs2, rs1)


ECALL = 0x00000073
EBREAK = 0x00100073
MRET = 0x30200073
WFI = 0x10500073
FENCE_I = 0x0000100F
NOP = 0x00000013

# CSR numbers used by the tests (privileged specification)
CSR = {"mstatus": 0x300, "misa": 0x301, "mie": 0x304, "mtvec": 0x305, "mscratch": 0x340, "mepc": 0x341,
       "mcause": 0x342, "mtval": 0x343, "mip": 0x344, "mcycle": 0xB00, "minstret": 0xB02,
       "cycle": 0xC00, "instret": 0xC02, "mvendorid": 0xF11, "mhartid": 0xF14, "satp": 0x180}


def _csr(f3):
    """CSR instruction: the register forms take rs1, the immediate forms a 5-bit uimm in its place"""
    return lambda rd, csr, rs1_or_uimm: i_type(CSR.get(csr, csr) if isinstance(csr, str) else csr,
                                              rs1_or_uimm, f3, rd, OPC["SYSTEM"])


csrrw, csrrs, csrrc, csrrwi, csrrsi, csrrci = _csr(1), _csr(2), _csr(3), _csr(5), _csr(6), _csr(7)


def csrr(rd, csr):
    return csrrs(rd, csr, 0)


def csrw(csr, rs1):
    return csrrw(0, csr, rs1)
