"""Net Setup for KiCad 10: tell it what each net carries (voltage, current), it sets
trace width, clearance and via size - for the router (netclasses) AND for DRC (.kicad_dru rules).

Width:     IPC-2221 current formula  I = k * dT^0.44 * A^0.725  (k 0.048 outer, 0.024 inner)
Clearance: IPC-2221 table 6-1, B2 (external, uncoated, < 3050 m)
Vias:      largest standard via that fits the trace; barrel = 25 um plating, inner-layer k (conservative)

Toolbar button in PCB editor, or CLI (for scripting / testing):
  python net_setup.py board.kicad_pcb --set "+24V=24:3" --set "GND=0:3" [--oz 1] [--rise 10]
"""
import json, math, os, re
import pcbnew

PREFIX = "NS_"                      # classes this plugin owns; anything else is left alone
DRU_BEGIN, DRU_END = "# BEGIN net_setup (generated, edits here are overwritten)", "# END net_setup"
VIAS = [(0.3, 0.6), (0.4, 0.8), (0.5, 1.0), (0.6, 1.2), (0.8, 1.6), (1.0, 2.0)]  # (drill, diameter) mm
PLATING_MM = 0.025
# IPC-2221 table 6-1 B2: (max volts, clearance mm); above 500 V: 0.005 mm/V
B2 = [(15, 0.1), (30, 0.1), (50, 0.6), (100, 0.6), (150, 0.6), (170, 1.25), (250, 1.25), (300, 1.25), (500, 2.5)]
SIGNAL_A = 0.3                      # at or below this (and <= 30 V) a net stays in Default


def ipc2221_area_mil2(amps, rise_c, k):
    return (amps / (k * rise_c ** 0.44)) ** (1 / 0.725)


def track_width_mm(amps, rise_c=10, oz=1.0, outer=True):
    area = ipc2221_area_mil2(amps, rise_c, 0.048 if outer else 0.024)
    return area / (1.378 * oz) * 0.0254


def via_amps(drill_mm, rise_c=10):
    t = PLATING_MM / 0.0254                       # mil
    area = math.pi * (drill_mm / 0.0254 + t) * t  # barrel ring cross-section, mil^2
    return 0.024 * rise_c ** 0.44 * area ** 0.725


def clearance_mm(volts):
    v = abs(volts)
    for vmax, c in B2:
        if v <= vmax:
            return c
    return 2.5 + 0.005 * (v - 500)


def up(x, step=0.05):
    return round(math.ceil(round(x / step, 6)) * step, 3)


def compute(volts, amps, rise_c, oz_outer, oz_inner, mins):
    """-> dict(width, clearance, via_d, via_drill, vias) in mm; width covers outer AND inner layers."""
    w = max(track_width_mm(amps, rise_c, oz_outer, True), track_width_mm(amps, rise_c, oz_inner, False)
            if mins["inner_layers"] else 0, mins["width"])
    w = up(w)
    fits = [v for v in VIAS if v[0] >= mins["drill"] and v[1] >= mins["via"] and v[1] <= max(w, VIAS[0][1])]
    drill, dia = fits[-1] if fits else max(VIAS[0], (mins["drill"], mins["via"]))
    return {"width": w, "clearance": max(up(clearance_mm(volts)), mins["clearance"]),
            "via_d": dia, "via_drill": drill, "vias": max(1, math.ceil(amps / via_amps(drill, rise_c)))}


def class_name(volts, amps):
    return "%s%gV_%gA" % (PREFIX, volts, amps)


def guess(net):
    """Starting values from the net name: '+24V', '3V3', '4V-OUT', 'GND', 'VBAT'..."""
    n = net.upper()
    if re.search(r"GND|VSS", n):
        return 0.0, 1.0
    m = re.search(r"(\d+)V(\d*)", n)
    if m:
        return float(m.group(1) + ("." + m.group(2) if m.group(2) else "")), 1.0
    return 3.3, 0.1


def board_mins(board):
    ds = board.GetDesignSettings()
    d = ds.m_NetSettings.GetDefaultNetclass()
    mm = pcbnew.ToMM
    return {"width": max(mm(ds.m_TrackMinWidth), mm(d.GetTrackWidth())),
            "clearance": max(mm(ds.m_MinClearance), mm(d.GetClearance())),
            "via": mm(ds.m_ViasMinSize), "drill": mm(ds.m_MinThroughDrill),
            "inner_layers": board.GetCopperLayerCount() > 2}


