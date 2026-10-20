"""Power Pours + GND stitching for KiCad 10.

1. Power pours: for each power net (Net Setup classes NS_<V>V_<I>A with I >= MIN_AMPS, or nets you pick),
   pads are clustered (regulator pin + caps + diode...) and each cluster gets a copper zone around it.
   Higher current = higher priority. Big power pads (regulator tabs) get solid zone connection for heat.
2. GND pour on every copper layer, lowest priority.
3. Stitching: GND via grid wherever GND copper is solid on all layers, clear of pads/holes/parts.

Everything generated is tagged (zone names PP_*, via group PP_STITCH): rerunning replaces it.
Toolbar button in PCB editor, or CLI:
  python power_pours.py board.kicad_pcb [--nets "+24V,3V3"] [--pitch 5] [--no-stitch]
"""
import math, re
import pcbnew

TAG = "PP_"
GROUP = "PP_STITCH"
MIN_AMPS = 1.0            # Net Setup classes at/above this get pours
CLUSTER_MM = 5.0          # pads of a net closer than this share one pour
MAX_FOREIGN = 2.0         # skip a pour covering more than this many other-net pads per own pad
MAX_ASPECT = 6.0          # ...or one this long and thin (a row of parts, not a regulator area)
MARGIN_MM = 1.5           # pour extends this far beyond the pads
BIG_PAD_MM2 = 4.0         # SMD power pads this big get solid (no thermal) connection: heat path
mm, MM = pcbnew.ToMM, pcbnew.FromMM


def power_nets(board):
    """{net: amps} from Net Setup netclasses (NS_24V_3A); GND-ish nets excluded (they get the plane)."""
    out = {}
    for net in board.GetNetsByName().values():
        name = net.GetNetname()
        m = re.match(r"NS_(-?[\d.]+)V_([\d.]+)A$", net.GetNetClassName())
        if m and float(m.group(2)) >= MIN_AMPS and not is_gnd(name):
            out[name] = float(m.group(2))
    return out


def is_gnd(name):
    return bool(re.match(r"^(GND|VSS|AGND|DGND|PGND)\b", name.upper()))


def pad_box(p, margin):
    b = p.GetBoundingBox()
    m = MM(margin)
    return (b.GetLeft() - m, b.GetTop() - m, b.GetRight() + m, b.GetBottom() + m)


def box_gap(a, b):
    dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def clusters(pads, dist_nm):
    """Single-linkage clustering on pad boxes."""
    boxes = [pad_box(p, 0) for p in pads]
    group = list(range(len(pads)))

    def root(i):
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i
    for i in range(len(pads)):
        for j in range(i + 1, len(pads)):
            if box_gap(boxes[i], boxes[j]) <= dist_nm:
                group[root(i)] = root(j)
    out = {}
    for i, p in enumerate(pads):
        out.setdefault(root(i), []).append(p)
    return list(out.values())


def hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def seg_dist(p, a, b):
    ax, ay, bx, by, px, py = a.x, a.y, b.x, b.y, p[0], p[1]
    dx, dy = bx - ax, by - ay
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / float(dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def rescue_via(board, pad, layer, copper, zones_by_net, group):
    """Via + stub from `pad` down to its own net's zone on another layer. True if placed."""
    if not any(z.IsOnLayer(l) for z in zones_by_net.get(pad.GetNetCode(), ()) for l in copper if l != layer):
        return False  # no plane on another layer to drop to
    nc = board.GetDesignSettings().m_NetSettings.GetEffectiveNetClass(pad.GetNetname())
    dia, drill, width = nc.GetViaDiameter(), nc.GetViaDrill(), nc.GetTrackWidth()
    clr = max(nc.GetClearance(), MM(0.2))
    net = pad.GetNetCode()

    def free(x, y, r, layers):
        """No other-net copper within r + clearance of (x, y) on any of `layers`."""
        need = r + clr
        for p in board.GetPads():
            if p.GetNetCode() != net and any(p.IsOnLayer(l) for l in layers):
                b = p.GetBoundingBox()
                if b.GetLeft() - need < x < b.GetRight() + need and b.GetTop() - need < y < b.GetBottom() + need:
                    return False
        for t in board.GetTracks():
            if t.GetNetCode() == net:
                continue
            if t.GetClass() == "PCB_VIA":
                if math.hypot(t.GetPosition().x - x, t.GetPosition().y - y) < need + t.GetWidth(pcbnew.F_Cu) / 2:
                    return False
            elif t.GetLayer() in layers and seg_dist((x, y), t.GetStart(), t.GetEnd()) < need + t.GetWidth() / 2:
                return False
        return True

    b = pad.GetBoundingBox()
    cx, cy = pad.GetPosition().x, pad.GetPosition().y
    reach = max(b.GetWidth(), b.GetHeight()) // 2 + dia // 2 + MM(0.3)
    edge = board.GetBoardEdgesBoundingBox()
    for ang in range(0, 360, 45):
        x = cx + int(reach * math.cos(math.radians(ang)))
        y = cy + int(reach * math.sin(math.radians(ang)))
        if not (edge.GetLeft() + dia < x < edge.GetRight() - dia and edge.GetTop() + dia < y < edge.GetBottom() - dia):
            continue
        if not free(x, y, dia / 2, copper):
            continue
        n = max(2, int(math.hypot(x - cx, y - cy) / (width / 4)))  # dense samples, ends included
        if not all(free(cx + (x - cx) * k / n, cy + (y - cy) * k / n, width / 2, [layer]) for k in range(n + 1)):
            continue  # stub pad -> via would hit other-net copper
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(x, y))
        v.SetWidth(dia)
        v.SetDrill(drill)
        v.SetNet(pad.GetNet())
        board.Add(v)
        group.AddItem(v)
        s = pcbnew.PCB_TRACK(board)
        s.SetStart(pad.GetPosition())
        s.SetEnd(pcbnew.VECTOR2I(x, y))
        s.SetWidth(width)  # netclass width: Net Setup DRC rule applies to the stub too
        s.SetLayer(layer)
        s.SetNet(pad.GetNet())
        board.Add(s)
        group.AddItem(s)
        return True
    return False


def in_poly(pt, poly):
    x, y = pt.x, pt.y
    c = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            c = not c
    return c


def add_zone(board, net, layer, poly, name, priority, spoke_mm):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNetCode(net.GetNetCode())
    o = z.Outline()
    o.NewOutline()
    for x, y in poly:
        o.Append(int(x), int(y))
    z.SetZoneName(name)
    z.SetAssignedPriority(priority)
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetThermalReliefSpokeWidth(MM(spoke_mm))
    z.SetThermalReliefGap(MM(0.5))
    z.SetMinThickness(MM(0.25))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    board.Add(z)
    return z


def clear_generated(board):
    for z in list(board.Zones()):
        if z.GetZoneName().startswith(TAG):
            board.Delete(z)
    for g in list(board.Groups()):
        if g.GetName() == GROUP:
            for it in list(g.GetItems()):
                board.Delete(it)
            board.Delete(g)


