"""Fab Rules: set the board's DRC to what the PCB fab can actually make (Lion Circuits).

Source: https://lioncircuits.com/pcb-manufacturing-capabilities (rigid PCB table, MII / Custom / Rush).
Sets Board Setup > Constraints, raises the Default netclass to at least the minimums, and writes the
few rules KiCad has no constraint box for (trace-to-pour, slot width) into the .kicad_dru.
Optionally writes the same rules into KiCad's default project template, so every NEW project gets them.

CLI: python fab_rules.py board.kicad_pcb [--service MII|Custom|Rush] [--oz 1|2|3] [--template]
"""
import json, os, re, shutil
import pcbnew

MIL = 0.0254
# per service: min trace/space by copper oz (mil), trace->pour by oz (mil), min drill (mm),
# plated hole spacing + hole->trace (mil), silk->pad (mil), mask dam (mm)
SERVICES = {
    "MII":    {"track": {1: 6}, "pour": {1: 6}, "drill": 0.35, "hole_gap": 12, "hole_trace": 12,
               "silk_pad": 6, "mask_dam": 0.152, "note": "standard / cheapest, 2 & 4 layers, 1 oz"},
    "Custom": {"track": {1: 6, 2: 10, 3: 12}, "pour": {1: 6, 2: 10, 3: 12}, "drill": 0.25, "hole_gap": 10,
               "hole_trace": 10, "silk_pad": 6, "mask_dam": 0.152, "note": "1-12 layers, 1/2/3 oz"},
    "Rush":   {"track": {1: 8, 2: 10}, "pour": {1: 8, 2: 12}, "drill": 0.4, "hole_gap": 14, "hole_trace": 14,
               "silk_pad": 8, "mask_dam": 0.254, "note": "fast turnaround, 1/2 oz"},
}
# silk_w: LC says 6 mil (0.1524); 0.15 used so KiCad's stock footprint text (0.15 mm) isn't flagged - 2 um apart
COMMON = {"annular": 0.152, "edge": 0.3, "silk_h": 0.5842, "silk_w": 0.15, "slot": 0.8}
DRU_BEGIN, DRU_END = "# BEGIN fab_rules (generated, edits here are overwritten)", "# END fab_rules"
def _template_dir():
    """KiCad's user template folder, found without hard-coded paths:
    1. KICAD_USER_TEMPLATE_DIR (KiCad sets it for plugins it runs)
    2. <KiCad docs>/<ver>/ worked out from where the plugin is installed
       (<KiCad docs>/<ver>/(scripting|3rdparty)/plugins/<pkg>/) - only if that really looks like KiCad's folder
    3. <real Documents>/KiCad/<major.minor>/template (Documents follows OneDrive/redirection)"""
    env = os.environ.get("KICAD_USER_TEMPLATE_DIR")
    if env:
        return os.path.join(env, "default")
    ver_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if any(os.path.isdir(os.path.join(ver_dir, d)) for d in ("scripting", "3rdparty", "template")):
        return os.path.join(ver_dir, "template", "default")
    try:
        from . import kpaths
    except ImportError:
        import kpaths
    ver = ".".join(pcbnew.Version().split(".")[:2])  # "10.0.5" -> "10.0"
    return os.path.join(kpaths.documents_dir(), "KiCad", ver, "template", "default")


TEMPLATE = _template_dir()