def paths(board):
    base = os.path.splitext(board.GetFileName())[0]
    return base + ".kicad_pro", base + ".kicad_dru", base + ".netsetup.json"


def apply(board, settings, rise_c=10, oz_outer=1.0, oz_inner=0.5):
    """settings: {net: (volts, amps)}. Writes netclasses + DRC rules. Returns {class: (params, [nets])}."""
    mins = board_mins(board)
    classes = {}
    for net, (v, a) in settings.items():
        if a <= SIGNAL_A and abs(v) <= 30:
            continue
        classes.setdefault(class_name(v, a), (compute(v, a, rise_c, oz_outer, oz_inner, mins), []))[1].append(net)

    pro, dru, store = paths(board)
    ns = board.GetDesignSettings().m_NetSettings
    # netclasses: keep the user's, replace ours
    keep = {str(k): c for k, c in ns.GetNetclasses().items() if not str(k).startswith(PREFIX)}
    ns.ClearNetclasses()
    for k, c in keep.items():
        ns.SetNetclass(k, c)
    F = pcbnew.FromMM
    for name, (p, _) in classes.items():
        nc = pcbnew.NETCLASS(name)
        nc.SetTrackWidth(F(p["width"])); nc.SetClearance(F(p["clearance"]))
        nc.SetViaDiameter(F(p["via_d"])); nc.SetViaDrill(F(p["via_drill"]))
        nc.SetDescription("net_setup: %d vias per layer change" % p["vias"])
        ns.SetNetclass(name, nc)
    # pattern assignments are not readable from Python: rebuild from the saved project, minus ours
    # ponytail: unsaved pattern edits made in the GUI this session are lost; save the project first
    user_patterns = []
    if os.path.exists(pro):
        for e in json.load(open(pro, encoding="utf-8")).get("net_settings", {}).get("netclass_patterns") or []:
            if not e["netclass"].startswith(PREFIX):
                user_patterns.append((e["pattern"], e["netclass"]))
    ns.ClearNetclassPatternAssignments()
    for pat, cls in user_patterns:
        ns.SetNetclassPatternAssignment(pat, cls)
    for name, (_, nets) in classes.items():
        for net in nets:
            ns.SetNetclassPatternAssignment(re.sub(r"([*?\[\]])", r"[\1]", net), name)  # literal match
    ns.RecomputeEffectiveNetclasses()
    board.SynchronizeNetsAndNetClasses(True)
    add_predefined_sizes(board.GetDesignSettings(), classes)

    write_dru(dru, classes)
    json.dump({"rise_c": rise_c, "oz_outer": oz_outer, "oz_inner": oz_inner,
               "nets": {k: list(v) for k, v in settings.items()}}, open(store, "w"), indent=1)
    return classes


def add_predefined_sizes(ds, classes):
    """Computed widths/vias into the toolbar dropdowns (entry 0 = 'use netclass' placeholder)."""
    F = pcbnew.FromMM
    tw = list(ds.m_TrackWidthList)
    if not tw or tw[0] != 0:
        tw.insert(0, 0)
    for p, _ in classes.values():
        if F(p["width"]) not in tw:
            tw.append(F(p["width"]))
    ds.m_TrackWidthList = pcbnew.intVector([tw[0]] + sorted(tw[1:]))
    vias = [(v.m_Diameter, v.m_Drill) for v in ds.m_ViasDimensionsList]
    if not vias or vias[0] != (0, 0):
        vias.insert(0, (0, 0))
    for p, _ in classes.values():
        v = (F(p["via_d"]), F(p["via_drill"]))
        if v not in vias:
            vias.append(v)
    out = pcbnew.VIA_DIMENSION_Vector()
    for d, dr in [vias[0]] + sorted(vias[1:]):
        out.append(pcbnew.VIA_DIMENSION(d, dr))
    ds.m_ViasDimensionsList = out


def too_thin(board, classes):
    """{class: n existing traces narrower than its width} - what DRC will flag."""
    out = {}
    for name, (p, nets) in classes.items():
        w = pcbnew.FromMM(p["width"])
        n = sum(1 for t in board.GetTracks() if t.GetClass() in ("PCB_TRACK", "PCB_ARC")
                and t.GetNetname() in nets and t.GetWidth() < w)
        out[name] = n
    return out


