# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""Build the soft-float library for the floating-point benchmarks (rv64im, lp64: no FPU).

The toolchain's libgcc and libm exist only for cores with an FPU (rv64imafdc), so this builds:
  - the soft-float arithmetic of compiler-rt (LLVM): add, sub, mul, div, compare and conversions for
    float, double and long double (128 bits on RV64);
  - the few libm functions the benchmarks call (pow, sqrt, acos, cos, atan) from fdlibm in newlib;
  - our glue, sw/softfp/libm_shim.c.

Sources come from the official release archives, pinned by version and SHA-256 (the hashes were
recorded when the archives were first downloaded); nothing from them is committed. The archives are
cached in <out>/downloads.
"""

import argparse
import hashlib
import re
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ARCHIVES = {
    "compiler-rt": (
        "https://github.com/llvm/llvm-project/releases/download/llvmorg-19.1.7/compiler-rt-19.1.7.src.tar.xz",
        "c12b6e764202c615c1a3af9a13d477846878757ae0e29e5f8979215a6958fffc",
    ),
    "newlib": (
        "https://sourceware.org/pub/newlib/newlib-4.5.0.20241231.tar.gz",
        "33f12605e0054965996c25c1382b3e463b0af91799001f5bb8c0630f2ec8c852",
    ),
}

# compiler-rt: the floating-point routines, fp_mode.c (the rounding mode they read) and the
# count-leading-zeros helpers they call (no Zbb in the core)
CRT_FP = re.compile(r"(add|sub|mul|div)[sdt]f3|neg[sdt]f2|compare[sdt]f2|(extend|trunc)[sdt]f[sdt]f2"
                    r"|(fix|fixuns)[sdt]f[sdt]i|(float|floatun)[sdt]i[sdt]f|fp_mode|clz[sdt]i2")
# newlib: the IEEE cores of fdlibm and their helpers
FDLIBM = ["math/e_pow.c", "math/e_sqrt.c", "math/e_acos.c", "math/s_cos.c", "math/k_cos.c",
          "math/k_sin.c", "math/e_rem_pio2.c", "math/k_rem_pio2.c", "math/s_atan.c", "math/s_floor.c",
          "common/s_scalbn.c", "common/s_copysign.c"]
CFLAGS = ["-march=rv64im_zicsr", "-mabi=lp64", "-mcmodel=medany", "-O2", "-ffreestanding", "-fno-builtin"]


def fetch(name, out):
    url, sha = ARCHIVES[name]
    path = out / "downloads" / url.rsplit("/", 1)[1]
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"softfp: downloading {url}")
        urllib.request.urlretrieve(url, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != sha:
        sys.exit(f"softfp: {path.name}: SHA-256 {digest}, expected {sha}")
    return path


def extract(archive, wanted, dest):
    """extract the members whose path relative to the top folder satisfies wanted(rel)"""
    with tarfile.open(archive) as tar:
        for m in tar.getmembers():
            rel = m.name.split("/", 1)[1] if "/" in m.name else ""
            if m.isfile() and wanted(rel):
                m.name = rel
                tar.extract(m, dest, filter="data")


def compile_all(cross, sources, includes, objdir, defines=()):
    objs = []
    for src in sources:
        obj = objdir / (src.parent.name + "_" + src.stem + ".o")
        subprocess.run([f"{cross}gcc", *CFLAGS, *defines, *(f"-I{i}" for i in includes), "-c", str(src),
                        "-o", str(obj)], check=True)
        objs.append(obj)
    return objs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="build/softfp")
    ap.add_argument("--cross", default="riscv64-elf-")
    args = ap.parse_args()
    out = Path(args.out)
    src = out / "src"
    objdir = out / "obj"
    objdir.mkdir(parents=True, exist_ok=True)

    crt = src / "compiler-rt"
    extract(fetch("compiler-rt", out), lambda r: r.startswith("lib/builtins/") and r.count("/") == 2
            and (r.endswith((".h", ".inc")) or CRT_FP.fullmatch(Path(r).stem)), crt)
    nl = src / "newlib"
    extract(fetch("newlib", out), lambda r: r == "COPYING.NEWLIB" or r in
            [f"newlib/libm/{f}" for f in FDLIBM] + ["newlib/libm/common/fdlibm.h",
                                                     "newlib/libm/common/math_config.h"], nl)

    builtins = crt / "lib" / "builtins"
    objs = compile_all(args.cross, sorted(builtins.glob("*.c")), [builtins], objdir)
    libm = nl / "newlib" / "libm"
    objs += compile_all(args.cross, [libm / f for f in FDLIBM], [libm / "common"], objdir, ["-D_IEEE_LIBM"])
    objs += compile_all(args.cross, [Path("sw/softfp/libm_shim.c")], [], objdir)

    lib = out / "libsoftfp.a"
    lib.unlink(missing_ok=True)
    subprocess.run([f"{args.cross}ar", "rcs", str(lib), *map(str, objs)], check=True)
    print(f"softfp: {lib} ({len(objs)} objects)")


if __name__ == "__main__":
    main()
