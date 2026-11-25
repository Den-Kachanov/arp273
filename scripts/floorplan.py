#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
"""
Floorplan estimate: how the core could look on a die, block by block

Usage: floorplan.py [--top core] [--xlen 64] [--seed 1] [--starts 16] [-j N] [--out build/floorplan]

1. Area. Yosys synthesizes the design with the hierarchy kept, maps it to plain CMOS gates
   (NAND, NOR, NOT and D flip-flops; `abc -g cmos2`) and counts transistors per module, the same
   way `stat -tech cmos` does (NAND/NOR 4, NOT 2, D flip-flop 16). Gate equivalents (GE) are
   transistors / 4, one GE being a 2-input NAND. The area in um^2 assumes the sky130 high-density
   library, where a NAND2 cell is 3.75 um^2, and a placement utilisation of 70 %. These two numbers
   are assumptions, not measurements: the result is an order of magnitude, not a layout.
2. Connections. From the same netlist: every net bit of the top module is traced to the blocks
   it touches (sub-module instances, the glue logic of the top module itself, the top-level
   ports). Bits shared by a pair of blocks form a bus between them. Clock and reset, and any bit
   that reaches more than 6 blocks, are global nets and are not drawn as buses.
3. Placement. Classic slicing floorplanning (Wong and Liu, 1986): a normalized Polish expression
   is improved by simulated annealing; each block is soft (aspect ratio 1:2 to 2:1); the cost is
   the die area, the bus length (bits x Manhattan distance between block centres) and a penalty
   for a die far from square. Each group of top-level ports gets an edge of the die (also chosen
   by the annealing) and sits on it at the point nearest to the blocks it connects to.
   Annealing is random: --starts independent runs with seeds seed, seed + 1, ... run in parallel
   processes (-j, default: number of CPUs) and the one with the lowest cost is kept
4. Drawing: the die with the blocks to scale, buses with their width in bits, the port groups on
   the edges, and a table of the numbers.

Blocks that are flip-flop arrays here (the register file) would be a memory macro or latch
array on a real chip, much smaller than flip-flops; the estimate keeps what the RTL gives.
"""

import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRANSISTORS = {"$_NAND_": 4, "$_NOR_": 4, "$_NOT_": 2, "$_DFF_P_": 16}
NAND2_UM2 = 3.75          # sky130_fd_sc_hd nand2_1, an assumption for the estimate
UTILISATION = 0.70        # share of the block area taken by cells, an assumption
GLOBAL_FANOUT = 6
COLORS = ("#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#ff9da7",
          "#9c755f", "#bab0ac")


def sources():
    files = sorted((REPO / "rtl" / "pkg").glob("*.sv")) + sorted((REPO / "rtl" / "core").glob("*.sv"))
    return [str(f.relative_to(REPO)) for f in files]


def synthesize(top, xlen, tmp):
    json_path = Path(tmp) / "netlist.json"
    script = (f"read_verilog -sv -DXLEN={xlen} {' '.join(sources())}; synth -top {top}; "
              f"dfflegalize -cell $_DFF_P_ x; abc -g cmos2; opt_clean; write_json {json_path}")
    subprocess.run(["yosys", "-q", "-p", script], check=True, capture_output=True, text=True, cwd=REPO)
    return json.loads(json_path.read_text())["modules"]


def module_transistors(mods, name):
    """Transistors of a module including its sub-modules"""
    total = 0
    for cell in mods[name]["cells"].values():
        if cell["type"] in mods:
            total += module_transistors(mods, cell["type"])
        else:
            if cell["type"] not in TRANSISTORS:
                raise ValueError(f"floorplan: unexpected cell {cell['type']} after abc -g cmos2")
            total += TRANSISTORS[cell["type"]]
    return total


def clean(type_name):
    return type_name.split("\\")[1] if type_name.startswith("$paramod\\") else type_name


