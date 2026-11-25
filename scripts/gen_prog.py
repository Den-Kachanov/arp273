# SPDX-License-Identifier: CERN-OHL-S-2.0
"""
Random RV64IM program generator for comparison with Spike

Usage: gen_prog.py --seed N [--n 400] -o prog.S

The same seed always gives the same program. The program is an assembly `main` that crt0 calls;
it returns 0. Registers: x5..x28 are random operands and results; x29 is a loop counter, x30 points
to a 256-byte data buffer, x31 holds jump targets; ra, sp, gp, tp are never written, so the return
to crt0 works. Control flow is safe by construction: branches and jumps go forward to a label,
loops count down a register that the body does not write, loads and stores are aligned and stay
inside the buffer. No CSR, ECALL or FENCE.I, which the core does not support yet

What it exercises: every RV64IM computation instruction (also W forms), edge-valued operands
(0, 1, -1, MIN, MAX, 32-bit boundaries), all load/store sizes with signed and unsigned loads,
all six branch conditions taken and not taken, JAL, JALR with bit 0 of the target set (it must
be cleared), AUIPC, LUI, division by zero and MIN / -1, back-to-back M instructions
"""

import argparse
import random

POOL = list(range(5, 29))
BUF = 256
EDGES = [0, 1, 2, 0xFFFFFFFFFFFFFFFF, 0x8000000000000000, 0x7FFFFFFFFFFFFFFF, 0x7FFFFFFF, 0x80000000,
         0xFFFFFFFF, 0xFFFFFFFF80000000, 0x100000000, 0xFFFFFFFF7FFFFFFF, 0x8000000000000001, 0x5555555555555555,
         0xAAAAAAAAAAAAAAAA, 0x00000000FFFF0000, 0xFFFF]

R_OPS = ["add", "sub", "sll", "slt", "sltu", "xor", "srl", "sra", "or", "and"]
RW_OPS = ["addw", "subw", "sllw", "srlw", "sraw"]
M_OPS = ["mul", "mulh", "mulhsu", "mulhu", "div", "divu", "rem", "remu"]
MW_OPS = ["mulw", "divw", "divuw", "remw", "remuw"]
I_OPS = ["addi", "slti", "sltiu", "xori", "ori", "andi"]
BRANCHES = ["beq", "bne", "blt", "bge", "bltu", "bgeu"]
LOADS = {"lb": 1, "lbu": 1, "lh": 2, "lhu": 2, "lw": 4, "lwu": 4, "ld": 8}
STORES = {"sb": 1, "sh": 2, "sw": 4, "sd": 8}


