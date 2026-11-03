"""Array Place: lay out repeated channels (LED + resistor, input protection, MOSFET drivers...) in one go.

1. Place ONE channel by hand the way you like it (e.g. D1 next to R1) and select those parts.
2. Run Array Place. Every other channel is found by connectivity: same footprints, wired to each other the same way
   (an LED whose resistor shares the same pad-to-pad net), so it works whatever the reference numbers are.
3. Spacing, columns per row and order update the board live while you drag the values; Ok keeps, Cancel restores.
Channels wired to a connector are ordered by its pin numbers, so channel order follows the connector.

Selecting just one part (e.g. one LED) arrays every part with the same footprint.

CLI: <KiCad python> array_place.py board.kicad_pcb D1,R1 [--pitch 4] [--cols 8] [-o out.kicad_pcb]
"""
import re
import pcbnew

CONN = re.compile(r"^(J|CN|CON|X|P|TB|XT|SV)\d", re.I)


def natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def fpid(fp):
    return str(fp.GetFPID().GetLibItemName())


def nets(fp):
    """{pad number: net code} of connected pads."""
    return {p.GetNumber(): p.GetNetCode() for p in fp.Pads() if p.GetNetCode() > 0}


def relations(template):
    """Order template parts anchor-first and, for each later part, how it hooks to an earlier one:
    [(index of earlier part, earlier pad, this pad)] - every pad pair sharing a net."""
    order, rel, rest = [template[0]], [None], list(template[1:])
    while rest:
        for fp in rest:
            links = [(j, pa, pb) for j, e in enumerate(order) for pa, na in nets(e).items()
                     for pb, nb in nets(fp).items() if na == nb]
            if links:
                order.append(fp)
                rel.append(links)
                rest.remove(fp)
                break
        else:  # not wired to the rest of the template: match by footprint only
            order.append(rest[0])
            rel.append([])
            rest.pop(0)
    return order, rel


def find_channels(board, template):
    """[[fp, ...] per channel], template first; each channel lists parts in the template's order."""
    order, rel = relations(template)
    used = {fp.GetReference() for fp in order}
    by_id = {}
    for fp in board.GetFootprints():
        by_id.setdefault(fpid(fp), []).append(fp)
    channels = [order]
    anchors = sorted((fp for fp in by_id[fpid(order[0])] if fp.GetReference() not in used),
                     key=lambda f: natural(f.GetReference()))
    for a in anchors:
        if a.GetReference() in used:
            continue
        chan, taken = [a], {a.GetReference()}
        for i in range(1, len(order)):
            cand = [fp for fp in by_id[fpid(order[i])] if fp.GetReference() not in used | taken]
            if rel[i]:
                cand = [fp for fp in cand if all(nets(chan[j]).get(pa) and nets(chan[j]).get(pa) == nets(fp).get(pb)
                                                 for j, pa, pb in rel[i])]  # wired exactly like the template
            if not cand:
                break
            pick = min(cand, key=lambda f: natural(f.GetReference()))
            chan.append(pick)
            taken.add(pick.GetReference())
        if len(chan) == len(order):
            channels.append(chan)
            used |= taken
    return channels


def connector_key(board, chan):
    """(connector ref, pin) the channel is wired to, for ordering; None if it doesn't touch a connector."""
    mine = {c.GetReference() for c in chan}
    codes = {n for fp in chan for n in nets(fp).values()}
    hits = [(natural(fp.GetReference()), natural(p.GetNumber())) for fp in board.GetFootprints()
            if CONN.match(fp.GetReference()) and fp.GetReference() not in mine
            for p in fp.Pads() if p.GetNetCode() in codes]
    return min(hits) if hits else None


class Layout(object):
    """Remembers where everything was, so a live preview can be redone or undone."""

    def __init__(self, board, channels):
        self.board, self.channels = board, channels
        self.orig = {fp.GetReference(): (fp.GetPosition(), fp.GetOrientationDegrees(), fp.IsFlipped())
                     for ch in channels for fp in ch}
        t = channels[0]
        a = t[0]
        self.base = a.GetPosition()
        self.rel = [(fp.GetPosition() - a.GetPosition(), fp.GetOrientationDegrees() - a.GetOrientationDegrees(),
                     fp.IsFlipped()) for fp in t]
        self.a_rot, self.a_flip = a.GetOrientationDegrees(), a.IsFlipped()
        bb = None
        for fp in t:
            b = fp.GetBoundingBox(False, False)
            if bb is None:
                bb = b
            else:
                bb.Merge(b)
        self.size = (pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight()))
        keyed = [(connector_key(board, ch), i) for i, ch in enumerate(channels)]
        self.by_connector = [channels[i] for _, i in sorted(keyed, key=lambda k: (k[0] is None, k[0] or [], k[1]))]

    def apply(self, pitch_x, pitch_y, cols, order="reference"):
        chans = self.by_connector if order == "connector" else self.channels
        for k, ch in enumerate(chans):
            col, row = k % max(1, cols), k // max(1, cols)
            origin = self.base + pcbnew.VECTOR2I(pcbnew.FromMM(col * pitch_x), pcbnew.FromMM(row * pitch_y))
            for fp, (off, drot, flip) in zip(ch, self.rel):
                if fp.IsFlipped() != flip:
                    fp.Flip(fp.GetPosition(), getattr(pcbnew, "FLIP_DIRECTION_LEFT_RIGHT", False))
                fp.SetOrientationDegrees(self.a_rot + drot)
                fp.SetPosition(origin + off)

    def restore(self):
        for ch in self.channels:
            for fp in ch:
                pos, rot, flip = self.orig[fp.GetReference()]
                if fp.IsFlipped() != flip:
                    fp.Flip(fp.GetPosition(), getattr(pcbnew, "FLIP_DIRECTION_LEFT_RIGHT", False))
                fp.SetOrientationDegrees(rot)
                fp.SetPosition(pos)