def make_pours(board, nets):
    """nets: {name: amps}. Returns (zones, warnings)."""
    copper = [l for l in board.GetEnabledLayers().CuStack()]
    zones, warn = [], []
    edge = board.GetBoardEdgesBoundingBox()
    by_net = {}
    for p in board.GetPads():
        by_net.setdefault(p.GetNetname(), []).append(p)

    # existing (user/Eagle) zones per layer, and which pads have a trace ending on them
    zone_nets = {}
    for z in board.Zones():
        for l in copper:
            if z.IsOnLayer(l) and not z.GetIsRuleArea():
                zone_nets.setdefault(l, set()).add(z.GetNetCode())
    ends = {}
    for t in board.GetTracks():
        if t.GetClass() == "PCB_TRACK" or t.GetClass() == "PCB_ARC":
            for pt in (t.GetStart(), t.GetEnd()):
                ends.setdefault((t.GetNetCode(), t.GetLayer()), []).append(pt)
        elif t.GetClass() == "PCB_VIA":
            for l in copper:
                ends.setdefault((t.GetNetCode(), l), []).append(t.GetPosition())

    def traced(p):
        b = p.GetBoundingBox()
        return any(b.Contains(pt) for l in copper if p.IsOnLayer(l) for pt in ends.get((p.GetNetCode(), l), ()))

    def touches(p, poly):
        b = p.GetBoundingBox()
        g = MM(0.5)  # zone clearance: copper this close still gets pushed away
        return any(in_poly(pcbnew.VECTOR2I(x, y), poly) for x in (b.GetLeft() - g, b.GetRight() + g)
                   for y in (b.GetTop() - g, b.GetBottom() + g)) or in_poly(p.GetPosition(), poly)

    zones_by_net = {}
    for z in board.Zones():
        if not z.GetIsRuleArea():
            zones_by_net.setdefault(z.GetNetCode(), []).append(z)
    group = pcbnew.PCB_GROUP(board)
    group.SetName(GROUP)
    board.Add(group)
    has_via = set()

    def pad_key(p):
        return (p.GetParentFootprint().GetReference(), p.GetNumber())

    # power pours above everything already on the board (Eagle zones etc.)
    base = 10 + max([z.GetAssignedPriority() for z in board.Zones()] or [0])
    for rank, (name, amps) in enumerate(sorted(nets.items(), key=lambda kv: kv[1])):
        net = board.FindNet(name)
        nc = board.GetDesignSettings().m_NetSettings.GetEffectiveNetClass(name)
        spoke = max(0.3, min(1.0, mm(nc.GetTrackWidth()) / 2))   # 4 spokes ~ 2x track width of copper
        k = 0
        for cl in clusters(by_net.get(name, []), MM(CLUSTER_MM)):
            if len(cl) < 2:
                continue  # lone pad: a trace does the job
            on_b = sum(1 for p in cl if not p.IsOnLayer(pcbnew.F_Cu))
            layer = pcbnew.B_Cu if on_b > len(cl) / 2 else pcbnew.F_Cu
            pts = []
            for p in cl:
                x0, y0, x1, y1 = pad_box(p, MARGIN_MM)
                pts += [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            poly = hull(pts)
            # a pour that swallows a row of other parts gets chopped into islands: skip it
            inside = [p for p in board.GetPads() if p.IsOnLayer(layer) and in_poly(p.GetPosition(), poly)]
            foreign = [p for p in inside if p.GetNetname() != name]
            w = max(x for x, _ in poly) - min(x for x, _ in poly)
            h = max(y for _, y in poly) - min(y for _, y in poly)
            if len(foreign) > MAX_FOREIGN * len(cl) or max(w, h) > MAX_ASPECT * min(w, h):
                warn.append("%s: skipped pour near %s (%d other-net pads, %.0fx%.0f mm; route as a wide trace)"
                            % (name, cl[0].GetParentFootprint().GetReference(), len(foreign), mm(w), mm(h)))
                continue
            # pads fed only by another net's zone (no trace) would be cut off by this higher-priority pour
            cut = [p for p in board.GetPads() if p.IsOnLayer(layer) and p.GetNetCode() in zone_nets.get(layer, ())
                   and p.GetNetname() != name and touches(p, poly) and not traced(p)
                   and pad_key(p) not in has_via]
            # give each such pad its own via down to its plane on another layer; skip the pour if one can't
            failed = []
            for p in cut:
                if rescue_via(board, p, layer, copper, zones_by_net, group):
                    has_via.add(pad_key(p))  # a pad under two pours needs only one via
                else:
                    failed.append(p)
            if failed:
                warn.append("%s: skipped pour near %s - no room for a plane via at %s"
                            % (name, cl[0].GetParentFootprint().GetReference(),
                               ", ".join("%s.%s" % (p.GetParentFootprint().GetReference(), p.GetNumber()) for p in failed[:4])))
                continue
            k += 1
            zones.append(add_zone(board, net, layer, poly, "%s%s_%d" % (TAG, name, k), base + rank, spoke))
            for p in cl:
                s = p.GetSize(pcbnew.F_Cu)
                if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and mm(s.x) * mm(s.y) >= BIG_PAD_MM2:
                    p.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)  # heat path into the pour

    # GND plane on every copper layer, lowest priority
    gnd = [n for n in board.GetNetsByName().values() if is_gnd(n.GetNetname()) and n.GetNetname() in by_net]
    if gnd:
        g = max(gnd, key=lambda n: len(by_net[n.GetNetname()]))  # main GND = most pads
        rect = [(edge.GetLeft(), edge.GetTop()), (edge.GetRight(), edge.GetTop()),
                (edge.GetRight(), edge.GetBottom()), (edge.GetLeft(), edge.GetBottom())]
        mine = {l for z in board.Zones() if not z.GetZoneName().startswith(TAG)
                and z.GetNetCode() == g.GetNetCode() for l in copper if z.IsOnLayer(l)}
        for layer in copper:
            if layer in mine:
                warn.append("GND pour on %s already exists (yours) - kept, not added" % board.GetLayerName(layer))
                continue
            zones.append(add_zone(board, g, layer, rect, "%sGND_%s" % (TAG, board.GetLayerName(layer)), 0, 0.5))

    # regulator / power-part tabs left unconnected = no heatsinking
    for fp in board.GetFootprints():
        big = [p for p in fp.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and not p.GetNetname()
               and mm(p.GetSize(pcbnew.F_Cu).x) * mm(p.GetSize(pcbnew.F_Cu).y) >= BIG_PAD_MM2]
        for p in big:
            warn.append("%s pad %s (%.0f mm2 tab) has no net: no heatsinking. Connect it in the schematic "
                        "(LM2596/LM78xx tab = GND/pin 3)." % (fp.GetReference(), p.GetNumber(),
                        mm(p.GetSize(pcbnew.F_Cu).x) * mm(p.GetSize(pcbnew.F_Cu).y)))
    if has_via:
        warn.insert(0, "added %d plane vias so pads under the pours keep their zone connection" % len(has_via))
    return zones, warn


