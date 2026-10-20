"""Replace imported Eagle symbols with KiCad library symbols, keeping every connection.

- New symbol = KiCad library graphics, but pin NUMBERS = the Eagle pad numbers, so footprints/PCB still match.
- Placement: rotation + position putting the most new pins exactly on old pin spots; the rest get a
  short bridge wire, only if it can't touch any other net (else the part keeps its Eagle symbol).
- Mapped symbols are written to <project>_kicad.kicad_sym and registered in sym-lib-table.
- Safety net: the netlist (which pins share a net) must be identical before/after, or nothing is saved.

Usage (KiCad python): sch_swap.py project.kicad_sch [-o out.kicad_sch]
"""
import copy, math, os, re, subprocess, sys, uuid
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sexp import parse, dump, find, first, walk, prop, Q

import pcbnew  # only to locate KiCad
KICAD = os.path.abspath(os.path.join(os.path.dirname(pcbnew.__file__), "..", "..", ".."))
SYMLIB = os.path.join(KICAD, "share", "kicad", "symbols")
CLI = os.path.join(KICAD, "bin", "kicad-cli")

# Eagle symbol name (regex) -> (KiCad symbol, pin mode). Modes: num = same number, name = by pin name/alias
MAP = [
    (r"^R-(US|EU)", "Device:R", "num"),
    (r"^C-(US|EU)", "Device:C", "num"),
    (r"^CPOL", "Device:C_Polarized", "name"),
    (r"^LED$", "Device:LED", "name"),
    (r"^D$", "Device:D", "name"),
    (r"^L$", "Device:L", "num"),
    (r"^SJ$", "Jumper:SolderJumper_2_Open", "num"),
    (r"^PNP$", "Transistor_BJT:TIP42C", "name"),
    (r"^NPN$", "Transistor_BJT:TIP41C", "name"),
    (r"^OK$", "Isolator:PC817", "num"),
    (r"^LM2596", "Regulator_Switching:LM2596S-ADJ", "num"),
    (r"^ESP32.*WROOM", "RF_Module:ESP32-WROOM-32", "name"),
    (r"^M0?(\d+)$", "Connector_Generic:Conn_01x{n:02d}", "num"),
    (r"^1803565", "Connector_Generic:Conn_01x16", "num"),
    (r"^1707489", "Connector_Generic:Conn_01x08", "num"),
    (r"^1803426", "Connector_Generic:Conn_01x02", "num"),
]
# Eagle pin name/number -> KiCad pin name candidates
ALIAS = {"C": ["K", "C"], "A": ["A"], "+": ["1"], "-": ["2"], "3V3": ["VDD", "3V3"], "TX": ["TXD0/IO1"],
         "RX": ["RXD0/IO3"], "CLK": ["SCK/CLK"], "CMD": ["SCS/CMD"], "SD0": ["SDO/SD0"], "SD1": ["SDI/SD1"],
         "SD2": ["SHD/SD2"], "SD3": ["SWP/SD3"]}
GRID = 1.27
_libcache = {}


def lib_symbol(lib_id):
    """Resolved (extends) KiCad library symbol as an sexp list named lib_id."""
    lib, name = lib_id.split(":")
    if lib not in _libcache:
        _libcache[lib] = {x[1]: x for x in find(parse(open(os.path.join(SYMLIB, lib + ".kicad_sym"), encoding="utf-8").read()), "symbol")}
    syms = _libcache[lib]
    if name not in syms:
        return None
    s = copy.deepcopy(syms[name])
    ext = first(s, "extends")
    if ext:
        parent = copy.deepcopy(syms[ext[1]])
        s.remove(ext)
        for u in find(parent, "symbol"):  # parent's units, renamed to the child
            u[1] = Q(u[1].replace(ext[1], name, 1))
            s.append(u)
        for key in ("pin_names", "pin_numbers", "exclude_from_sim", "in_bom", "on_board"):
            if not first(s, key) and first(parent, key):
                s.insert(2, first(parent, key))
    s[1] = Q(lib_id)
    return s