def blocks_and_buses(mods, top):
    """Blocks with transistor counts, buses between blocks, and the top-level port groups"""
    topm = mods[top]
    blocks, glue_t = {}, 0
    endpoints = {}                       # net bit -> set of endpoints

    def touch(bit, who):
        if isinstance(bit, int):
            endpoints.setdefault(bit, set()).add(who)

    for inst, cell in topm["cells"].items():
        if cell["type"] in mods:
            name = clean(cell["type"])
            blocks[name] = {"t": module_transistors(mods, cell["type"]), "inst": inst}
            for bits in cell["connections"].values():
                for b in bits:
                    touch(b, name)
        else:
            glue_t += TRANSISTORS[cell["type"]]
            for bits in cell["connections"].values():
                for b in bits:
                    touch(b, "glue")
    if glue_t:
        blocks["glue"] = {"t": glue_t, "inst": f"{top}.sv"}

    globals_, port_group = set(), {}
    for pname, port in topm["ports"].items():
        group = pname.split("_")[0] if pname not in ("clk_i", "rst_ni") else pname
        if pname in ("clk_i", "rst_ni"):
            globals_.update(b for b in port["bits"] if isinstance(b, int))
            continue
        port_group[group] = port_group.get(group, 0) + len(port["bits"])
        for b in port["bits"]:
            touch(b, f"io:{group}")

    buses = {}
    for bit, ends in endpoints.items():
        if bit in globals_ or len(ends) > GLOBAL_FANOUT:
            continue
        ends = sorted(ends)
        for i in range(len(ends)):
            for j in range(i + 1, len(ends)):
                key = (ends[i], ends[j])
                buses[key] = buses.get(key, 0) + 1
    return blocks, buses, port_group


# ---------- slicing floorplan, Wong-Liu ----------

ASPECTS = (0.5, 0.7, 1.0, 1.4, 2.0)


def pareto(shapes):
    """Keep shapes (w, h) that are not dominated, sorted by width"""
    shapes = sorted(set(shapes))
    out, best_h = [], math.inf
    for w, h in shapes:
        if h < best_h - 1e-9:
            out.append((w, h))
            best_h = h
    return out


def evaluate(expr, area):
    """Shape curves bottom-up; return the root curve and, per node, how each shape was built"""
    stack = []
    for tok in expr:
        if tok in ("H", "V"):
            b, a = stack.pop(), stack.pop()
            combos = []
            for ia, (wa, ha) in enumerate(a[0]):
                for ib, (wb, hb) in enumerate(b[0]):
                    if tok == "V":
                        combos.append(((wa + wb, max(ha, hb)), ia, ib))
                    else:
                        combos.append(((max(wa, wb), ha + hb), ia, ib))
            keep = pareto([c[0] for c in combos])
            src = {}
            for shp, ia, ib in combos:
                if shp in keep and shp not in src:
                    src[shp] = (ia, ib)
            stack.append((keep, (tok, a, b, [src[s] for s in keep])))
        else:
            s = [(math.sqrt(area[tok] * r), math.sqrt(area[tok] / r)) for r in ASPECTS]
            stack.append((pareto(s), ("leaf", tok)))
    return stack[0]


def assign(node, k, x, y, rects):
    """Place shape k of a node at (x, y), recursively"""
    shapes, info = node
    w, h = shapes[k]
    if info[0] == "leaf":
        rects[info[1]] = (x, y, w, h)
        return
    tok, a, b, src = info
    ia, ib = src[k]
    assign(a, ia, x, y, rects)
    if tok == "V":
        assign(b, ib, x + a[0][ia][0], y, rects)
    else:
        assign(b, ib, x, y + a[0][ia][1], rects)


