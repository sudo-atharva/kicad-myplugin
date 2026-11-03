"""Auto-attach KiCad library 3D models to imported (Eagle etc.) footprints.

Copper is never touched: each footprint keeps its own pads; we find the KiCad
library footprint whose pads match geometrically, then copy its 3D model with
the offset/rotation needed to line up with these pads. No match -> generated box.

Usage (KiCad's python):
  <KiCad python> eagle3d.py board.kicad_pcb|board.brd [-o out.kicad_pcb]
"""
import json, math, os, re, subprocess, sys, argparse
import pcbnew

try:
    from . import kpaths  # inside the MyPlugin package
except ImportError:
    import kpaths  # run as a script from this folder

FP_DIR = kpaths.footprint_dir()
MODEL_DIR = kpaths.model_dir()
# library index cache: shared by all boards, so it lives in the per-user data dir (never the plugin folder)
CACHE = os.path.join(kpaths.user_data_dir(), "fp_index.json")

PAD_RE = re.compile(r'\(pad\s+"([^"]*)"\s+(\w+)\s+\w+.*?\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)\s*\(size\s+([-\d.]+)\s+([-\d.]+)\)', re.S)
MODEL_RE = re.compile(r'\(model\s+"?([^"\s)]+)"?.*?\(offset\s+\(xyz\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\)\s*\).*?\(rotate\s+\(xyz\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\)\s*\)', re.S)

# Reference prefix -> preferred library prefixes (name hint only, geometry decides)
REF_LIBS = {
    "R": ("Resistor_",), "C": ("Capacitor_",), "L": ("Inductor_",),
    "D": ("Diode_", "LED_"), "LED": ("LED_",), "Q": ("Package_TO_SOT",),
    "IC": ("Package_",), "U": ("Package_", "RF_Module"), "J": ("Connector_", "TerminalBlock"),
    "X": ("Connector_",), "SV": ("Connector_Pin",), "SL": ("Connector_Pin",),
    "OK": ("Package_DIP", "Package_SO"), "K": ("Relay_",), "F": ("Fuse",), "Y": ("Crystal",),
    "SW": ("Button_Switch",), "S": ("Button_Switch",), "ESP": ("RF_Module",),
}
# Parts that are copper only: never get a model
NO_MODEL = {"SJ", "JP", "TP", "FID", "MH", "H"}
# Eagle package names -> KiCad naming (compared upper-case with -_ ., stripped)
PKG_ALIAS = {"DO214AC": "DSMA", "DO214AA": "DSMB", "DO214AB": "DSMC", "SMA": "DSMA", "SMB": "DSMB",
             "TO220": "TO2203VERTICAL", "TO263": "TO263", "SOT23": "SOT23", "DIL": "DIP", "ESP32WROOM": "ESP32WROOM"}
# Eagle pad names -> KiCad numbering (diodes/LEDs: 1=K 2=A, polarized caps: 1=+)
PAD_ALIAS = {"A": "2", "C": "1", "K": "1", "+": "1", "-": "2"}


GFX_RE = re.compile(r'\((fp_line|fp_rect|fp_circle|fp_arc|fp_poly)(.*?)\(layer\s+"([^"]+)"\)', re.S)
PT_RE = re.compile(r'\((start|end|mid|center|xy)\s+([-\d.]+)\s+([-\d.]+)\)')


def bbox(pts):
    if not pts:
        return None
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def norm(s):
    return re.sub(r"[-_ .,]", "", s.upper())


def model_exists(path):
    if not MODEL_DIR:
        return False  # 3D model library not installed on this machine
    p = re.sub(r"\$\{KICAD\d*_3DMODEL_DIR\}", MODEL_DIR.replace("\\", "/"), path)
    base = os.path.splitext(p)[0]
    return any(os.path.exists(base + ext) for ext in (".step", ".stp", ".wrl"))  # KiCad swaps step/wrl