class ArrayPlace(object):
    def Run(self):
        import wx
        board = pcbnew.GetBoard()
        sel = [fp for fp in board.GetFootprints() if fp.IsSelected()]
        if not sel:
            wx.MessageBox("Place one channel by hand (e.g. an LED and its resistor), select those parts, "
                          "then run Array Place again.", "Array Place")
            return
        channels = find_channels(board, sorted(sel, key=lambda f: natural(f.GetReference())))
        if len(channels) < 2:
            wx.MessageBox("No other channel wired like %s was found." % ", ".join(f.GetReference() for f in sel),
                          "Array Place")
            return
        lay = Layout(board, channels)
        w, h = lay.size
        n = len(channels)

        dlg = wx.Dialog(None, title="Array Place - %d channels of %s" % (n, "+".join(fpid(f).split("_")[0] for f in channels[0])))
        px = wx.SpinCtrlDouble(dlg, min=-200, max=200, inc=0.25, initial=round(w + 1.0, 2))
        py = wx.SpinCtrlDouble(dlg, min=-200, max=200, inc=0.25, initial=round(h + 1.0, 2))
        cols = wx.SpinCtrl(dlg, min=1, max=n, initial=n)
        order = wx.Choice(dlg, choices=["By reference (D1, D2, ...)", "By connector pin"])
        order.SetSelection(1 if any(connector_key(board, c) for c in channels) else 0)
        listing = wx.StaticText(dlg, label="\n".join(", ".join(f.GetReference() for f in c) for c in channels[:12])
                                + ("\n..." if n > 12 else ""))

        def update(_=None):
            lay.apply(px.GetValue(), py.GetValue(), cols.GetValue(), "connector" if order.GetSelection() else "reference")
            pcbnew.Refresh()

        for c, ev in ((px, wx.EVT_SPINCTRLDOUBLE), (py, wx.EVT_SPINCTRLDOUBLE), (cols, wx.EVT_SPINCTRL),
                      (order, wx.EVT_CHOICE)):
            c.Bind(ev, update)
        for c in (px, py, cols):
            c.Bind(wx.EVT_TEXT, update)  # typing updates live too
        g = wx.FlexGridSizer(0, 2, 6, 10)
        for label, c in (("Spacing X (mm, negative = leftwards)", px), ("Spacing Y (mm, negative = upwards)", py),
                         ("Channels per row", cols), ("Order", order)):
            g.Add(wx.StaticText(dlg, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            g.Add(c, 0, wx.EXPAND)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(g, 0, wx.ALL, 12)
        s.Add(wx.StaticText(dlg, label="Channels found (first = your template):"), 0, wx.LEFT, 12)
        s.Add(listing, 0, wx.ALL, 12)
        s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizerAndFit(s)
        update()
        if dlg.ShowModal() != wx.ID_OK:
            lay.restore()
        dlg.Destroy()
        pcbnew.Refresh()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("refs", help="template parts, comma separated, e.g. D1,R1")
    ap.add_argument("--pitch", type=float, help="mm between channels (default: template width + 1)")
    ap.add_argument("--pitch-y", type=float)
    ap.add_argument("--cols", type=int)
    ap.add_argument("--order", choices=["reference", "connector"], default="reference")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    b = pcbnew.LoadBoard(a.board)
    want = a.refs.split(",")
    tmpl = sorted((f for f in b.GetFootprints() if f.GetReference() in want), key=lambda f: want.index(f.GetReference()))
    chans = find_channels(b, tmpl)
    lay = Layout(b, chans)
    lay.apply(a.pitch if a.pitch is not None else lay.size[0] + 1, a.pitch_y if a.pitch_y is not None else lay.size[1] + 1,
              a.cols or len(chans), a.order)
    for c in chans:
        print(" ".join(f.GetReference() for f in c))
    pcbnew.SaveBoard(a.out or a.board, b)