def write_dru(path, classes):
    """Netclass width/via are only router defaults; these rules make DRC enforce them."""
    rules = [DRU_BEGIN]
    for name, (p, nets) in sorted(classes.items()):
        rules.append('(rule "%s min width"\n  (condition "A.NetClass == \'%s\'")\n'
                     '  (constraint track_width (min %.3fmm)))' % (name, name, p["width"]))
        rules.append('(rule "%s via"\n  (condition "A.NetClass == \'%s\' && A.Type == \'Via\'")\n'
                     '  (constraint via_diameter (min %.3fmm))\n  (constraint hole_size (min %.3fmm)))'
                     % (name, name, p["via_d"], p["via_drill"]))
        rules.append('(rule "%s clearance"\n  (condition "A.NetClass == \'%s\'")\n'
                     '  (constraint clearance (min %.3fmm)))' % (name, name, p["clearance"]))
    rules.append(DRU_END)
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else "(version 1)\n"
    old = re.sub(re.escape(DRU_BEGIN) + ".*?" + re.escape(DRU_END) + r"\n?", "", old, flags=re.S)
    if "(version" not in old:
        old = "(version 1)\n" + old
    open(path, "w", encoding="utf-8").write(old.rstrip() + "\n\n" + "\n".join(rules) + "\n")


def load_settings(board):
    store = paths(board)[2]
    saved = json.load(open(store)) if os.path.exists(store) else {"nets": {}}
    nets = {}
    for n in sorted(board.GetNetsByName().keys(), key=str):
        n = str(n)
        if n:
            nets[n] = tuple(saved["nets"].get(n, guess(n)))
    return saved, nets