def up(x, step=0.01):
    return round(-(-round(x / step, 6) // 1) * step, 3)


def rules(service="MII", oz=1):
    s = SERVICES[service]
    if oz not in s["track"]:
        raise ValueError("%s service does not offer %d oz (has %s)" % (service, oz, sorted(s["track"])))
    t = s["track"][oz] * MIL
    return {"track": round(t, 4), "space": round(t, 4), "pour": round(s["pour"][oz] * MIL, 4),
            "drill": s["drill"], "annular": COMMON["annular"],
            "via": up(s["drill"] + 2 * COMMON["annular"]),
            "hole_gap": round(s["hole_gap"] * MIL, 4), "hole_trace": round(s["hole_trace"] * MIL, 4),
            "edge": COMMON["edge"], "silk_pad": round(s["silk_pad"] * MIL, 4), "mask_dam": s["mask_dam"],
            "silk_h": COMMON["silk_h"], "silk_w": COMMON["silk_w"], "slot": COMMON["slot"]}


LABELS = [("track", "Min track width"), ("space", "Min clearance"), ("pour", "Min trace-to-pour"),
          ("drill", "Min drill"), ("annular", "Min annular ring"), ("via", "Min via diameter"),
          ("hole_gap", "Hole to hole"), ("hole_trace", "Hole to copper"), ("edge", "Copper to edge"),
          ("silk_pad", "Silk to pad"), ("mask_dam", "Solder mask dam"), ("silk_h", "Min silk text height"),
          ("silk_w", "Min silk line width"), ("slot", "Min slot width")]


def table(r):
    return "\n".join("%-22s %.3f mm  (%.1f mil)" % (lab, r[k], r[k] / MIL) for k, lab in LABELS)


def apply(board, service="MII", oz=1):
    """Set constraints + Default netclass floor + .kicad_dru extras. Returns list of change strings."""
    r = rules(service, oz)
    ds = board.GetDesignSettings()
    F, mm = pcbnew.FromMM, pcbnew.ToMM
    changes = []
    for attr, key in (("m_TrackMinWidth", "track"), ("m_MinClearance", "space"), ("m_MinThroughDrill", "drill"),
                      ("m_ViasMinAnnularWidth", "annular"), ("m_ViasMinSize", "via"), ("m_HoleToHoleMin", "hole_gap"),
                      ("m_HoleClearance", "hole_trace"), ("m_CopperEdgeClearance", "edge"), ("m_SilkClearance", "silk_pad"),
                      ("m_SolderMaskMinWidth", "mask_dam"), ("m_MinSilkTextHeight", "silk_h"),
                      ("m_MinSilkTextThickness", "silk_w")):
        old = mm(getattr(ds, attr))
        setattr(ds, attr, F(r[key]))
        if abs(old - r[key]) > 1e-6:
            changes.append("%-24s %.3f -> %.3f mm" % (attr[2:], old, r[key]))
    # Default netclass must not sit below what the fab can make (never lowers a bigger value)
    d = ds.m_NetSettings.GetDefaultNetclass()
    for get, set_, key in (("GetTrackWidth", "SetTrackWidth", "track"), ("GetClearance", "SetClearance", "space"),
                           ("GetViaDiameter", "SetViaDiameter", "via"), ("GetViaDrill", "SetViaDrill", "drill")):
        if mm(getattr(d, get)()) < r[key] - 1e-6:
            changes.append("Default netclass %-9s %.3f -> %.3f mm" % (get[3:], mm(getattr(d, get)()), r[key]))
            getattr(d, set_)(F(r[key]))
    dru = os.path.splitext(board.GetFileName())[0] + ".kicad_dru"
    write_dru(dru, service, oz, r)
    return changes


def dru_rules(service, oz, r):
    return "\n".join([
        DRU_BEGIN,
        "# Lion Circuits %s, %d oz copper" % (service, oz),
        '(rule "fab trace to pour"\n  (condition "A.Type == \'Zone\' && B.Type != \'Zone\'")\n'
        '  (constraint clearance (min %.4fmm)))' % r["pour"],
        '(rule "fab min slot"\n  (condition "A.Type == \'Pad\' && A.Hole_Size_X != A.Hole_Size_Y")\n'
        '  (constraint hole_size (min %.3fmm)))' % r["slot"],
        DRU_END])


def write_dru(path, service, oz, r):
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else "(version 1)\n"
    old = re.sub(re.escape(DRU_BEGIN) + ".*?" + re.escape(DRU_END) + r"\n?", "", old, flags=re.S)
    if "(version" not in old:
        old = "(version 1)\n" + old
    open(path, "w", encoding="utf-8").write(old.rstrip() + "\n\n" + dru_rules(service, oz, r) + "\n")


def write_template(service="MII", oz=1, template=TEMPLATE):
    """Bake the rules into KiCad's default template (used for every new project). Backs up the old one.
    Edits the template JSON directly: pcbnew.BOARD() is unusable inside the running PCB editor."""
    r = rules(service, oz)
    os.makedirs(template, exist_ok=True)
    pro = os.path.join(template, "default.kicad_pro")
    cur = json.load(open(pro, encoding="utf-8")) if os.path.exists(pro) else {}
    if os.path.exists(pro) and not os.path.exists(pro + ".before_fab_rules"):
        shutil.copy2(pro, pro + ".before_fab_rules")
    cur.setdefault("meta", {"filename": "default.kicad_pro", "version": 3})
    dr = cur.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rules", {})
    dr.update({"min_track_width": r["track"], "min_clearance": r["space"], "min_through_hole_diameter": r["drill"],
               "min_via_diameter": r["via"], "min_via_annular_width": r["annular"], "min_hole_to_hole": r["hole_gap"],
               "min_hole_clearance": r["hole_trace"], "min_copper_edge_clearance": r["edge"],
               "min_silk_clearance": r["silk_pad"], "min_text_height": r["silk_h"], "min_text_thickness": r["silk_w"]})
    classes = cur.setdefault("net_settings", {}).setdefault("classes", [])
    d = next((c for c in classes if c.get("name") == "Default"), None)
    if d is None:
        d = {"name": "Default", "track_width": 0.2, "clearance": 0.2, "via_diameter": 0.6, "via_drill": 0.3}
        classes.insert(0, d)
    for key, floor in (("track_width", r["track"]), ("clearance", r["space"]), ("via_diameter", r["via"]),
                       ("via_drill", r["drill"])):
        d[key] = max(d.get(key, 0), floor)  # never lowers a bigger default
    json.dump(cur, open(pro, "w", encoding="utf-8"), indent=2)
    write_dru(os.path.join(template, "default.kicad_dru"), service, oz, r)
    return pro


class FabRules(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Fab Rules (Lion Circuits)"
        self.category = "DRC"
        self.description = "Set DRC to Lion Circuits manufacturing capabilities"
        self.show_toolbar_button = False

    def Run(self):
        import wx
        board = pcbnew.GetBoard()
        dlg = wx.Dialog(None, title="Fab Rules - Lion Circuits", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        names = list(SERVICES)
        svc = wx.RadioBox(dlg, label="Service", choices=["%s - %s" % (n, SERVICES[n]["note"]) for n in names],
                          majorDimension=1, style=wx.RA_SPECIFY_COLS)
        oz = wx.Choice(dlg)
        tpl = wx.CheckBox(dlg, label="Also make this the default for every NEW project (KiCad default template)")
        tpl.SetValue(True)
        out = wx.TextCtrl(dlg, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(420, 250))
        out.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        def pick_service(_=None):
            opts = sorted(SERVICES[names[svc.GetSelection()]]["track"])
            oz.SetItems(["%d oz copper" % o for o in opts])
            oz.SetSelection(0)
            show()

        def current():
            n = names[svc.GetSelection()]
            return n, sorted(SERVICES[n]["track"])[oz.GetSelection()]

        def show(_=None):
            out.SetValue(table(rules(*current())))
        svc.Bind(wx.EVT_RADIOBOX, pick_service)
        oz.Bind(wx.EVT_CHOICE, show)
        pick_service()
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(svc, 0, wx.ALL | wx.EXPAND, 8)
        s.Add(oz, 0, wx.LEFT | wx.RIGHT, 8)
        s.Add(out, 1, wx.ALL | wx.EXPAND, 8)
        s.Add(tpl, 0, wx.LEFT | wx.RIGHT, 8)
        s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizerAndFit(s)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        service, copper = current()
        make_tpl = tpl.GetValue()
        dlg.Destroy()
        changes = apply(board, service, copper)
        msg = "Lion Circuits %s, %d oz applied.\n\n" % (service, copper)
        msg += ("\n".join(changes) if changes else "Board already matched these rules.")
        if make_tpl:
            msg += "\n\nNew projects now start with these rules:\n" + write_template(service, copper)
        pcbnew.Refresh()
        wx.MessageBox(msg + "\n\nSave the board, then run DRC (Inspect > Design Rules Checker).", "Fab Rules")


def _selfcheck():
    r = rules("MII", 1)
    assert r["track"] == 0.1524 and r["drill"] == 0.35 and r["via"] == 0.66 and r["hole_gap"] == 0.3048
    assert rules("Custom", 3)["track"] == 0.3048 and rules("Rush", 2)["pour"] == 0.3048
    try:
        rules("MII", 2)
        raise AssertionError("MII has no 2 oz")
    except ValueError:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("board", nargs="?")
    ap.add_argument("--service", default="MII", choices=list(SERVICES))
    ap.add_argument("--oz", type=int, default=1)
    ap.add_argument("--template", action="store_true", help="also write KiCad's default project template")
    a = ap.parse_args()
    _selfcheck()
    if a.board:
        b = pcbnew.LoadBoard(a.board)
        print("\n".join(apply(b, a.service, a.oz)) or "no changes")
        pcbnew.SaveBoard(a.board, b)
    if a.template:
        print("template:", write_template(a.service, a.oz))
    if not a.board and not a.template:
        print("selfcheck ok\n" + table(rules(a.service, a.oz)))
