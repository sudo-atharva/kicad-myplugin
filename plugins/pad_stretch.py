"""Pad Stretch: make the pads of footprints longer (outward, away from the part's centre), e.g. +0.5 mm for hand
soldering, then keep it for the project.

Pick the footprints: the selected ones, all with a reference prefix (all R, all C ...) or all of a footprint
(all R_0603_1608Metric ...). SMD pads by default; through-hole optional; pads at the centre (thermal pads) and pads
at odd angles are left alone. Live on the board; Cancel puts everything back.

Keep: each stretched footprint type is saved once into the project library <project>-pads (libs/<project>-pads.pretty
next to the board, added to the project's fp-lib-table) as <name>_ext<mm>, and the parts point to it, so the stretch
survives "Update Footprints from Library". The schematic's Footprint fields are updated too when the schematic is
closed (else use KiCad's Tools > Update Schematic from PCB), so "Update PCB from Schematic" doesn't undo it.

CLI (KiCad's python): pad_stretch.py <board.kicad_pcb> <mm> <prefix or footprint name or refs, comma separated>
"""
import glob, os, re, sys

try:
    from . import easyeda_import, sexp
except ImportError:
    import easyeda_import, sexp

TOL = 0.05  # mm: a pad this close to the footprint centre is a centre/thermal pad


def prefix(ref):
    m = re.match(r"^([A-Za-z_#]+)", ref)
    return m.group(1).upper() if m else ref


def fp_name(fp):
    return fp.GetFPID().GetLibItemName().wx_str() if hasattr(fp.GetFPID().GetLibItemName(), "wx_str") else str(fp.GetFPID().GetLibItemName())


def pick(board, mode, keys, selected=None):
    """Footprints by mode: 'selected', 'prefix' (keys = prefixes) or 'footprint' (keys = footprint names)."""
    fps = list(board.GetFootprints())
    if mode == "selected":
        return [f for f in fps if f.IsSelected()] if selected is None else selected
    keys = {k.upper() if mode == "prefix" else k for k in keys}
    if mode == "prefix":
        return [f for f in fps if prefix(f.GetReference()) in keys]
    return [f for f in fps if fp_name(f) in keys]