# ----------------------------------------------------------------------------- GUI
class NetSetup(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Net Setup (V / A -> width, clearance, vias)"
        self.category = "Routing"
        self.description = "Enter voltage and current per net; sets netclasses and DRC rules"
        self.show_toolbar_button = True

    def Run(self):
        import wx
        import wx.grid
        board = pcbnew.GetBoard()
        saved, nets = load_settings(board)
        mins = board_mins(board)
        pads = {}
        for p in board.GetPads():
            pads[p.GetNetname()] = pads.get(p.GetNetname(), 0) + 1
        auto = re.compile(r"^(N\$\d+|Net-\(|unconnected-)", re.I)
        names = sorted(nets, key=lambda n: (bool(auto.match(n)), -nets[n][1], n))

        dlg = wx.Dialog(None, title="Net Setup", size=(900, 650), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        top = wx.BoxSizer(wx.HORIZONTAL)
        ctl = {}
        for key, label, val in (("rise_c", "Temp rise C", saved.get("rise_c", 10)),
                                ("oz_outer", "Outer oz", saved.get("oz_outer", 1.0)),
                                ("oz_inner", "Inner oz", saved.get("oz_inner", 0.5))):
            top.Add(wx.StaticText(dlg, label=label), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 4)
            ctl[key] = wx.TextCtrl(dlg, value=str(val), size=(50, -1))
            top.Add(ctl[key], 0, wx.ALL, 4)
        top.Add(wx.StaticText(dlg, label="  Nets <= %.1f A and <= 30 V stay Default" % SIGNAL_A), 0, wx.ALIGN_CENTER_VERTICAL)
        cols = ["Net", "Pads", "Voltage V", "Current A", "Width mm", "Clear mm", "Via mm", "Vias"]
        g = wx.grid.Grid(dlg)
        g.CreateGrid(len(names), len(cols))
        for i, c in enumerate(cols):
            g.SetColLabelValue(i, c)
        g.SetRowLabelSize(0)

        def num(key, dflt):
            try:
                return float(ctl[key].GetValue())
            except ValueError:
                return dflt

        def refresh(row):
            try:
                v, a = float(g.GetCellValue(row, 2)), float(g.GetCellValue(row, 3))
            except ValueError:
                return
            if a <= SIGNAL_A and abs(v) <= 30:
                vals = ["Default"] + [""] * 3
            else:
                p = compute(v, a, num("rise_c", 10), num("oz_outer", 1), num("oz_inner", 0.5), mins)
                vals = ["%.2f" % p["width"], "%.2f" % p["clearance"], "%.1f/%.1f" % (p["via_d"], p["via_drill"]), str(p["vias"])]
            for c, val in enumerate(vals, 4):
                g.SetCellValue(row, c, val)

        for r, n in enumerate(names):
            for c, val in enumerate([n, str(pads.get(n, 0)), "%g" % nets[n][0], "%g" % nets[n][1]]):
                g.SetCellValue(r, c, val)
            for c in (0, 1, 4, 5, 6, 7):
                g.SetReadOnly(r, c)
            refresh(r)
        g.AutoSizeColumns()
        g.Bind(wx.grid.EVT_GRID_CELL_CHANGED, lambda e: (refresh(e.GetRow()), e.Skip()))
        for t in ctl.values():
            t.Bind(wx.EVT_TEXT, lambda e: ([refresh(r) for r in range(len(names))], e.Skip()))

        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(top, 0, wx.ALL, 4)
        s.Add(g, 1, wx.EXPAND | wx.ALL, 4)
        s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizer(s)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        settings = {}
        for r, n in enumerate(names):
            try:
                settings[n] = (float(g.GetCellValue(r, 2)), float(g.GetCellValue(r, 3)))
            except ValueError:
                settings[n] = nets[n]
        classes = apply(board, settings, num("rise_c", 10), num("oz_outer", 1), num("oz_inner", 0.5))
        dlg.Destroy()
        pcbnew.Refresh()
        thin = too_thin(board, classes)
        lines = []
        for k, (p, n) in sorted(classes.items()):
            lines.append("%s  ->  %.2f mm track, clearance %.2f, via %.1f/%.1f (x%d per layer change)\n      nets: %s\n"
                         "      existing traces too thin: %s"
                         % (k, p["width"], p["clearance"], p["via_d"], p["via_drill"], p["vias"], ", ".join(n),
                            ("%d  (DRC will flag them - use Power Trace to widen)" % thin[k]) if thin[k] else
                            "none - your current traces already meet this"))
        msg = "\n".join(lines) if lines else "Every net is at signal level (<= %.1f A, <= 30 V): all stay in Default." % SIGNAL_A
        wx.MessageBox(msg + "\n\nDone:\n - netclasses created and nets assigned (Board Setup > Net Classes)\n"
                      " - widths/vias added to the track & via dropdowns on the toolbar\n"
                      " - DRC rules written to %s\n\nPress X to route: 'use netclass width' picks the right size per net."
                      "\nSave the board to keep it." % os.path.basename(paths(board)[1]), "Net Setup")


def _selfcheck():
    # IPC-2221 reference points (external, 1 oz, 10 C): 1 A ~ 0.30 mm, 3 A ~ 1.37 mm
    assert abs(track_width_mm(1) - 0.30) < 0.02, track_width_mm(1)
    assert abs(track_width_mm(3) - 1.37) < 0.03, track_width_mm(3)
    assert track_width_mm(1, outer=False) > 2 * track_width_mm(1)       # inner layers need ~2.6x
    assert track_width_mm(1, oz=2) < track_width_mm(1)
    assert clearance_mm(24) == 0.1 and clearance_mm(48) == 0.6 and clearance_mm(230) == 1.25
    assert abs(clearance_mm(600) - 3.0) < 1e-9
    assert guess("+24V") == (24.0, 1.0) and guess("3V3") == (3.3, 1.0) and guess("GND.2")[0] == 0
    mins = {"width": 0.2, "clearance": 0.2, "via": 0.5, "drill": 0.3, "inner_layers": False}
    p = compute(24, 3, 10, 1, 0.5, mins)
    assert p["width"] == 1.4 and p["via_d"] == 1.2 and p["vias"] * via_amps(p["via_drill"]) >= 3, p
    assert compute(5, 0.5, 10, 1, 0.5, mins)["width"] == 0.2           # never below board minimum


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("board", nargs="?")
    ap.add_argument("--set", action="append", default=[], help='NET=VOLTS:AMPS, e.g. "+24V=24:3"')
    ap.add_argument("--rise", type=float, default=10)
    ap.add_argument("--oz", type=float, default=1.0)
    ap.add_argument("--oz-inner", type=float, default=0.5)
    a = ap.parse_args()
    _selfcheck()
    if not a.board:
        print("selfcheck ok")
        raise SystemExit
    b = pcbnew.LoadBoard(a.board)
    _, nets = load_settings(b)
    for s in a.set:
        n, va = s.rsplit("=", 1)
        nets[n] = tuple(float(x) for x in va.split(":"))
    classes = apply(b, nets, a.rise, a.oz, a.oz_inner)
    pcbnew.SaveBoard(a.board, b)
    for k, (p, n) in sorted(classes.items()):
        print("%-14s width %.2f  clear %.2f  via %.1f/%.1f x%d  <- %s" % (k, p["width"], p["clearance"], p["via_d"],
              p["via_drill"], p["vias"], " ".join(n)))