def floorplan(names, area, buses, port_group, seed, iters=20000):
    """Simulated annealing over normalized Polish expressions; return (rects, die_w, die_h, wirelength)"""
    rng = random.Random(seed)
    edges = [(a, b, w) for (a, b), w in buses.items()]
    groups = sorted(port_group)
    total_area = sum(area.values())

    def pins(sides, centre, die_w, die_h):
        """Each port group on its edge, at the bus-weighted centre of the blocks it connects to"""
        pts = {}
        for g in groups:
            io = f"io:{g}"
            peers = [(b if a == io else a, wt) for a, b, wt in edges if io in (a, b)]
            peers = [(n, wt) for n, wt in peers if n in centre]
            tot = sum(wt for _, wt in peers) or 1
            cx = sum(centre[n][0] * wt for n, wt in peers) / tot if peers else die_w / 2
            cy = sum(centre[n][1] * wt for n, wt in peers) / tot if peers else die_h / 2
            pts[io] = [(0.0, cy), (die_w, cy), (cx, 0.0), (cx, die_h)][sides[g]]
        # groups on the same edge keep a minimum distance, so that they and their labels do not overlap
        for side in range(4):
            on_side = sorted((io for io in pts if sides[io[3:]] == side),
                             key=lambda io: pts[io][1] if side < 2 else pts[io][0])
            length = die_h if side < 2 else die_w
            gap = length / (len(on_side) + 1) if len(on_side) > 1 else 0
            gap = min(gap, 0.12 * length)
            prev = -math.inf
            for io in on_side:
                v = pts[io][1] if side < 2 else pts[io][0]
                v = min(max(v, prev + gap, 0.05 * length), 0.95 * length)
                pts[io] = (pts[io][0], v) if side < 2 else (v, pts[io][1])
                prev = v
        return pts

    def cost(expr, sides):
        root = evaluate(expr, area)
        best = None
        for k, (w, h) in enumerate(root[0]):
            rects = {}
            assign(root, k, 0.0, 0.0, rects)
            centre = {n: (r[0] + r[2] / 2, r[1] + r[3] / 2) for n, r in rects.items()}
            centre.update(pins(sides, dict(centre), w, h))
            wl = sum(wt * (abs(centre[a][0] - centre[b][0]) + abs(centre[a][1] - centre[b][1]))
                     for a, b, wt in edges if a in centre and b in centre)
            aspect = max(w / h, h / w)
            c = (w * h) / total_area + 0.6 * wl / (wl_norm * math.sqrt(total_area)) + 0.3 * (aspect - 1)
            if best is None or c < best[0]:
                best = (c, rects, w, h, wl, centre)
        assert best is not None
        return best

    wl_norm = max(sum(buses.values()), 1)
    # initial expression: a row of blocks
    expr = [names[0]]
    for n in names[1:]:
        expr += [n, "V" if len(expr) % 4 == 1 else "H"]

    def valid(e):
        operands = 0
        for i, t in enumerate(e):
            operands += -1 if t in ("H", "V") else 1
            if operands < 1:
                return False
            if t in ("H", "V") and i > 0 and e[i - 1] == t:
                return False        # normalized: no two equal operators in a row
        return operands == 1

    sides = {g: i % 4 for i, g in enumerate(groups)}
    cur = cost(expr, sides)
    best_expr, best = expr[:], cur
    temp = 1.0
    for it in range(iters):
        e, sd = expr[:], dict(sides)
        move = rng.randrange(4 if groups else 3)
        idx = [i for i, t in enumerate(e) if t not in ("H", "V")]
        if move == 0:                                   # swap two adjacent operands
            k = rng.randrange(len(idx) - 1)
            e[idx[k]], e[idx[k + 1]] = e[idx[k + 1]], e[idx[k]]
        elif move == 1:                                 # complement a chain of operators
            ops = [i for i, t in enumerate(e) if t in ("H", "V")]
            i = rng.choice(ops)
            while i < len(e) and e[i] in ("H", "V"):
                e[i] = "V" if e[i] == "H" else "H"
                i += 1
        elif move == 2:                                 # swap an operand with a neighbouring operator
            i = rng.randrange(len(e) - 1)
            if (e[i] in ("H", "V")) != (e[i + 1] in ("H", "V")):
                e[i], e[i + 1] = e[i + 1], e[i]
        else:                                           # move a port group to another edge
            sd[rng.choice(groups)] = rng.randrange(4)
        if not valid(e):
            continue
        new = cost(e, sd)
        if new[0] < cur[0] or rng.random() < math.exp((cur[0] - new[0]) / max(temp, 1e-9)):
            expr, sides, cur = e, sd, new
            if cur[0] < best[0]:
                best_expr, best = expr[:], cur
        temp = 1.0 * (0.9995 ** it)
    cost_value, rects, w, h, wl, centre = best
    return cost_value, rects, w, h, wl, best_expr, {k: v for k, v in centre.items() if k.startswith("io:")}