def unit_pins(sym, unit=1):
    """[(pin_node, number, name, x, y)] of a lib symbol for one unit (+ common unit 0)."""
    out = []
    for u in find(sym, "symbol"):
        un = int(u[1].rsplit("_", 2)[-2])
        if un in (0, unit):
            for p in find(u, "pin"):
                a = first(p, "at")
                out.append((p, first(p, "number")[1], first(p, "name")[1], float(a[1]), float(a[2])))
    return out


def place(lx, ly, rot):
    """Lib (y-up) offset -> schematic offset (y-down), calibrated on imported wire ends."""
    x, y = lx, -ly
    a = math.radians(-rot)
    return round(x * math.cos(a) - y * math.sin(a), 4), round(x * math.sin(a) + y * math.cos(a), 4)


def pin_map(eagle_pins, kicad_pins, mode):
    """{eagle_number: kicad_pin_index} or None if not a clean 1:1 match."""
    m, used = {}, set()
    for _, en, ename, _, _ in eagle_pins:
        cands = []
        for i, (_, kn, kname, _, _) in enumerate(kicad_pins):
            if i in used:
                continue
            if mode == "num" and kn == en:
                cands.append(i)
            elif mode == "name":
                keys = ALIAS.get(en, []) + ALIAS.get(ename, []) + [en, ename]
                kfull = kname.upper()
                if any(k and (kfull == k.upper() or kn == k) for k in keys):
                    cands.append(i)
        if mode == "name" and not cands:  # GND1..GND5 -> a free GND pin, else stack on a used one
            base = re.sub(r"\d+$", "", en).upper()
            same = [i for i, (_, kn, kname, _, _) in enumerate(kicad_pins) if kname.upper() == base]
            cands = [i for i in same if i not in used]
            if not cands and same:
                m[en] = ("stack", same[0])  # extra Eagle pin of the same function: hidden stacked pin
                continue
        if not cands:
            return None
        m[en] = cands[0]
        used.add(cands[0])
    return m


def build_mapped(kicad, emap, eagle_pins, libname, newname):
    """KiCad symbol copy whose pin numbers are the Eagle numbers."""
    s = copy.deepcopy(kicad)
    s[1] = Q(newname)
    kp = unit_pins(s)
    taken = set(emap)
    by_idx = {i: en for en, i in emap.items() if not isinstance(i, tuple)}
    for en, i in emap.items():
        if isinstance(i, tuple):  # stacked duplicate, hidden, same spot as its twin
            node = copy.deepcopy(kp[i[1]][0])
            first(node, "number")[1] = Q(en)
            if "hide" not in node:
                node.append("hide")
            unit = next(u for u in find(s, "symbol") if any(p is kp[i[1]][0] for p in find(u, "pin")))
            unit.append(node)
    for i, (node, kn, _, _, _) in enumerate(kp):
        num = first(node, "number")
        if i in by_idx:
            num[1] = Q(by_idx[i])
        elif kn in taken:
            return None  # an extra KiCad pin would collide with an Eagle number
    for u in find(s, "symbol"):
        u[1] = Q(u[1].replace(kicad[1].split(":")[1], newname.split(":")[1], 1))
    return s


def segs_of(s):
    out = []
    for w in find(s, "wire"):
        xy = find(first(w, "pts"), "xy")
        out.append(((float(xy[0][1]), float(xy[0][2])), (float(xy[1][1]), float(xy[1][2]))))
    return out


def pt2(p):
    return (round(p[0], 2), round(p[1], 2))


def on_seg(p, a, b, tol=0.01):
    (x, y), (x0, y0), (x1, y1) = p, a, b
    if min(x0, x1) - tol <= x <= max(x0, x1) + tol and min(y0, y1) - tol <= y <= max(y0, y1) + tol:
        return abs((x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)) <= tol * max(1, math.hypot(x1 - x0, y1 - y0))
    return False


def netlist(sch):
    out = sch + ".net"
    subprocess.run([CLI, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch],
                   capture_output=True)
    n = parse(open(out, encoding="utf-8").read())
    os.remove(out)
    nets = set()
    for net in walk(n, "net"):
        nodes = frozenset((first(x, "ref")[1], first(x, "pin")[1]) for x in find(net, "node"))
        if len(nodes) > 1:
            nets.add(nodes)
    return nets