def build_index():
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    if not FP_DIR or not os.path.isdir(FP_DIR):
        raise RuntimeError("KiCad's footprint library was not found on this machine. Install the KiCad libraries "
                           "(footprints + 3D models) with the KiCad installer, then try again.")
    idx = []
    for lib in sorted(os.listdir(FP_DIR)):
        if not lib.endswith(".pretty"):
            continue
        d = os.path.join(FP_DIR, lib)
        for fn in os.listdir(d):
            if not fn.endswith(".kicad_mod"):
                continue
            txt = open(os.path.join(d, fn), encoding="utf-8").read()
            m = MODEL_RE.search(txt)
            if not m or not model_exists(m.group(1)):
                continue  # no model, or library points at a model that isn't installed
            # skip NPTH and unnumbered SMD pads (paste windows / thermal sub-pads)
            pads = [(n, t != "smd", float(x), -float(y), float(w), float(h), float(r or 0))
                    for n, t, x, y, r, w, h in PAD_RE.findall(txt)
                    if t != "np_thru_hole" and not (t == "smd" and n == "")]
            pts = []
            for kind, body, layer in GFX_RE.findall(txt):
                if layer not in ("F.Fab", "F.SilkS"):
                    continue
                xy = [(float(a), -float(b)) for _, a, b in PT_RE.findall(body)]
                if kind == "fp_circle" and len(xy) == 2:
                    r = math.dist(*xy)
                    xy = [(xy[0][0] - r, xy[0][1] - r), (xy[0][0] + r, xy[0][1] + r)]
                pts += xy
            if pads:
                idx.append({"lib": lib[:-7], "name": fn[:-10], "pads": pads, "body": bbox(pts),
                            "model": m.group(1), "off": [float(v) for v in m.group(2, 3, 4)],
                            "rot": [float(v) for v in m.group(5, 6, 7)]})
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(idx, open(CACHE, "w"))
    return idx