class Stretch(object):
    """Stretches pads of some footprints and can put them back."""

    def __init__(self, footprints, tht=False):
        import pcbnew
        self.pcbnew = pcbnew
        self.pads = []  # (pad, original size, original footprint-relative position)
        attribs = {pcbnew.PAD_ATTRIB_SMD} | ({pcbnew.PAD_ATTRIB_PTH} if tht else set())
        layer = pcbnew.PADSTACK.ALL_LAYERS
        for fp in footprints:
            for p in fp.Pads():
                if p.GetAttribute() in attribs:
                    # copies: KiCad hands back references to the pad's own values, which change with it
                    s, r = p.GetSize(layer), p.GetFPRelativePosition()
                    self.pads.append((p, pcbnew.VECTOR2I(s.x, s.y), pcbnew.VECTOR2I(r.x, r.y)))

    def apply(self, out_mm):
        """Every pad `out_mm` longer, pointing away from its footprint's centre. Returns pads changed."""
        pcbnew = self.pcbnew
        layer = pcbnew.PADSTACK.ALL_LAYERS
        out, n = pcbnew.FromMM(out_mm), 0
        for p, size, rel in self.pads:
            p.SetSize(layer, size)
            p.SetFPRelativePosition(rel)
            if abs(pcbnew.ToMM(rel.x)) < TOL and abs(pcbnew.ToMM(rel.y)) < TOL:
                continue  # centre pad
            ang = round(p.GetFPRelativeOrientation().AsDegrees()) % 180
            if ang not in (0, 90):
                continue  # odd angle: leave it
            horiz = abs(rel.x) >= abs(rel.y)  # pads to the left/right of the centre grow along x
            sign = 1 if (rel.x if horiz else rel.y) > 0 else -1
            along_x = horiz != (ang == 90)  # the pad's own x axis, if it's turned 90 degrees
            s = pcbnew.VECTOR2I(size.x + (out if along_x else 0), size.y + (0 if along_x else out))
            if s.x <= 0 or s.y <= 0:
                continue
            p.SetSize(layer, s)
            p.SetFPRelativePosition(pcbnew.VECTOR2I(rel.x + (sign * out // 2 if horiz else 0),
                                                    rel.y + (0 if horiz else sign * out // 2)))
            n += 1
        return n

    def restore(self):
        self.apply(0)


def project_lib(board_file):
    """(nickname, folder): <project>-pads, libs/<project>-pads.pretty next to the board."""
    base = re.sub(r"_v\d+$", "", os.path.splitext(os.path.basename(board_file))[0])
    nick = "%s-pads" % base
    return nick, os.path.join(os.path.dirname(os.path.abspath(board_file)), "libs", nick + ".pretty")


def keep(board, board_file, footprints, out_mm):
    """Save each stretched footprint type into the project library and point the parts to it.
    Returns {old lib:name -> new lib:name}."""
    import pcbnew
    nick, path = project_lib(board_file)
    if not os.path.isdir(path):
        os.makedirs(path)
    easyeda_import.add_lib(os.path.join(os.path.dirname(os.path.abspath(board_file)), "fp-lib-table"), "fp",
                           "${KIPRJMOD}/libs/%s.pretty" % nick, name=nick, descr="Footprints changed for this project (Pad Stretch)")
    done = {}
    for fp in footprints:
        old = fp.GetFPIDAsString()
        name = re.sub(r"_ext-?\d+\.\d+$", "", fp_name(fp)) + "_ext%.2f" % out_mm
        if old not in done:
            lib = pcbnew.FOOTPRINT(fp)  # copy, back to library position: unrotated, on top, at 0,0
            if lib.IsFlipped():
                lib.Flip(lib.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
            lib.SetOrientationDegrees(0)
            lib.SetPosition(pcbnew.VECTOR2I(0, 0))
            lib.SetFPID(pcbnew.LIB_ID(nick, name))
            lib.SetReference("REF**")
            # KiCad's own format by name: guessing it from the path fails while the .pretty folder is still empty
            pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP).FootprintSave(path, lib)
            done[old] = "%s:%s" % (nick, name)
        fp.SetFPID(pcbnew.LIB_ID(nick, name))
    return done


def update_schematics(board_file, refs, new_fp):
    """Set the Footprint field of these references in the project's schematics. Skips (and returns the names of)
    schematics open in KiCad (lock file next to them). Returns (symbols changed, open schematics)."""
    d = os.path.dirname(os.path.abspath(board_file))
    changed, busy = 0, []
    for sch in glob.glob(os.path.join(d, "*.kicad_sch")):
        if os.path.exists(os.path.join(d, "~%s.lck" % os.path.basename(sch))):
            busy.append(os.path.basename(sch))
            continue
        tree = sexp.parse(open(sch, encoding="utf-8").read())
        n = 0
        for sym in sexp.find(tree, "symbol"):
            if not sexp.first(sym, "lib_id"):
                continue
            names = {str(sexp.prop(sym, "Reference"))} | {str(r[1]) for r in sexp.walk(sym, "reference")}
            hit = names & set(refs)
            if not hit:
                continue
            new = new_fp.get(sorted(hit)[0])
            for p in sexp.find(sym, "property"):
                if p[1] == "Footprint" and new and p[2] != new:
                    p[2] = sexp.Q(new)
                    n += 1
        if n:
            with open(sch, "w", encoding="utf-8", newline="\n") as f:
                f.write(sexp.dump(tree) + "\n")
            changed += n
    return changed, busy


def run(board, board_file, footprints, out_mm, tht=False, save=True):
    """Stretch + keep, without a dialog. Returns (pads changed, {old -> new}, schematic symbols changed, open schematics)."""
    st = Stretch(footprints, tht)
    n = st.apply(out_mm)
    if not save or not n:
        return n, {}, 0, []
    mapping = keep(board, board_file, footprints, out_mm)
    new_by_ref = {f.GetReference(): f.GetFPIDAsString() for f in footprints}
    changed, busy = update_schematics(board_file, list(new_by_ref), new_by_ref)
    return n, mapping, changed, busy


class PadStretch(object):
    def Run(self):
        import wx, pcbnew
        board = pcbnew.GetBoard()
        board_file = board.GetFileName()
        fps = list(board.GetFootprints())
        sel = [f for f in fps if f.IsSelected()]
        if not fps:
            wx.MessageBox("No footprints on this board.", "Pad Stretch")
            return
        prefixes, names = {}, {}
        for f in fps:
            prefixes[prefix(f.GetReference())] = prefixes.get(prefix(f.GetReference()), 0) + 1
            names[fp_name(f)] = names.get(fp_name(f), 0) + 1
        sel_pre = {prefix(f.GetReference()) for f in sel}
        sel_names = {fp_name(f) for f in sel}

        dlg = wx.Dialog(None, title="Pad Stretch", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        modes = ["Selected footprints (%d)" % len(sel), "All with these reference letters", "All with these footprints"]
        mode = wx.RadioBox(dlg, label="Which parts", choices=modes, majorDimension=1)
        mode.SetSelection(0 if sel else 1)
        lst = wx.CheckListBox(dlg, size=(420, 200))
        amount = wx.SpinCtrlDouble(dlg, min=-2, max=5, inc=0.05, initial=0.5)
        amount.SetDigits(2)
        tht = wx.CheckBox(dlg, label="Also through-hole pads")
        save = wx.CheckBox(dlg, label="Keep for this project (save the changed footprints in the project library)")
        save.SetValue(True)
        info = wx.StaticText(dlg, label="")
        state = {"st": None, "keys": []}

        def fill(_=None):
            m = mode.GetSelection()
            lst.Clear()
            if m == 1:
                state["keys"] = sorted(prefixes)
                lst.Set(["%s  (%d)" % (k, prefixes[k]) for k in state["keys"]])
                for i, k in enumerate(state["keys"]):
                    lst.Check(i, k in sel_pre)
            elif m == 2:
                state["keys"] = sorted(names, key=lambda k: (-names[k], k))
                lst.Set(["%s  (%d)" % (k, names[k]) for k in state["keys"]])
                for i, k in enumerate(state["keys"]):
                    lst.Check(i, k in sel_names)
            lst.Enable(m != 0)
            update()

        def chosen():
            m = mode.GetSelection()
            if m == 0:
                return sel
            keys = [state["keys"][i] for i in lst.GetCheckedItems()]
            return pick(board, "prefix" if m == 1 else "footprint", keys)

        def update(_=None):
            if state["st"]:
                state["st"].restore()
            parts = chosen()
            state["st"] = Stretch(parts, tht.GetValue())
            n = state["st"].apply(amount.GetValue())
            info.SetLabel("%d parts, %d pads stretched (live on the board)" % (len(parts), n))
            pcbnew.Refresh()

        mode.Bind(wx.EVT_RADIOBOX, fill)
        lst.Bind(wx.EVT_CHECKLISTBOX, update)
        amount.Bind(wx.EVT_SPINCTRLDOUBLE, update)
        amount.Bind(wx.EVT_TEXT, update)
        tht.Bind(wx.EVT_CHECKBOX, update)
        g = wx.FlexGridSizer(0, 2, 6, 10)
        g.Add(wx.StaticText(dlg, label="Longer by (mm, outward)"), 0, wx.ALIGN_CENTER_VERTICAL)
        g.Add(amount)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(mode, 0, wx.ALL | wx.EXPAND, 8)
        s.Add(lst, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)
        s.Add(g, 0, wx.ALL, 8)
        s.Add(tht, 0, wx.LEFT, 8)
        s.Add(save, 0, wx.LEFT | wx.TOP, 8)
        s.Add(info, 0, wx.ALL, 8)
        s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizerAndFit(s)
        fill()
        ok = dlg.ShowModal() == wx.ID_OK
        parts = chosen()
        dlg.Destroy()
        if not ok:
            if state["st"]:
                state["st"].restore()
            pcbnew.Refresh()
            return
        if not save.GetValue() or not parts:
            pcbnew.Refresh()
            return
        if not board_file:
            wx.MessageBox("Stretched. Save the board once to keep the footprints in a project library.", "Pad Stretch")
            return
        mapping = keep(board, board_file, parts, amount.GetValue())
        new_by_ref = {f.GetReference(): f.GetFPIDAsString() for f in parts}
        changed, busy = update_schematics(board_file, list(new_by_ref), new_by_ref)
        pcbnew.Refresh()
        text = "Saved in the project library:\n" + "\n".join("  %s -> %s" % kv for kv in mapping.items())
        text += "\n\nSchematic: %d footprint fields updated." % changed
        if busy:
            text += ("\n%s is open in KiCad, so it was not changed: in the PCB editor use Tools > Update Schematic from "
                     "PCB (footprint assignments) so 'Update PCB from Schematic' keeps the new pads." % ", ".join(busy))
        wx.MessageBox(text + "\n\nSave the board to keep it.", "Pad Stretch")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        raise SystemExit
    import pcbnew
    bf, mm, keys = os.path.abspath(sys.argv[1]), float(sys.argv[2]), sys.argv[3].split(",")
    b = pcbnew.LoadBoard(bf)
    fps = pick(b, "prefix", keys) or pick(b, "footprint", keys) or [f for f in b.GetFootprints() if f.GetReference() in keys]
    print(run(b, bf, fps, mm))
    pcbnew.SaveBoard(bf, b)