def stitch(board, pitch_mm=5.0, avoid_parts=True):
    """GND via grid where every copper layer has solid GND fill around the via."""
    gnd_zones = [z for z in board.Zones() if is_gnd(z.GetNetname()) and not z.GetIsRuleArea()]
    if not gnd_zones:
        return 0
    net = gnd_zones[0].GetNet()
    copper = list(board.GetEnabledLayers().CuStack())
    fills = {l: [z.GetFilledPolysList(l) for z in gnd_zones if z.IsOnLayer(l) and z.GetNetCode() == net.GetNetCode()]
             for l in copper}
    if not all(fills.values()):
        return 0
    ds = board.GetDesignSettings()
    nc = ds.m_NetSettings.GetEffectiveNetClass(net.GetNetname())  # Net Setup DRC rules apply to these vias too
    dia, drill = nc.GetViaDiameter(), nc.GetViaDrill()
    r = dia // 2 + MM(0.05)
    hole_gap = max(ds.m_HoleToHoleMin, MM(0.25))
    keep = []  # (x, y, radius) of holes and pads to stay away from
    for p in board.GetPads():
        b = p.GetBoundingBox()
        keep.append((b.GetLeft(), b.GetTop(), b.GetRight(), b.GetBottom()))
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            b = t.GetBoundingBox()
            keep.append((b.GetLeft() - hole_gap, b.GetTop() - hole_gap, b.GetRight() + hole_gap, b.GetBottom() + hole_gap))
    if avoid_parts:
        for fp in board.GetFootprints():
            b = fp.GetBoundingBox(False, False)
            keep.append((b.GetLeft(), b.GetTop(), b.GetRight(), b.GetBottom()))
    clr = MM(0.2)

    def blocked(x, y):
        for x0, y0, x1, y1 in keep:
            if x0 - r - clr < x < x1 + r + clr and y0 - r - clr < y < y1 + r + clr:
                return True
        return False

    ring = [(math.cos(a) * r, math.sin(a) * r) for a in [i * math.pi / 4 for i in range(8)]] + [(0, 0)]

    def solid(x, y):
        return all(any(all(f.Contains(pcbnew.VECTOR2I(int(x + dx), int(y + dy))) for dx, dy in ring) for f in fl)
                   for fl in fills.values())

    e = board.GetBoardEdgesBoundingBox()
    step = MM(pitch_mm)
    group = pcbnew.PCB_GROUP(board)
    group.SetName(GROUP)
    board.Add(group)
    n = 0
    y = e.GetTop() + step // 2
    while y < e.GetBottom():
        x = e.GetLeft() + step // 2
        while x < e.GetRight():
            if not blocked(x, y) and solid(x, y):
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
                v.SetWidth(dia)
                v.SetDrill(drill)
                v.SetNet(net)
                board.Add(v)
                group.AddItem(v)
                n += 1
            x += step
        y += step
    return n