class Gen:
    def __init__(self, seed, n):
        self.r = random.Random(seed)
        self.n = n
        self.out = []
        self.label = 0

    def emit(self, text):
        self.out.append("\t" + text)

    def new_label(self):
        self.label += 1
        return f".L{self.label}"

    def reg(self):
        return f"x{self.r.choice(POOL)}"

    def value(self):
        """A 64-bit constant: an edge value or a random value of random length"""
        if self.r.random() < 0.4:
            return self.r.choice(EDGES)
        return self.r.getrandbits(self.r.choice((8, 16, 32, 64)))

    def load_const(self, reg):
        self.emit(f"li {reg}, {self.value():#x}")

    def simple(self):
        """One computation, load or store instruction (no control flow)"""
        r = self.r
        kind = r.choices(["r", "rw", "m", "mw", "i", "iw", "shift", "shiftw", "lui", "auipc", "load", "store", "li"],
                         weights=[16, 6, 12, 5, 12, 4, 6, 3, 3, 2, 8, 8, 5])[0]
        rd, rs1, rs2 = self.reg(), self.reg(), self.reg()
        if kind == "r":
            self.emit(f"{r.choice(R_OPS)} {rd}, {rs1}, {rs2}")
        elif kind == "rw":
            self.emit(f"{r.choice(RW_OPS)} {rd}, {rs1}, {rs2}")
        elif kind == "m":
            self.emit(f"{r.choice(M_OPS)} {rd}, {rs1}, {rs2}")
        elif kind == "mw":
            self.emit(f"{r.choice(MW_OPS)} {rd}, {rs1}, {rs2}")
        elif kind == "i":
            self.emit(f"{r.choice(I_OPS)} {rd}, {rs1}, {r.choice([r.randrange(-2048, 2048), -1, 0, 1, 2047, -2048])}")
        elif kind == "iw":
            self.emit(f"addiw {rd}, {rs1}, {r.randrange(-2048, 2048)}")
        elif kind == "shift":
            self.emit(f"{r.choice(['slli', 'srli', 'srai'])} {rd}, {rs1}, {r.randrange(64)}")
        elif kind == "shiftw":
            self.emit(f"{r.choice(['slliw', 'srliw', 'sraiw'])} {rd}, {rs1}, {r.randrange(32)}")
        elif kind == "lui":
            self.emit(f"lui {rd}, {r.randrange(1 << 20):#x}")
        elif kind == "auipc":
            self.emit(f"auipc {rd}, {r.randrange(1 << 20):#x}")
        elif kind == "load":
            op, size = r.choice(list(LOADS.items()))
            self.emit(f"{op} {rd}, {r.randrange(0, BUF - size + 1, size)}(x30)")
        elif kind == "store":
            op, size = r.choice(list(STORES.items()))
            self.emit(f"{op} {rs2}, {r.randrange(0, BUF - size + 1, size)}(x30)")
        else:
            self.load_const(rd)

    def block(self, count):
        for _ in range(count):
            self.simple()

    def forward_branch(self):
        """A conditional branch over 1..4 instructions"""
        skip = self.new_label()
        self.emit(f"{self.r.choice(BRANCHES)} {self.reg()}, {self.reg()}, {skip}")
        self.block(self.r.randint(1, 4))
        self.out.append(f"{skip}:")

    def forward_jal(self):
        skip = self.new_label()
        rd = self.r.choice([self.reg(), "x0"])
        self.emit(f"jal {rd}, {skip}")
        self.block(self.r.randint(1, 3))
        self.out.append(f"{skip}:")

    def forward_jalr(self):
        """JALR through x31; bit 0 of the address is set half of the time and must be ignored"""
        skip = self.new_label()
        rd = self.r.choice([self.reg(), "x0"])
        self.emit(f"la x31, {skip}")
        if self.r.random() < 0.5:
            self.emit("addi x31, x31, 1")
        self.emit(f"jalr {rd}, 0(x31)")
        self.block(self.r.randint(1, 3))
        self.out.append(f"{skip}:")

    def loop(self):
        """A counted loop; the body never writes x29"""
        top = self.new_label()
        self.emit(f"li x29, {self.r.randint(2, 5)}")
        self.out.append(f"{top}:")
        self.block(self.r.randint(2, 6))
        self.emit("addi x29, x29, -1")
        self.emit(f"bnez x29, {top}")

    def program(self):
        self.out += ["\t.section .data", "\t.balign 8", "buf:", f"\t.space {BUF}",
                     "\t.section .text", "\t.globl main", "main:", "\tla x30, buf"]
        for reg in POOL:
            self.load_const(f"x{reg}")
        # fill the buffer so that loads read varied data
        for off in range(0, BUF, 8):
            self.emit(f"sd x{self.r.choice(POOL)}, {off}(x30)")
        done = 0
        while done < self.n:
            step = self.r.choices(["block", "branch", "jal", "jalr", "loop", "reload"], weights=[30, 14, 4, 4, 3, 5])[0]
            if step == "block":
                k = self.r.randint(3, 10)
                self.block(k)
                done += k
            elif step == "reload":
                self.load_const(self.reg())
                done += 1
            else:
                {"branch": self.forward_branch, "jal": self.forward_jal, "jalr": self.forward_jalr,
                 "loop": self.loop}[step]()
                done += 6
        self.emit("li a0, 0")
        self.emit("ret")
        return "\n".join(self.out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--n", type=int, default=400, help="approximate number of instructions (default 400)")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    text = f"# generated by scripts/gen_prog.py --seed {args.seed} --n {args.n}\n" \
           f"# SPDX-License-Identifier: CERN-OHL-S-2.0\n" + Gen(args.seed, args.n).program()
    with open(args.output, "w") as f:
        f.write(text)


if __name__ == "__main__":
    main()
