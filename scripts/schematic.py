#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# SPDX-FileCopyrightText: 2026 Denys Kachanov
"""
Schematics generated from the RTL: Yosys builds the netlist, netlistsvg or Graphviz draws it

Usage: schematic.py [module ...] [--level LEVEL] [--all] [-j N] [--xlen 64] [--out DIR] [--max-cells N] [--loose]

Output: <out>/<level>/<module>_xlen<N>.svg, one folder per level (default out: build/schematics)

Levels:
  blocks  the module as Yosys elaborates it, sub-modules kept as boxes; for `core` this is the
          block diagram of the core: decoder, regfile, alu, lsu, muldiv... and the glue between them
  rtl     word-level cells after `proc; opt`: adders, comparators, muxes, registers
  gates   synthesized to simple gates (AND, OR, NAND, NOR, XOR, XNOR, MUX, NOT) and flip-flops;
          only for small modules: a netlist above --max-cells is skipped, it would be unreadable
  flat    the whole module flattened into one word-level netlist (no boxes), laid out by Graphviz:
          for `core` about 1300 cells on a sheet of about 20000 x 36000 pt; not readable, it shows
          the scale of the design as one piece. Takes about a minute
  map     gate map: every gate and flip-flop of the gate-level netlist is one small square,
          grouped by module, all at the same scale and coloured by kind (flip-flop, mux, xor,
          other logic). Shows where the area goes
  graph   the whole gate-level netlist as a graph: every gate is a dot, every connection a line,
          laid out by the DrL algorithm of igraph (built for graphs of 10^4 to 10^6 nodes; the
          core, 18000 gates, takes about 25 s on one CPU core). Clusters show the structure:
          the 31 registers of the regfile, the carry chains of the counters, muldiv and alu.
          Coloured by module, or by gate kind for a module without sub-modules. Needs igraph
          (in requirements.txt). Nets with more than 48 pins (clock, reset, broadcast buses)
          are left out, they would pull everything into one point

Every picture is drawn from the current code, so it cannot go out of date: rerun it after a change.
Without module names, the default set is drawn (the ones that stay readable, plus the gate map).
--all draws every module at every level that applies to it: blocks for modules with sub-modules,
rtl for the others, gates where the netlist is at most --max-cells, flat and map for modules with
sub-modules, graph for all. The jobs run in parallel (-j, default: number of CPUs); each Yosys,
netlistsvg or Graphviz run is single-threaded, so parallel jobs are what uses the machine.
No step can use a GPU: Yosys, ABC, ELK, Graphviz and igraph DrL all run on the CPU.

Besides one SVG per schematic, `all_xlen<N>.svg` puts every schematic of the run (blocks, rtl and
gates levels) on one sheet at the same scale, so their sizes can be compared directly.

Layout: a compact ELK setting (network-simplex layering and node placement, small spacings,
post-compaction) gives about a third of the area of the netlistsvg default on the default set;
--loose uses the netlistsvg default.

Yosys runs from the repository root with relative paths, so no absolute path of this machine
ends up in the SVG (cell ids contain the source location).

Needs netlistsvg in tools/: `npm --prefix tools ci` (pinned in tools/package-lock.json)
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import rtl_files

REPO = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO / ".venv" / "bin" / "python"
NETLISTSVG_DIR = REPO / "tools" / "node_modules" / "netlistsvg"
NETLISTSVG = NETLISTSVG_DIR / "bin" / "netlistsvg.js"
DEFAULT = [("core", "blocks"), ("decoder", "gates"), ("branch_unit", "rtl"), ("imm_gen", "rtl"), ("core", "map")]
LEVELS = ("blocks", "rtl", "gates", "flat", "map", "graph")
FANOUT_LIMIT = 48
GROUP_COLORS = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
                "#bcbd22", "#17becf")
NETLISTSVG_LEVELS = ("blocks", "rtl", "gates")
MAP_KINDS = (("flip-flop", "#c0392b"), ("mux", "#2e86c1"), ("xor/xnor", "#d4ac0d"), ("other logic", "#5d6d7e"))
GATES = "AND,NAND,OR,NOR,XOR,XNOR,MUX"
COMPACT = {
    "org.eclipse.elk.layered.layering.strategy": "NETWORK_SIMPLEX",
    "org.eclipse.elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
    "org.eclipse.elk.layered.compaction.postCompaction.strategy": "LEFT_RIGHT_CONSTRAINT_LOCKING",
    "org.eclipse.elk.layered.spacing.nodeNodeBetweenLayers": "15",
    "org.eclipse.elk.spacing.nodeNode": "10",
    "org.eclipse.elk.spacing.edgeNode": "6",
    "org.eclipse.elk.spacing.edgeEdge": "4",
    "org.eclipse.elk.layered.spacing.edgeNodeBetweenLayers": "6",
    "org.eclipse.elk.layered.spacing.edgeEdgeBetweenLayers": "4",
}


def sources():
    """RTL files relative to the repository root"""
    files = rtl_files.files(REPO, with_pkg=True)
    return [str(f.relative_to(REPO)) for f in files]


def compact_skin(path, post_compaction=True):
    """The netlistsvg default skin with the compact ELK options"""
    skin = (NETLISTSVG_DIR / "lib" / "default.svg").read_text()
    options = {k: v for k, v in COMPACT.items() if post_compaction or "postCompaction" not in k}
    attrs = "\n      ".join(f'{k}="{v}"' for k, v in options.items())
    skin, n = re.subn(r"<s:layoutEngine[^>]*/>", f"<s:layoutEngine\n      {attrs}\n    />", skin, flags=re.S)
    if n != 1:
        sys.exit("schematic: the netlistsvg skin has no single <s:layoutEngine/>; check the netlistsvg version")
    path.write_text(skin)


def netlist(module, level, xlen, json_path):
    """Run Yosys and write the netlist as JSON; return the number of cells in the top module"""
    read = f"read_verilog -sv -DXLEN={xlen} {' '.join(sources())}; hierarchy -top {module}"
    if level == "gates":
        script = f"{read}; synth -top {module} -flatten; abc -g {GATES}; opt_clean; write_json {json_path}"
    else:
        # blocks and rtl: no flattening, so instances stay boxes; rtl differs only for modules without children
        script = f"{read}; proc; opt -full; wreduce; opt_clean; clean -purge; write_json {json_path}"
    subprocess.run(["yosys", "-q", "-p", script], check=True, capture_output=True, text=True, cwd=REPO)
    data = json.loads(json_path.read_text())
    # a parameterised instance type reads "$paramod\regfile\WRITE_THROUGH=1'0"; show it as "regfile"
    for cell in data["modules"][module]["cells"].values():
        if cell["type"].startswith("$paramod\\"):
            cell["type"] = cell["type"].split("\\")[1]
    json_path.write_text(json.dumps(data))
    return len(data["modules"][module]["cells"])


def flat_schematic(module, xlen, svg_path, tmp):
    """Flatten to word-level cells and lay the whole netlist out with Graphviz dot; return the cell count"""
    prefix = Path(tmp) / f"{module}_flat"
    script = (f"read_verilog -sv -DXLEN={xlen} {' '.join(sources())}; hierarchy -top {module}; proc; flatten; "
              f"opt -full; wreduce; opt_clean; clean -purge; tee -q -o {prefix}.stat stat; "
              f"show -format dot -prefix {prefix} -notitle")
    subprocess.run(["yosys", "-q", "-p", script], check=True, capture_output=True, text=True, cwd=REPO)
    subprocess.run(["dot", "-Tsvg", f"{prefix}.dot", "-o", str(svg_path)],
                   check=True, capture_output=True, text=True, timeout=1800)
    m = re.search(r"(\d+)\s+cells", Path(f"{prefix}.stat").read_text())
    return int(m.group(1)) if m else 0


def kind(cell_type):
    """Gate kind for the map colours"""
    if "DFF" in cell_type or "DLATCH" in cell_type:
        return "flip-flop"
    if cell_type == "$_MUX_":
        return "mux"
    if cell_type in ("$_XOR_", "$_XNOR_"):
        return "xor/xnor"
    return "other logic"


def gate_counts(module, xlen, tmp):
    """
    Gate-level synthesis that keeps the hierarchy (flattening would lose which module a gate
    came from); return [(name, {kind: count})] for every sub-module instance, recursively
    summed, and for the glue logic of the top module itself
    """
    json_path = Path(tmp) / f"{module}_map.json"
    script = (f"read_verilog -sv -DXLEN={xlen} {' '.join(sources())}; synth -top {module}; "
              f"abc -g {GATES}; opt_clean; write_json {json_path}")
    subprocess.run(["yosys", "-q", "-p", script], check=True, capture_output=True, text=True, cwd=REPO)
    mods = json.loads(json_path.read_text())["modules"]

    def total(name):
        counts = {}
        for cell in mods[name]["cells"].values():
            parts = [total(cell["type"])] if cell["type"] in mods else [{kind(cell["type"]): 1}]
            for part in parts:
                for k, v in part.items():
                    counts[k] = counts.get(k, 0) + v
        return counts

    groups, glue = [], {}
    types = [c["type"] for c in mods[module]["cells"].values() if c["type"] in mods]
    for inst, cell in mods[module]["cells"].items():
        if cell["type"] in mods:
            label = cell["type"].split("\\")[1] if cell["type"].startswith("$paramod\\") else cell["type"]
            if types.count(cell["type"]) > 1:
                # a module used twice (the pipeline has two lsu): name the block after the instance
                label = inst.lstrip("\\")
                label = label[2:] if label.startswith("u_") else label
            groups.append((label, total(cell["type"])))
        else:
            k = kind(cell["type"])
            glue[k] = glue.get(k, 0) + 1
    if glue:
        groups.append((f"{module}.sv glue logic", glue))
    if not groups:
        groups.append((module, total(module)))
    return groups


def gate_map(module, xlen, svg_path, tmp):
    """Draw the gate map: one square per gate, a block per module, the same scale everywhere"""
    groups = sorted(gate_counts(module, xlen, tmp), key=lambda g: -sum(g[1].values()))
    # every coordinate stays a multiple of the pitch, so the square patterns line up in each block
    pitch, sq, pad, cap = 5, 4, 30, 50
    total_gates = sum(sum(c.values()) for _, c in groups)
    blocks = []
    for name, counts in groups:
        n = sum(counts.values())
        cols = max(8, round(n ** 0.5 * 1.3))
        ff = counts.get("flip-flop", 0)
        line2 = f"{n} ({100 * n / total_gates:.1f}%)" + (f", {ff} flip-flops" if ff else "")
        # the slot is at least as wide as its caption (about 8 px per character at 14-15 px)
        slot = max(cols * pitch, 8 * max(len(name), len(line2)) + 10)
        blocks.append((name, line2, counts, n, cols, -(-n // cols), slot))
    row_limit = max(max(b[6] for b in blocks), 1200)

    x, y, row_h, placed = pad, 90, 0, []
    for b in blocks:
        slot, h = b[6], b[5] * pitch
        if x > pad and x + slot > row_limit + pad:
            x, y, row_h = pad, y + row_h + pad, 0
        x, y = x - x % pitch, y - y % pitch
        placed.append((x, y, b))
        x += slot + pad
        row_h = max(row_h, h + cap)
    width = row_limit + 2 * pad
    height = y + row_h + 80

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
         f'<rect width="{width}" height="{height}" fill="#ffffff"/>', "<defs>"]
    for i, (_, color) in enumerate(MAP_KINDS):
        o.append(f'<pattern id="k{i}" width="{pitch}" height="{pitch}" patternUnits="userSpaceOnUse">'
                 f'<rect width="{sq}" height="{sq}" fill="{color}"/></pattern>')
    o.append("</defs>")
    o.append(f'<text x="{pad}" y="38" font-family="sans-serif" font-size="22" font-weight="bold" fill="#222">'
             f'Gate map of {module} (XLEN = {xlen}): {total_gates} gates and flip-flops, one square each</text>')
    o.append(f'<text x="{pad}" y="62" font-family="sans-serif" font-size="13" fill="#555">Yosys synth + abc -g '
             f'{GATES}, hierarchy kept; generic gates, not a real cell library or layout</text>')
    for x0, y0, block in placed:
        name, line2, counts, cols = block[0], block[1], block[2], block[4]
        o.append(f'<text x="{x0}" y="{y0 + 18}" font-family="sans-serif" font-size="15" font-weight="bold" '
                 f'fill="#1d3b5c">{name}</text>')
        o.append(f'<text x="{x0}" y="{y0 + 38}" font-family="sans-serif" font-size="13" fill="#444">{line2}</text>')
        top, i = y0 + cap, 0
        for k, (kname, _) in enumerate(MAP_KINDS):
            run = counts.get(kname, 0)
            while run > 0:
                r, c = divmod(i, cols)
                if c == 0 and run >= cols:
                    # whole rows at once
                    full = run // cols
                    o.append(f'<rect x="{x0}" y="{top + r * pitch}" width="{cols * pitch}" '
                             f'height="{full * pitch}" fill="url(#k{k})"/>')
                    take = full * cols
                else:
                    take = min(run, cols - c)
                    o.append(f'<rect x="{x0 + c * pitch}" y="{top + r * pitch}" width="{take * pitch}" '
                             f'height="{pitch}" fill="url(#k{k})"/>')
                i += take
                run -= take
    lx, ly = pad, height - 34
    for kname, color in MAP_KINDS:
        o.append(f'<rect x="{lx}" y="{ly - 11}" width="12" height="12" fill="{color}"/>'
                 f'<text x="{lx + 18}" y="{ly}" font-family="sans-serif" font-size="14" fill="#222">{kname}</text>')
        lx += 150
    o.append("</svg>")
    svg_path.write_text("\n".join(o) + "\n")
    return total_gates


def graph_view(module, xlen, svg_path, tmp):
    """Whole gate-level netlist as a graph, laid out by igraph DrL; return (gates, edges)"""
    try:
        import igraph
    except ImportError as e:
        raise RuntimeError("the graph level needs igraph: .venv/bin/pip install -r requirements.txt") from e
    json_path = Path(tmp) / f"{module}_graph.json"
    # synthesize per module first, flatten afterwards: the gate names keep the instance they came from
    script = (f"read_verilog -sv -DXLEN={xlen} {' '.join(sources())}; synth -top {module}; "
              f"abc -g {GATES}; opt_clean; flatten; opt_clean; write_json {json_path}")
    subprocess.run(["yosys", "-q", "-p", script], check=True, capture_output=True, text=True, cwd=REPO)
    cells = json.loads(json_path.read_text())["modules"][module]["cells"]
    names = list(cells)
    index = {n: i for i, n in enumerate(names)}

    def group(name):
        if name.startswith("$flatten\\"):
            inst = name[len("$flatten\\"):].split(".")[0]
            return inst[2:] if inst.startswith("u_") else inst
        return f"{module}.sv glue logic"

    groups = [group(n) for n in names]
    if len(set(groups)) == 1:          # no sub-modules: colour by gate kind instead
        groups = [kind(cells[n]["type"]) for n in names]
    nets = {}
    for name, cell in cells.items():
        for bits in cell["connections"].values():
            for b in bits:
                if isinstance(b, int):
                    nets.setdefault(b, set()).add(index[name])
    edges = []
    for members in nets.values():
        m = sorted(members)
        if 2 <= len(m) <= FANOUT_LIMIT:
            edges += [(m[0], j) for j in m[1:]]
    g = igraph.Graph(n=len(names), edges=edges)
    # DrL is random: a fixed seed makes the same netlist give the same picture (a stable file in git)
    igraph.set_random_number_generator(random.Random(1))
    coords = g.layout_drl().coords if len(names) > 2 else [(i, 0) for i in range(len(names))]

    # scale to a 1600 x 1600 drawing area
    xs, ys = [c[0] for c in coords], [c[1] for c in coords]
    x0, y0 = min(xs, default=0), min(ys, default=0)
    span = max(max(xs, default=1) - x0, max(ys, default=1) - y0, 1e-9)
    area, margin, legend_w = 1600, 30, 330
    pts = [(margin + (x - x0) / span * area, 90 + (y - y0) / span * area) for x, y in coords]
    order = sorted(set(groups), key=lambda k: -groups.count(k))
    color = {k: GROUP_COLORS[i % len(GROUP_COLORS)] for i, k in enumerate(order)}
    width, height = area + 2 * margin + legend_w, area + 130
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
         f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
         f'<text x="{margin}" y="38" font-family="sans-serif" font-size="22" font-weight="bold" fill="#222">'
         f'Netlist graph of {module} (XLEN = {xlen}): {len(names)} gates and flip-flops, {len(edges)} connections</text>',
         f'<text x="{margin}" y="62" font-family="sans-serif" font-size="13" fill="#555">igraph DrL layout; one dot '
         f'per gate; nets with more than {FANOUT_LIMIT} pins left out; not a placement or a layout</text>',
         ]
    # one path for all connections and one per colour for the dots: a much smaller file than one
    # element per line or dot (the core: about 18000 dots and 27000 lines)
    wires = " ".join(f"M{pts[a][0]:.0f} {pts[a][1]:.0f}L{pts[b][0]:.0f} {pts[b][1]:.0f}" for a, b in edges)
    o.append(f'<path d="{wires}" fill="none" stroke="#000" stroke-opacity="0.05" stroke-width="0.4"/>')
    for k in order:
        dots = "".join(f"M{p[0] - 1.2:.1f} {p[1] - 1.2:.1f}h2.4v2.4h-2.4z"
                       for p, gk in zip(pts, groups, strict=True) if gk == k)
        o.append(f'<path d="{dots}" fill="{color[k]}"/>')
    lx = area + 2 * margin
    for i, k in enumerate(order):
        y = 110 + i * 24
        o.append(f'<circle cx="{lx + 6}" cy="{y - 5}" r="6" fill="{color[k]}"/>'
                 f'<text x="{lx + 20}" y="{y}" font-family="sans-serif" font-size="15" fill="#222">'
                 f'{k} ({groups.count(k)})</text>')
    o.append("</svg>")
    svg_path.write_text("\n".join(o) + "\n")
    return len(names), len(edges)


def white_background(svg_path):
    """
    netlistsvg leaves the background transparent: black wires disappear on a dark page (GitHub
    in dark mode). Put a white rectangle under everything
    """
    text = svg_path.read_text()
    m = re.search(r"<svg\b[^>]*>", text)
    if m and 'data-background="white"' not in text:
        rect = '<rect data-background="white" width="100%" height="100%" fill="#ffffff" stroke="none"/>'
        svg_path.write_text(text[:m.end()] + rect + text[m.end():])


def draw(json_path, svg_path, skins):
    """
    Run netlistsvg with the first skin that works. ELK post-compaction sometimes fails with
    "Invalid hitboxes for scanline constraint calculation"; the next skin drops it, the last
    one (None) is the netlistsvg default
    """
    error = None
    for skin in skins:
        # netlistsvg recurses through bit-level nets; a large stack avoids "Maximum call stack size exceeded"
        cmd = ["node", "--stack-size=60000", str(NETLISTSVG), str(json_path), "-o", str(svg_path)]
        if skin:
            cmd += ["--skin", str(skin)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
            if svg_path.exists() and svg_path.stat().st_size > 0:
                white_background(svg_path)
                return
        except subprocess.CalledProcessError as e:
            error = e
    raise error or RuntimeError("netlistsvg wrote nothing")


def size(svg_text):
    """Width and height of the root <svg> element"""
    head = svg_text[:1000]
    w = re.search(r'\swidth="([\d.]+)"', head)
    h = re.search(r'\sheight="([\d.]+)"', head)
    if not (w and h):
        raise ValueError("schematic: an SVG without width and height on its root element")
    return float(w.group(1)), float(h.group(1))


def combine(parts, out, xlen):
    """
    Put every schematic on one sheet at the same scale (1 unit = 1 unit), largest first,
    in rows of limited width; each with a caption: module, level, cells and size
    """
    pad, cap, title_h = 40, 34, 70
    items = []
    for module, level, cells, path in parts:
        text = path.read_text()
        w, h = size(text)
        body = re.sub(r"^\s*<\?xml[^>]*>\s*", "", text)
        # the nested <svg> keeps its own coordinate system; only its position on the sheet is set
        body = re.sub(r"<svg\b([^>]*?)\swidth=\"[\d.]+\"\s+height=\"[\d.]+\"",
                      lambda m, w=w, h=h: f'<svg{m.group(1)} width="{w:g}" height="{h:g}" viewBox="0 0 {w:g} {h:g}"',
                      body, count=1)
        items.append({"m": module, "l": level, "c": cells, "w": w, "h": h, "body": body})
    items.sort(key=lambda i: -i["w"] * i["h"])
    row_limit = max(max(i["w"] for i in items), 2600)

    # shelf packing
    x, y, row_h, placed = pad, title_h, 0, []
    for it in items:
        if x > pad and x + it["w"] > row_limit + pad:
            x, y, row_h = pad, y + row_h + pad, 0
        placed.append((x, y, it))
        x += it["w"] + pad
        row_h = max(row_h, it["h"] + cap)
    width = row_limit + 2 * pad
    height = y + row_h + pad + 50

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
         f'xmlns:s="https://github.com/nturley/netlistsvg" width="{width:g}" height="{height:g}" '
         f'viewBox="0 0 {width:g} {height:g}">',
         f'<rect width="{width:g}" height="{height:g}" fill="#ffffff" stroke="none"/>',
         f'<text x="{pad}" y="42" style="font-family: sans-serif; font-size: 26px; font-weight: bold; fill: #222">'
         f'Schematics generated from the RTL, XLEN = {xlen}, all at the same scale</text>']
    for x0, y0, it in placed:
        o.append(f'<text x="{x0:g}" y="{y0 + 22:g}" style="font-family: sans-serif; font-size: 18px; '
                 f'font-weight: bold; fill: #1d3b5c">{it["m"]} ({it["l"]}): {it["c"]} cells, '
                 f'{it["w"]:.0f} × {it["h"]:.0f}</text>')
        o.append(f'<rect x="{x0 - 6:g}" y="{y0 + cap - 6:g}" width="{it["w"] + 12:g}" height="{it["h"] + 12:g}" '
                 f'fill="none" stroke="#9aaabb" stroke-dasharray="6,4"/>')
        o.append(f'<g transform="translate({x0:g},{y0 + cap:g})">{it["body"]}</g>')
    # scale bar, 500 units
    sy = height - 30
    o.append(f'<line x1="{pad}" y1="{sy:g}" x2="{pad + 500}" y2="{sy:g}" stroke="#222" stroke-width="3"/>'
             f'<text x="{pad + 510}" y="{sy + 6:g}" style="font-family: sans-serif; font-size: 16px; fill: #222">'
             f'500 units; cells: Yosys cells of the drawn netlist (gates, word-level cells or blocks)</text>')
    o.append("</svg>")
    out.write_text("\n".join(o) + "\n")


def run_job(module, level, args, tmp, skins):
    """Draw one schematic and time it; see draw_one for the result"""
    start = time.monotonic()
    result = draw_one(module, level, args, tmp, skins)
    return (*result, time.monotonic() - start)


def draw_one(module, level, args, tmp, skins):
    """
    Draw one schematic; return (module, level, status, note, svg_path or None, cells).
    status: "wrote", "skipped" or "FAILED". Runs in a worker thread: every tool is a subprocess
    """
    level_dir = Path(args.out).resolve() / level
    level_dir.mkdir(parents=True, exist_ok=True)
    svg_path = level_dir / f"{module}_xlen{args.xlen}.svg"
    work = Path(tmp) / f"{module}_{level}"
    work.mkdir(exist_ok=True)
    try:
        if level == "flat":
            n = flat_schematic(module, args.xlen, svg_path, work)
            return module, level, "wrote", f"{n} cells, flattened, Graphviz", svg_path, n
        if level == "map":
            n = gate_map(module, args.xlen, svg_path, work)
            return module, level, "wrote", f"gate map, {n} gates and flip-flops", svg_path, n
        if level == "graph":
            n, e = graph_view(module, args.xlen, svg_path, work)
            return module, level, "wrote", f"{n} gates, {e} connections, igraph DrL", svg_path, n
        json_path = work / "netlist.json"
        cells = netlist(module, level, args.xlen, json_path)
        if level == "gates" and cells > args.max_cells:
            return module, level, "skipped", f"{cells} cells > --max-cells {args.max_cells}; see the graph level", None, cells
        draw(json_path, svg_path, skins)
        w, h = size(svg_path.read_text())
        return module, level, "wrote", f"{cells} cells, {w:.0f} x {h:.0f}", svg_path, cells
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as e:
        text = (getattr(e, "stderr", "") or str(e)).strip().splitlines()
        return module, level, "FAILED", text[-1] if text else type(e).__name__, None, 0


def all_jobs(known):
    """Every module at every level that applies to it (see --all in the description)"""
    instantiates = {}
    for f in rtl_files.files(REPO):
        text = f.read_text()
        for m in re.findall(r"^\s*module\s+(\w+)", text, re.M):
            instantiates[m] = any(re.search(rf"^\s*{other}\s+(#\s*\(|\w+\s*\()", text, re.M)
                                  for other in known if other != m)
    jobs = []
    for m in sorted(known):
        if instantiates.get(m):
            jobs += [(m, "blocks"), (m, "flat"), (m, "map")]
        else:
            jobs += [(m, "rtl"), (m, "gates")]
        jobs.append((m, "graph"))
    return jobs


def main():
    # the graph level needs igraph from the project virtualenv; a direct run with the system Python
    # switches to it, the way `make` would run it
    # compare prefixes, not executables: .venv/bin/python is a symlink to the system interpreter
    if VENV_PYTHON.exists() and Path(sys.prefix).resolve() != (REPO / ".venv").resolve() \
            and os.environ.get("SCHEMATIC_NO_REEXEC") != "1":
        os.environ["SCHEMATIC_NO_REEXEC"] = "1"
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modules", nargs="*", help="modules to draw (default: a readable set)")
    ap.add_argument("--level", choices=LEVELS, help="level for the modules given (default rtl)")
    ap.add_argument("--all", action="store_true", help="every module at every level that applies")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1, help="parallel jobs (default: CPUs)")
    ap.add_argument("--xlen", type=int, default=64)
    ap.add_argument("--out", default=str(REPO / "build" / "schematics"))
    ap.add_argument("--max-cells", type=int, default=700, help="largest gate-level netlist for netlistsvg")
    ap.add_argument("--loose", action="store_true", help="netlistsvg default layout instead of the compact one")
    args = ap.parse_args()

    if not NETLISTSVG.exists():
        sys.exit("schematic: netlistsvg is not installed; run `npm --prefix tools ci`")
    known = set()
    for f in rtl_files.files(REPO):
        known |= set(re.findall(r"^\s*module\s+(\w+)", f.read_text(), re.M))
    if args.all:
        jobs = all_jobs(known)
    elif args.modules:
        jobs = [(m, args.level or "rtl") for m in args.modules]
    else:
        jobs = DEFAULT
    for m, _ in jobs:
        if m not in known:
            sys.exit(f"schematic: no module '{m}'; known: {', '.join(sorted(known))}")

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="schematic_") as tmp:
        skins = [None]
        if not args.loose:
            compact_skin(Path(tmp) / "compact.svg")
            compact_skin(Path(tmp) / "compact_no_post.svg", post_compaction=False)
            skins = [Path(tmp) / "compact.svg", Path(tmp) / "compact_no_post.svg", None]
        # slowest first (flat, then graph), so the long jobs do not start last
        weight = {"flat": 0, "graph": 1, "map": 2, "rtl": 3, "blocks": 3, "gates": 4}
        ordered = sorted(jobs, key=lambda j: weight[j[1]])
        print(f"schematic: {len(ordered)} jobs on {min(args.jobs, len(ordered))} workers")
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            futures = [pool.submit(run_job, m, lvl, args, tmp, skins) for m, lvl in ordered]
            for f in as_completed(futures):
                module, level, status, note, path, cells, secs = f.result()
                shown = path.relative_to(REPO) if isinstance(path, Path) and path.is_relative_to(REPO) else ""
                print(f"  {status:8} {level:6} {module:14} {secs:6.1f}s  {note}  {shown}", flush=True)
                results.append((module, level, status, path, cells))

    print(f"schematic: {len(results)} jobs done in {time.monotonic() - started:.0f} s")
    # same-scale sheets: one per netlistsvg level, and one with all of them
    drawn = [(m, lvl, c, p) for m, lvl, st, p, c in results if st == "wrote" and lvl in NETLISTSVG_LEVELS]
    for level in NETLISTSVG_LEVELS:
        parts = sorted([d for d in drawn if d[1] == level])
        if len(parts) > 1:
            sheet = out / level / f"sheet_xlen{args.xlen}.svg"
            combine(parts, sheet, args.xlen)
            print(f"  wrote    sheet  {level:14} {len(parts)} schematics, same scale  {sheet.relative_to(REPO) if sheet.is_relative_to(REPO) else sheet}")
    if len(drawn) > 1:
        sheet = out / f"all_xlen{args.xlen}.svg"
        combine(sorted(drawn), sheet, args.xlen)
        print(f"  wrote    sheet  all            {len(drawn)} schematics, same scale  {sheet.relative_to(REPO) if sheet.is_relative_to(REPO) else sheet}")
    return 1 if any(r[2] == "FAILED" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