def run(src, out):
    s = parse(open(src, encoding="utf-8").read())
    ls = first(s, "lib_symbols")
    defs = {x[1]: x for x in find(ls, "symbol")}
    proj = os.path.splitext(os.path.basename(src))[0]
    libname = proj + "_kicad"
    insts = find(s, "symbol")

    # every connection point on the sheet, to keep bridge wires away from other nets
    wires = segs_of(s)
    points = defaultdict(set)  # (x,y) -> owners
    for a, b in wires:
        points[(round(a[0], 2), round(a[1], 2))].add("wire")
        points[(round(b[0], 2), round(b[1], 2))].add("wire")
    for l in find(s, "label") + find(s, "global_label") + find(s, "junction"):
        a = first(l, "at")
        points[(round(float(a[1]), 2), round(float(a[2]), 2))].add("label")
    old_pins = {}  # inst uuid -> {eagle_num: (x,y)}
    for inst in insts:
        d = defs[first(inst, "lib_id")[1]]
        at = first(inst, "at")
        X, Y, R = float(at[1]), float(at[2]), float(at[3])
        u = int(first(inst, "unit")[1])
        old_pins[id(inst)] = {n: (round(X + place(x, y, R)[0], 2), round(Y + place(x, y, R)[1], 2))
                              for _, n, _, x, y in unit_pins(d, u)}
        for p in old_pins[id(inst)].values():
            points[p].add(id(inst))

    new_defs, report, skipped = {}, defaultdict(int), defaultdict(str)
    for inst in insts:
        lib_id = first(inst, "lib_id")[1]
        ename = lib_id.split(":")[1]
        d = defs[lib_id]
        if len(find(d, "symbol")) > 2:
            skipped[ename] = "multi-unit"
            continue
        rule = next(((pat, kid, mode) for pat, kid, mode in MAP if re.search(pat, ename)), None)
        if not rule:
            skipped[ename] = "no KiCad match"
            continue
        m = re.search(rule[0], ename)
        kid = rule[1].format(n=int(m.group(1))) if "{n" in rule[1] else rule[1]
        val = str(prop(inst, "Value") or "")
        if kid.startswith("Regulator_Switching:LM2596"):
            v = re.search(r"(3\.3|5|12)\s*V", val, re.I)
            kid = "Regulator_Switching:LM2596S-" + (v.group(1) if v else "ADJ")
        newname = "%s:%s" % (libname, kid.split(":")[1])
        if newname not in new_defs:
            k = lib_symbol(kid)
            ep = unit_pins(d)
            emap = pin_map(ep, unit_pins(k), rule[2]) if k else None
            nd = build_mapped(k, emap, ep, libname, newname) if emap else None
            new_defs[newname] = nd
            if nd is None:
                skipped[ename] = "pins don't map 1:1 onto " + kid
        nd = new_defs[newname]
        if nd is None:
            continue

        # best rotation/position: most new pins exactly on old pin spots
        olds = old_pins[id(inst)]
        npins = {first(node, "number")[1]: (x, y) for node, _, _, x, y in unit_pins(nd)}
        best = None
        for rot in (0, 90, 180, 270):
            off = {n: place(x, y, rot) for n, (x, y) in npins.items()}
            for anchor in olds:
                if anchor not in off:
                    continue
                tx = round((olds[anchor][0] - off[anchor][0]) / GRID) * GRID
                ty = round((olds[anchor][1] - off[anchor][1]) / GRID) * GRID
                newp = {n: (round(tx + o[0], 2), round(ty + o[1], 2)) for n, o in off.items()}
                score = sum(1 for n in olds if n in newp and newp[n] == olds[n])
                travel = sum(math.dist(newp[n], olds[n]) for n in olds if n in newp)  # keeps pin order/polarity
                key = (score, -round(travel, 2), rot == float(first(inst, "at")[3]))
                if best is None or key > best[0]:
                    best = (key, rot, tx, ty, newp)
        _, rot, tx, ty, newp = best

        # bridge wires for pins that moved: straight or L, touching nothing of another net
        bridges = []
        mine = set(olds.values()) | set(newp.values())
        ok = True
        for n, op in olds.items():
            np_ = newp.get(n)
            if np_ == op:
                continue
            others = [p for p, own in points.items() if p not in (op, np_) and own != {id(inst)}]
            route = None
            for mid in ((np_[0], op[1]), (op[0], np_[1])):
                segs = [(op, mid), (mid, np_)] if mid not in (op, np_) else [(op, np_)]
                if any(a[0] != b[0] and a[1] != b[1] for a, b in segs):
                    continue
                if any(on_seg(p, a, b) for a, b in segs for p in others) or \
                   any(on_seg(np_, a, b) for a, b in wires if op not in (pt2(a), pt2(b))) or \
                   any(on_seg(q, a, b) for a, b in segs for q in mine if q not in (op, np_)):
                    continue
                route = segs
                break
            if route is None:
                ok = False
                break
            bridges += route
        if not ok:
            skipped[ename] = skipped[ename] or "no clean wire route for moved pins (kept Eagle symbol)"
            continue

        # commit: swap symbol, add bridges
        first(inst, "lib_id")[1] = Q(newname)
        at = first(inst, "at")
        at[1], at[2], at[3] = "%g" % round(tx, 4), "%g" % round(ty, 4), "%g" % rot
        first(inst, "unit")[1] = "1"
        for a, b in bridges:
            s.append(["wire", ["pts", ["xy", "%g" % a[0], "%g" % a[1]], ["xy", "%g" % b[0], "%g" % b[1]]],
                      ["stroke", ["width", "0"], ["type", "default"]], ["uuid", str(uuid.uuid4())]])
            wires.append((a, b))
            for p in (a, b):
                points[(round(p[0], 2), round(p[1], 2))].add("wire")
        for p in newp.values():
            points[p].add(id(inst))
        report[(ename, newname)] += 1

    # lib_symbols: add mapped defs, drop unused eagle ones
    used = {first(i, "lib_id")[1] for i in insts}
    for k in list(defs):
        if k not in used:
            ls.remove(defs[k])
    for name, nd in new_defs.items():
        if nd is not None and name in used:
            ls.append(nd)
    open(out, "w", encoding="utf-8").write(dump(s) + "\n")

    # project symbol library + sym-lib-table entry
    d = os.path.dirname(os.path.abspath(out))
    lib = ["kicad_symbol_lib", ["version", "20241209"], ["generator", Q("sch_swap")]]
    for name, nd in new_defs.items():
        if nd is not None and name in used:
            x = copy.deepcopy(nd)
            x[1] = Q(name.split(":")[1])
            lib.append(x)
    open(os.path.join(d, libname + ".kicad_sym"), "w", encoding="utf-8").write(dump(lib) + "\n")
    tbl = os.path.join(d, "sym-lib-table")
    t = open(tbl, encoding="utf-8").read() if os.path.exists(tbl) else "(sym_lib_table\n)\n"
    if '"%s"' % libname not in t:
        t = t.rstrip().rstrip(")") + '  (lib (name "%s") (type "KiCad") (uri "${KIPRJMOD}/%s.kicad_sym") (options "") (descr "KiCad symbols mapped to Eagle pad numbers"))\n)\n' % (libname, libname)
        open(tbl, "w", encoding="utf-8").write(t)
    return report, skipped