def draw(top, xlen, blocks, area_um2, buses, port_group, rects, die_w, die_h, wl, pins, svg_path):
    scale = 900 / max(die_w, die_h)
    margin, table_w = 150, 470
    W = int(die_w * scale) + 2 * margin + table_w
    H = max(int(die_h * scale) + 2 * margin + 60, 760)
    ox, oy = margin, margin + 40

    def P(x, y):
        return ox + x * scale, oy + (die_h - y) * scale     # y up, like a layout viewer

    order = sorted(blocks, key=lambda n: -area_um2[n])
    color = {n: COLORS[i % len(COLORS)] for i, n in enumerate(order)}
    total = sum(area_um2.values())
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         f'<text x="{margin}" y="38" font-size="22" font-weight="bold" fill="#222">Floorplan estimate of {top} '
         f'(XLEN = {xlen}): {die_w:.0f} x {die_h:.0f} um, {die_w * die_h / 1e6:.3f} mm2</text>',
         f'<text x="{margin}" y="60" font-size="13" fill="#555">Area from transistor counts (Yosys, abc -g cmos2) x '
         f'{NAND2_UM2} um2 per NAND2 (sky130 hd) / {UTILISATION:.0%} utilisation; slicing floorplan by simulated '
         f'annealing; an estimate, not a layout</text>']
    # die
    x0, y0 = P(0, die_h)
    o.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{die_w * scale:.1f}" height="{die_h * scale:.1f}" '
             f'fill="#20262e" stroke="#c9a227" stroke-width="3"/>')
    # blocks
    for n in order:
        x, y, w, h = rects[n]
        rx, ry = P(x, y + h)
        o.append(f'<rect x="{rx + 2:.1f}" y="{ry + 2:.1f}" width="{w * scale - 4:.1f}" height="{h * scale - 4:.1f}" '
                 f'fill="{color[n]}" fill-opacity="0.85" stroke="#111" stroke-width="1"/>')
    # buses: centre to centre, width by bits
    centre = {n: P(r[0] + r[2] / 2, r[1] + r[3] / 2) for n, r in rects.items()}
    for name, (px, py) in pins.items():
        centre[name] = P(px, py)
    for (a, b), bits in sorted(buses.items(), key=lambda kv: kv[1]):
        if a not in centre or b not in centre:
            continue
        (ax, ay), (bx, by) = centre[a], centre[b]
        sw = 0.8 + 1.4 * math.log2(1 + bits)
        o.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="#ffffff" '
                 f'stroke-opacity="0.55" stroke-width="{sw:.1f}" stroke-linecap="round"/>')
        mx, my = (ax + bx) / 2, (ay + by) / 2
        o.append(f'<text x="{mx:.1f}" y="{my + 4:.1f}" font-size="11" text-anchor="middle" fill="#fff" '
                 f'stroke="#20262e" stroke-width="3" paint-order="stroke">{bits}</text>')
    # labels on top of the buses
    for n in order:
        x, y, w, h = rects[n]
        cx, cy = P(x + w / 2, y + h / 2)
        box = min(w, h) * scale
        if box < 60:
            # too small for a label inside: a short tag, the full name is in the table
            o.append(f'<text x="{cx:.1f}" y="{cy + 4:.1f}" font-size="10" font-weight="bold" text-anchor="middle" '
                     f'fill="#fff" stroke="#111" stroke-width="2.5" paint-order="stroke">{n[:3]}</text>')
            continue
        # fit the name into the block width (about 0.6 em per character)
        fs = max(9, min(20, int(box / 7), int(w * scale / (0.62 * len(n)))))
        o.append(f'<text x="{cx:.1f}" y="{cy - 2:.1f}" font-size="{fs}" font-weight="bold" text-anchor="middle" '
                 f'fill="#fff" stroke="#111" stroke-width="3" paint-order="stroke">{n}</text>')
        o.append(f'<text x="{cx:.1f}" y="{cy + fs - 2:.1f}" font-size="{max(9, fs - 5)}" text-anchor="middle" '
                 f'fill="#fff" stroke="#111" stroke-width="3" paint-order="stroke">'
                 f'{area_um2[n] / 1e3:.1f}k um2</text>')
    # port groups on the edge
    for name, (px, py) in pins.items():
        cx, cy = P(px, py)
        g = name[3:]
        o.append(f'<rect x="{cx - 9:.1f}" y="{cy - 9:.1f}" width="18" height="18" fill="#c9a227" stroke="#111"/>')
        dx = -14 if px == 0 else (14 if px >= die_w - 1e-6 else 0)
        dy = 24 if py == 0 else (-14 if py >= die_h - 1e-6 else 4)
        anchor = "end" if dx < 0 else ("start" if dx > 0 else "middle")
        o.append(f'<text x="{cx + dx:.1f}" y="{cy + dy:.1f}" font-size="12" text-anchor="{anchor}" fill="#222">'
                 f'{g} ({port_group[g]} bits)</text>')
    # table
    tx, ty = W - table_w + 10, oy
    o.append(f'<text x="{tx}" y="{ty}" font-size="15" font-weight="bold" fill="#222">Block     transistors    GE    '
             f'area um2    share</text>')
    for i, n in enumerate(order):
        t = blocks[n]["t"]
        y = ty + 24 + i * 22
        o.append(f'<rect x="{tx}" y="{y - 12}" width="12" height="12" fill="{color[n]}"/>'
                 f'<text x="{tx + 18}" y="{y}" font-size="13" fill="#222" xml:space="preserve">'
                 f'{n:<14}{t:>9,}{t / 4:>9,.0f}{area_um2[n]:>11,.0f}{100 * area_um2[n] / total:>7.1f}%'
                 f'{f"  [{n[:3]}]" if min(rects[n][2], rects[n][3]) * scale < 60 else ""}</text>')
    y = ty + 24 + len(order) * 22 + 14
    lines = [f"blocks: {total / 1e6:.3f} mm2; die: {die_w * die_h / 1e6:.3f} mm2 "
             f"(whitespace {100 * (1 - total / (die_w * die_h)):.0f}%)",
             f"bus length: {wl / 1e3:,.0f} bit-mm (bits x Manhattan distance)",
             f"buses: {len(buses)}, {sum(buses.values())} bit connections; clk, rst and nets",
             f"reaching more than {GLOBAL_FANOUT} blocks are global, not drawn",
             f"top-level ports: {sum(port_group.values())} bits in {len(port_group)} groups",
             "regfile: flip-flops here; on silicon a memory macro would be smaller",
             "generated by scripts/floorplan.py from the RTL"]
    for i, t in enumerate(lines):
        o.append(f'<text x="{tx}" y="{y + i * 18}" font-size="12" fill="#444">{t}</text>')
    o.append("</svg>")
    svg_path.write_text("\n".join(o) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", default="core")
    ap.add_argument("--xlen", type=int, default=64)
    ap.add_argument("--seed", type=int, default=1, help="first annealing seed; the same seeds give the same floorplan")
    ap.add_argument("--starts", type=int, default=16, help="independent annealing runs, the best is kept")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1, help="parallel processes")
    ap.add_argument("--out", default=str(REPO / "build" / "floorplan"))
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="floorplan_") as tmp:
        mods = synthesize(args.top, args.xlen, tmp)
    blocks, buses, port_group = blocks_and_buses(mods, args.top)
    if not blocks:
        sys.exit(f"floorplan: {args.top} has no sub-modules to place")
    area_um2 = {n: b["t"] / 4 * NAND2_UM2 / UTILISATION for n, b in blocks.items()}
    names = sorted(blocks, key=lambda n: -area_um2[n])
    seeds = [args.seed + i for i in range(max(1, args.starts))]
    with ProcessPoolExecutor(max_workers=max(1, min(args.jobs, len(seeds)))) as pool:
        runs = list(pool.map(floorplan, *zip(*[(names, area_um2, buses, port_group, sd) for sd in seeds],
                                             strict=True)))
    best_i = min(range(len(runs)), key=lambda i: runs[i][0])
    _, rects, w, h, wl, expr, pins = runs[best_i]
    best_seed = seeds[best_i]
    wls = sorted(r[4] / 1e3 for r in runs)

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    svg = out / f"{args.top}_xlen{args.xlen}.svg"
    draw(args.top, args.xlen, blocks, area_um2, buses, port_group, rects, w, h, wl, pins, svg)
    (out / f"{args.top}_xlen{args.xlen}.json").write_text(json.dumps({
        "top": args.top, "xlen": args.xlen, "seed": best_seed, "starts": len(seeds),
        "polish_expression": " ".join(expr),
        "assumptions": {"nand2_um2": NAND2_UM2, "utilisation": UTILISATION},
        "die_um": [round(w, 1), round(h, 1)],
        "blocks": {n: {"transistors": b["t"], "ge": b["t"] / 4, "area_um2": round(area_um2[n], 1),
                       "rect_um": [round(v, 1) for v in rects[n]]} for n, b in blocks.items()},
        "buses_bits": {f"{a} -- {b}": v for (a, b), v in sorted(buses.items(), key=lambda kv: -kv[1])},
        "ports_bits": port_group}, indent=1))

    print(f"floorplan: {args.top}, XLEN = {args.xlen}, {len(seeds)} annealing runs, best seed {best_seed} "
          f"(bus length {wls[0]:.0f} to {wls[-1]:.0f} bit-mm over the runs)")
    for n in names:
        print(f"  {n:<14} {blocks[n]['t']:>8,} transistors {blocks[n]['t'] / 4:>8,.0f} GE {area_um2[n]:>10,.0f} um2")
    print(f"  die {w:.0f} x {h:.0f} um = {w * h / 1e6:.3f} mm2, blocks {sum(area_um2.values()) / 1e6:.3f} mm2")
    print("  largest buses: " + ", ".join(f"{a}-{b} {v}" for (a, b), v in
                                          sorted(buses.items(), key=lambda kv: -kv[1])[:6]))
    shown = svg.relative_to(REPO) if svg.is_relative_to(REPO) else svg
    print(f"  wrote {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