def rot(x, y, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return x * c - y * s, x * s + y * c


def fit(epads, kpads):
    """Best rigid fit of library pads onto eagle pads. Returns (cost, theta, tx, ty) in y-up mm.
    Pad counts may differ (modules with extra GND/thermal pads): the smaller set is matched
    into the larger one, centres aligned on the pad bounding box."""
    def centre(ps):
        xs = [p[2] for p in ps]; ys = [p[3] for p in ps]
        return (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    ecx, ecy = centre(epads)
    small_is_k = len(kpads) <= len(epads)
    best = None
    for th in (0, 90, 180, 270):
        kr = [(k, rot(k[2], k[3], th)) for k in kpads]
        kcx, kcy = centre([(0, 0, x, y) for _, (x, y) in kr])
        kk = [(k, x - kcx, y - kcy) for k, (x, y) in kr]
        ee = [(e, e[2] - ecx, e[3] - ecy) for e in epads]
        small, big = (kk, ee) if small_is_k else (ee, kk)
        used, err, size_err, mism, numhit = set(), 0.0, 0.0, 0, 0
        for a, ax, ay in small:
            j, d = min(((j, math.hypot(ax - bx, ay - by)) for j, (b, bx, by) in enumerate(big)
                        if j not in used), key=lambda t: t[1])
            used.add(j)
            k, e = (a, big[j][0]) if small_is_k else (big[j][0], a)
            err = max(err, d)
            mism += (k[1] != e[1])
            size_err += abs(sorted(k[4:6])[0] - sorted(e[4:6])[0]) + abs(sorted(k[4:6])[1] - sorted(e[4:6])[1])
            numhit += (k[0] == PAD_ALIAS.get(e[0], e[0]))
        cost = err + 0.05 * size_err / len(small) + mism * 1.0 - 0.001 * numhit
        if best is None or cost < best[0]:
            # library origin (0,0) -> rotated, then shifted by the centre alignment
            best = (cost, th, ecx - kcx, ecy - kcy)
    return best


def ref_prefix(ref):
    return re.match(r"[A-Za-z]*", ref).group(0).upper()


def name_bonus(fp, entry):
    """Category/name score: R1206 must pick Resistor_SMD:R_1206, not a MELF with similar pads."""
    libs = REF_LIBS.get(ref_prefix(fp.GetReference()), ())
    pkg = norm(str(fp.GetFPID().GetLibItemName()))
    if "LED" in pkg:
        libs = ("LED_",)
    name = norm(entry["name"])
    b = 0.0
    if libs:
        b += 0.15 if entry["lib"].startswith(libs) else -0.5
    for k, v in PKG_ALIAS.items():
        if k in pkg and v in name:
            b += 0.15
    nums = re.findall(r"\d{4}", pkg)  # 0603/1206 style chip sizes
    if nums and nums[0] in name:
        b += 0.1
    if "HANDSOLDER" in name:
        b -= 0.02
    if any(p.GetNumber() == "+" for p in fp.Pads()) and name.startswith("CP"):
        b += 0.1  # polarized cap pads -> polarized cap footprint
    return b


def local_up(fp, v):
    """Board point -> footprint top-side local frame, y-up mm (same frame as library pads)."""
    x, y = rot(pcbnew.ToMM(v.x - fp.GetPosition().x), pcbnew.ToMM(v.y - fp.GetPosition().y),
               fp.GetOrientationDegrees())  # board is y-down, so +deg undoes the CCW rotation
    return (x, y) if fp.IsFlipped() else (x, -y)  # flipped local frame is already Y-mirrored


def footprint_pads(fp):
    out = []
    for p in fp.Pads():
        if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
            continue
        x, y = local_up(fp, p.GetPosition())
        sz = p.GetSize(pcbnew.F_Cu)
        out.append((p.GetNumber(), p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH, round(x, 3), round(y, 3),
                    pcbnew.ToMM(sz.x), pcbnew.ToMM(sz.y), 0))
    return out


def footprint_body(fp):
    layers = {pcbnew.F_SilkS, pcbnew.F_Fab, pcbnew.B_SilkS, pcbnew.B_Fab}
    pts = []
    for g in fp.GraphicalItems():
        if g.GetLayer() in layers and g.GetClass() != "PCB_TEXT":
            bb = g.GetBoundingBox()
            for cx in (bb.GetLeft(), bb.GetRight()):
                for cy in (bb.GetTop(), bb.GetBottom()):
                    pts.append(local_up(fp, pcbnew.VECTOR2I(cx, cy)))
    return bbox(pts)


def body_term(ebody, e, th, tx, ty):
    """mm mismatch of body outlines (silk+fab) - separates vertical vs horizontal headers, cap diameters."""
    if not ebody or not e.get("body"):
        return 0.0
    x0, y0, x1, y1 = e["body"]
    pts = [rot(x, y, th) for x in (x0, x1) for y in (y0, y1)]
    k = bbox([(x + tx, y + ty) for x, y in pts])
    return 0.015 * sum(abs(a - b) for a, b in zip(k, ebody))


def box_model(fp, pads, outdir):
    """Grey box covering the pads/body. KiCad VRML units: 1 = 0.1 inch."""
    name = re.sub(r"[^\w.-]", "_", str(fp.GetFPID().GetLibItemName()))
    path = os.path.join(outdir, name + ".wrl")
    xs = [p[2] for p in pads] or [0]; ys = [p[3] for p in pads] or [0]
    w = max(xs) - min(xs) + 1.0; h = max(ys) - min(ys) + 1.0
    z = 1.0 if all(not p[1] for p in pads) else 3.0  # ponytail: fixed heights, read a height field if you need accuracy
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    u = 1 / 2.54
    os.makedirs(outdir, exist_ok=True)
    with open(path, "w") as f:
        f.write("#VRML V2.0 utf8\nTransform { translation %f %f %f children Shape {"
                " appearance Appearance { material Material { diffuseColor 0.25 0.25 0.28 } }"
                " geometry Box { size %f %f %f } } }\n" % (cx * u, cy * u, z / 2 * u, w * u, h * u, z * u))
    return "${KIPRJMOD}/3d_generated/" + os.path.basename(path)


CUSTOM_DIR = "3d_models"  # drop your own .step/.wrl files here, next to the board
MODEL_EXT = (".step", ".stp", ".wrl", ".wrz")


def custom_models(board_dir):
    d = os.path.join(board_dir, CUSTOM_DIR)
    return sorted(f for f in os.listdir(d) if f.lower().endswith(MODEL_EXT)) if os.path.isdir(d) else []


def candidates(board, max_err=0.35, top=5, everything=False):
    """Footprints without a model (everything=True: all of them), grouped by package: [{"package", "fps", "pads", "options"}].
    options = up to `top` library matches, best first: (cost, theta, tx, ty, entry)."""
    idx = build_index()
    by_count = {}
    for e in idx:
        by_count.setdefault(len(e["pads"]), []).append(e)
    groups = {}
    for fp in board.GetFootprints():
        if (len(fp.Models()) and not everything) or ref_prefix(fp.GetReference()) in NO_MODEL:
            continue
        pads = footprint_pads(fp)
        if not pads:
            continue
        key = (str(fp.GetFPID().GetLibItemName()), ref_prefix(fp.GetReference()), tuple(pads))
        if key in groups:
            groups[key]["fps"].append(fp)
            continue
        pkg = norm(key[0])
        # same pad count, plus name-matched parts with any count (modules with extra GND/thermal pads)
        cands = list(by_count.get(len(pads), []))
        cands += [e for e in idx if len(e["pads"]) != len(pads)
                  and any(k in pkg and v in norm(e["name"]) for k, v in PKG_ALIAS.items())]
        ebody, ranked = footprint_body(fp), []
        for e in cands:
            if len(e["pads"]) == len(pads) and sum(p[1] for p in e["pads"]) != sum(p[1] for p in pads):
                continue  # different SMD/THT mix
            c, th, tx, ty = fit(pads, e["pads"])
            c -= name_bonus(fp, e)
            if c <= max_err:  # pads decide acceptance, body shape ranks
                ranked.append((c + body_term(ebody, e, th, tx, ty), (c, th, tx, ty, e)))
        ranked.sort(key=lambda r: r[0])
        groups[key] = {"package": key[0], "fps": [fp], "pads": pads, "options": [o for _, o in ranked[:top]]}
    return list(groups.values())


def model_from(option):
    c, th, tx, ty, e = option
    m = pcbnew.FP_3DMODEL()
    ox, oy = rot(e["off"][0], e["off"][1], th)
    m.m_Filename = e["model"]
    m.m_Offset = pcbnew.VECTOR3D(ox + tx, oy + ty, e["off"][2])
    # KiCad's model Z rotation is clockwise-positive, our fit angle is CCW
    m.m_Rotation = pcbnew.VECTOR3D(e["rot"][0], e["rot"][1], e["rot"][2] - th)
    return m


def copy_model(m):
    """FP_3DMODEL has no copy constructor in the SWIG API; each footprint needs its own instance."""
    c = pcbnew.FP_3DMODEL()
    c.m_Filename, c.m_Offset, c.m_Rotation, c.m_Scale = m.m_Filename, m.m_Offset, m.m_Rotation, m.m_Scale
    return c


def option_label(option):
    return "%s:%s" % (option[4]["lib"], option[4]["name"])


def apply(group, choice, out_dir):
    """choice: index into group["options"], "box" for a generated box, None to skip. Returns report rows."""
    if choice is None:
        return []
    if isinstance(choice, tuple):  # ("custom", file name in <board dir>/3d_models/)
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = "${KIPRJMOD}/%s/%s" % (CUSTOM_DIR, choice[1])
        label, cost = "custom: " + choice[1], None
    elif choice == "box":
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = box_model(group["fps"][0], group["pads"], os.path.join(out_dir, "3d_generated"))
        label, cost = "BOX (no library match)", None
    else:
        opt = group["options"][choice]
        m, label, cost = model_from(opt), option_label(opt), round(opt[0], 3)
    for fp in group["fps"]:
        fp.Models().clear()  # replace, never stack a second model on top
        fp.Models().push_back(copy_model(m))
    return [(fp.GetReference(), group["package"], label, cost) for fp in group["fps"]]


def attach_models(board, out_dir, max_err=0.35):
    """Attach the best 3D model (or a generated box) to every footprint that has none.
    Returns [(ref, package, match, cost)]."""
    report = []
    for g in candidates(board, max_err, top=1):
        report += apply(g, 0 if g["options"] else "box", out_dir)
    return report


def preview_png(fp, model, out_png, run=True):
    """3D render of just this footprint with `model` (None = no model), isometric, via kicad-cli. ~1-3 s.
    run=False: only write the one-part board and return its path (render() it later, e.g. in a thread)."""
    b = pcbnew.BOARD()
    f = pcbnew.FOOTPRINT(fp)
    b.Add(f)
    if f.IsFlipped():  # preview from the top, whichever side the part sits on
        f.Flip(f.GetPosition(), getattr(pcbnew, "FLIP_DIRECTION_TOP_BOTTOM", False))
    f.SetOrientationDegrees(0)
    f.Models().clear()
    if model is not None:
        f.Models().push_back(model)
    bb = f.GetBoundingBox(False, False)
    m = pcbnew.FromMM(1.5)
    r = pcbnew.PCB_SHAPE(b)
    r.SetShape(pcbnew.SHAPE_T_RECT)
    r.SetStart(pcbnew.VECTOR2I(bb.GetLeft() - m, bb.GetTop() - m))
    r.SetEnd(pcbnew.VECTOR2I(bb.GetRight() + m, bb.GetBottom() + m))
    r.SetLayer(pcbnew.Edge_Cuts)
    b.Add(r)
    pcb = os.path.splitext(out_png)[0] + ".kicad_pcb"
    pcbnew.SaveBoard(pcb, b)
    return render(pcb, out_png) if run else pcb


def render(pcb, out_png):
    """No pcbnew here, so it is safe to call from a worker thread."""
    subprocess.run([kpaths.kicad_cli(), "pcb", "render", "--quality", "basic", "-w", "360", "--height", "280",
                    "--rotate", "-45,0,30", "--zoom", "0.6", "-o", out_png, pcb],
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out_png if os.path.exists(out_png) else None


def pick_and_apply(board, out_dir):
    """3D Models dialog: every package without a model, its top library matches, a live 3D preview.
    Returns the report of what was applied, or None if cancelled."""
    import tempfile, threading
    import wx
    busy = wx.BusyInfo("Matching footprints to KiCad 3D models...\n(first run indexes the library, ~20 s)")
    try:
        groups = candidates(board)
    finally:
        del busy
    if not groups:
        if wx.MessageBox("Every footprint already has a 3D model.\nPick models for all parts anyway "
                         "(Skip keeps a part's current model)?", "3D Models", wx.YES_NO) != wx.YES:
            return None
        groups = candidates(board, everything=True)
        if not groups:
            return []
    tmp = tempfile.mkdtemp(prefix="myplugin_3d_")
    BOX, SKIP, BROWSE = "Generated box (no library match)", "Skip - leave without a model", "Browse for a model file..."
    dlg = wx.Dialog(None, title="3D Models - pick a model per package", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
    lst = wx.ListBox(dlg, choices=["%s  (%s)" % (g["package"], ", ".join(f.GetReference() for f in g["fps"]))
                                   for g in groups], size=(380, 300))
    pick = wx.Choice(dlg, size=(420, -1))
    pic = wx.StaticBitmap(dlg, size=(360, 280))
    note = wx.StaticText(dlg, label="")
    # default: best match; when re-picking parts that already have a model, default to keeping it
    choice = [None if len(g["fps"][0].Models()) else 0 if g["options"] else "box" for g in groups]  # per group: option index, "box" or None
    shown = {}  # (group, choice) -> png path

    def values(i):
        """Dropdown entries for group i: [(label, value)]; value as apply() takes it, or "browse"."""
        return ([(option_label(o), n) for n, o in enumerate(groups[i]["options"])]
                + [("Custom: " + f, ("custom", f)) for f in custom_models(out_dir)]
                + [(BROWSE, "browse"), (BOX, "box"), (SKIP, None)])

    def preview_model(c, i):
        if isinstance(c, int):
            return model_from(groups[i]["options"][c])
        if isinstance(c, tuple):  # absolute path: the preview board lives in a temp folder, not the project
            m = pcbnew.FP_3DMODEL()
            m.m_Filename = os.path.join(out_dir, CUSTOM_DIR, c[1]).replace("\\", "/")
            return m
        fp = groups[i]["fps"][0]
        return copy_model(fp.Models()[0]) if c is None and len(fp.Models()) else None  # Skip: what it has now

    def show(i):
        c = choice[i]
        key = (i, c)
        if key in shown:
            pic.SetBitmap(wx.Bitmap(shown[key]) if shown[key] else wx.Bitmap(360, 280))
            note.SetLabel("" if shown[key] else "Preview failed (model file missing?)")
            return
        note.SetLabel("Rendering preview...")
        png = os.path.join(tmp, "p%d_%d.png" % (i, len(shown)))
        pcb = preview_png(groups[i]["fps"][0], preview_model(c, i), png, run=False)

        def work():
            png = render(pcb, os.path.splitext(pcb)[0] + ".png")
            wx.CallAfter(done, key, png)
        threading.Thread(target=work, daemon=True).start()

    def done(key, png):
        if not dlg:  # dialog already closed
            return
        shown[key] = png
        if lst.GetSelection() == key[0] and choice[key[0]] == key[1]:
            show(key[0])

    def on_list(_=None):
        i = lst.GetSelection()
        vals = values(i)
        pick.Set([label for label, _ in vals])
        pick.SetSelection(next(n for n, (_, v) in enumerate(vals) if v == choice[i]))
        show(i)

    def on_pick(_):
        i = lst.GetSelection()
        v = values(i)[pick.GetSelection()][1]
        if v == "browse":
            fd = wx.FileDialog(dlg, "3D model (copied into %s/ next to the board)" % CUSTOM_DIR,
                               wildcard="3D models (*.step;*.stp;*.wrl;*.wrz)|*.step;*.stp;*.wrl;*.wrz",
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
            if fd.ShowModal() == wx.ID_OK:
                import shutil
                os.makedirs(os.path.join(out_dir, CUSTOM_DIR), exist_ok=True)
                src = fd.GetPath()
                dst = os.path.join(out_dir, CUSTOM_DIR, os.path.basename(src))
                if os.path.abspath(src) != os.path.abspath(dst):
                    shutil.copy2(src, dst)
                choice[i] = ("custom", os.path.basename(src))
            fd.Destroy()
            on_list()  # refresh the list (new file shows up for every package)
            return
        choice[i] = v
        show(i)

    lst.Bind(wx.EVT_LISTBOX, on_list)
    pick.Bind(wx.EVT_CHOICE, on_pick)
    right = wx.BoxSizer(wx.VERTICAL)
    right.Add(wx.StaticText(dlg, label="Model for this package (best match first):"))
    right.Add(pick, 0, wx.EXPAND | wx.TOP, 4)
    right.Add(pic, 0, wx.TOP, 8)
    right.Add(note, 0, wx.TOP, 4)
    row = wx.BoxSizer(wx.HORIZONTAL)
    row.Add(lst, 1, wx.EXPAND | wx.ALL, 8)
    row.Add(right, 0, wx.ALL, 8)
    s = wx.BoxSizer(wx.VERTICAL)
    s.Add(row, 1, wx.EXPAND)
    s.Add(wx.StaticText(dlg, label="Copper is never changed. Ok applies the chosen model to every part of that package.\n"
                                   "Your own models: put .step/.wrl files in %s/ next to the board (or use Browse). "
                                   "Custom models go in at zero offset; nudge them in Footprint Properties > 3D Models."
                                   % CUSTOM_DIR), 0, wx.LEFT, 8)
    s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
    dlg.SetSizerAndFit(s)
    lst.SetSelection(0)
    on_list()
    ok = dlg.ShowModal() == wx.ID_OK
    dlg.Destroy()
    if not ok:
        return None
    report = []
    for g, c in zip(groups, choice):
        report += apply(g, c, out_dir)
    return report


def summarize(report):
    seen = {}
    for ref, pkg, match, c in report:
        seen.setdefault((pkg, match), []).append(ref)
    lines = ["%-22s -> %-60s x%d" % (pkg, match, len(refs)) for (pkg, match), refs in sorted(seen.items())]
    boxes = sum(1 for r in report if r[3] is None)
    lines.append("\n%d footprints: %d library models, %d generated boxes" % (len(report), len(report) - boxes, boxes))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("-o", "--out")
    ap.add_argument("--max-err", type=float, default=0.35, help="mm tolerance for a pad match")
    a = ap.parse_args()

    src = a.board
    if not src.endswith(".kicad_pcb"):
        pcb = os.path.splitext(src)[0] + ".kicad_pcb"
        subprocess.run([kpaths.kicad_cli(), "pcb", "import", "-o", pcb, src], check=True,
                       cwd=os.path.dirname(os.path.abspath(src)))
        src = pcb
    out = a.out or src
    board = pcbnew.LoadBoard(src)
    report = attach_models(board, os.path.dirname(os.path.abspath(out)), a.max_err)
    board.Save(out)
    print(summarize(report) + ". Saved " + out)


if __name__ == "__main__":
    main()