def run(board, nets=None, pitch_mm=5.0, do_stitch=True):
    clear_generated(board)
    nets = nets if nets is not None else power_nets(board)
    zones, warn = make_pours(board, nets)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    vias = stitch(board, pitch_mm) if do_stitch else 0
    if vias:
        filler.Fill(board.Zones())  # vias join the GND fill
    report = []
    for z in zones:
        report.append("%-20s %-6s %6.0f mm2" % (z.GetZoneName(), board.GetLayerName(z.GetLayer()),
                                                z.CalculateFilledArea() / 1e12))
    return report, vias, warn


class PowerPours(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Power Pours + GND Stitching"
        self.category = "Routing"
        self.description = "Pour copper around power-net pads (regulators), GND planes, stitching vias"
        self.show_toolbar_button = True

    def Run(self):
        import wx
        board = pcbnew.GetBoard()
        found = power_nets(board)
        names = sorted({n.GetNetname() for n in board.GetNetsByName().values() if n.GetNetname()
                        and not is_gnd(n.GetNetname())}, key=lambda n: (n not in found, n))
        dlg = wx.Dialog(None, title="Power Pours", size=(420, 560), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(dlg, label="Nets to pour (pre-ticked: Net Setup classes >= %g A)" % MIN_AMPS), 0, wx.ALL, 6)
        lst = wx.CheckListBox(dlg, choices=names)
        for i, n in enumerate(names):
            lst.Check(i, n in found)
        s.Add(lst, 1, wx.EXPAND | wx.ALL, 6)
        row = wx.BoxSizer(wx.HORIZONTAL)
        st = wx.CheckBox(dlg, label="GND stitching vias, pitch mm:")
        st.SetValue(True)
        pitch = wx.TextCtrl(dlg, value="5", size=(50, -1))
        row.Add(st, 0, wx.ALIGN_CENTER_VERTICAL)
        row.Add(pitch, 0, wx.LEFT, 4)
        s.Add(row, 0, wx.ALL, 6)
        s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizer(s)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        chosen = {names[i]: found.get(names[i], MIN_AMPS) for i in lst.GetCheckedItems()}
        try:
            p = float(pitch.GetValue())
        except ValueError:
            p = 5.0
        do_st = st.GetValue()
        dlg.Destroy()
        report, vias, warn = run(board, chosen, p, do_st)
        pcbnew.Refresh()
        wx.MessageBox("\n".join(report) + "\n\nStitching vias: %d" % vias +
                      ("\n\nWARNINGS:\n" + "\n".join(warn) if warn else ""), "Power Pours")


def _selfcheck():
    assert hull([(0, 0), (2, 0), (1, 1), (2, 2), (0, 2)]) == [(0, 0), (2, 0), (2, 2), (0, 2)]
    assert box_gap((0, 0, 1, 1), (4, 0, 5, 1)) == 3 and box_gap((0, 0, 2, 2), (1, 1, 3, 3)) == 0
    assert is_gnd("GND") and is_gnd("GND.2") and not is_gnd("+24V")
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert in_poly(pcbnew.VECTOR2I(5, 5), sq) and not in_poly(pcbnew.VECTOR2I(15, 5), sq)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("board", nargs="?")
    ap.add_argument("-o", "--out")
    ap.add_argument("--nets", help="comma list; default = Net Setup classes >= %g A" % MIN_AMPS)
    ap.add_argument("--pitch", type=float, default=5.0)
    ap.add_argument("--no-stitch", action="store_true")
    a = ap.parse_args()
    _selfcheck()
    if not a.board:
        print("selfcheck ok")
        raise SystemExit
    b = pcbnew.LoadBoard(a.board)
    nets = {n: MIN_AMPS for n in a.nets.split(",")} if a.nets else None
    report, vias, warn = run(b, nets, a.pitch, not a.no_stitch)
    pcbnew.SaveBoard(a.out or a.board, b)
    print("\n".join(report))
    print("stitching vias:", vias)
    for w in warn:
        print("WARNING:", w)