def swap_file(sch, out=None):
    """Swap symbols in `sch`, write `out` (default: in place, old file kept as .eagle_backup).
    Returns (ok, report_text). Nothing is written unless the netlist is identical."""
    import shutil
    out = out or sch
    before = netlist(sch)
    tmp = out + ".tmp.kicad_sch"
    report, skipped = run(sch, tmp)
    after = netlist(tmp)
    lines = ["%-22s -> %-40s x%d" % (e, k, n) for (e, k), n in sorted(report.items())]
    lines += ["kept %-17s (%s)" % (e, why) for e, why in sorted(skipped.items())]
    if before != after:
        os.remove(tmp)
        lost = list(before - after)
        lines.append("NETLIST CHANGED - nothing saved. %d nets differ, e.g.:" % len(lost))
        lines += ["   " + str(sorted(n)) for n in lost[:5]]
        return False, "\n".join(lines)
    if out == sch:
        shutil.copy2(sch, os.path.splitext(sch)[0] + ".eagle_backup.kicad_sch")
    shutil.move(tmp, out)
    lines.append("netlist identical (%d nets). Saved %s" % (len(after), out))
    return True, "\n".join(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("sch")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    ok, text = swap_file(a.sch, a.out)
    print(text)
    sys.exit(0 if ok else 1)
