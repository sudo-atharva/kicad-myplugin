"""Copper Path: wide copper that behaves like a trace.

Route the net normally (W / X - click the points it must touch). Then Copper Path wraps that net's traces
on that layer in a zone of the width you choose (3 mm, 5 mm, ...). It is a zone, so KiCad keeps clearance to
every other net by itself - a fat path never shorts. Pads it touches connect solid (no thermal spokes).
Drag the traces with the router (connections stay intact), then press "Refresh Copper Paths": every path is
rebuilt around the current traces at its stored width. Corners: round, or 45 deg (octagon caps, datasheet look).

Path settings live in the zone name: CP|<net>|<layer>|<width mm>|<round|45>
CLI: python copper_path.py board.kicad_pcb --net VIN --layer F.Cu --width 4 [--style 45]
     python copper_path.py board.kicad_pcb --refresh
"""
import math
import pcbnew

TAG = "CP|"
mm, MM = pcbnew.ToMM, pcbnew.FromMM


def path_shape(board, net, layer, width_mm, style="round"):
    """Union of the net's tracks/vias on `layer`, each grown to `width_mm`. None if the net has no tracks there."""
    W = MM(width_mm)
    # round: fine arcs; 45: an error of 7.6 % of the radius turns every cap/corner into an octagon
    err = MM(0.005) if style == "round" else max(MM(0.01), int(W / 2 * (1 - math.cos(math.pi / 8))))
    shape = pcbnew.SHAPE_POLY_SET()
    n = 0
    for t in board.GetTracks():
        if t.GetNetname() != net:
            continue
        if t.GetClass() in ("PCB_TRACK", "PCB_ARC") and t.GetLayer() == layer:
            grow = max(0, (W - t.GetWidth()) // 2)  # clearance arg grows the track outline to the path width
        elif t.GetClass() == "PCB_VIA" and t.IsOnLayer(layer):
            grow = max(0, (W - t.GetWidth(layer)) // 2)
        else:
            continue
        one = pcbnew.SHAPE_POLY_SET()
        t.TransformShapeToPolygon(one, layer, grow, err, pcbnew.ERROR_OUTSIDE)
        shape.BooleanAdd(one)
        n += 1
    if not n:
        return None
    shape.Simplify()
    _keep_off_other_nets(board, net, layer, shape, err)
    return shape if shape.OutlineCount() else None


SLIVER_MM = 0.3  # copper narrower than 2x this (fingers between pins) is removed


def _clearance(board, net_a, net_b):
    ns = board.GetDesignSettings().m_NetSettings
    c = max(ns.GetEffectiveNetClass(net_a).GetClearance(), board.GetDesignSettings().m_MinClearance)
    if net_b:
        c = max(c, ns.GetEffectiveNetClass(net_b).GetClearance())
    return c


def _keep_off_other_nets(board, net, layer, shape, err):
    """Cut other-net pads/tracks/vias (+clearance) out of the path, then drop slivers.
    The path then narrows into a pin row by itself instead of spreading over neighbouring pins."""
    bb = shape.BBox()
    keep = pcbnew.SHAPE_POLY_SET()
    margin = MM(0.05)
    for p in board.GetPads():
        if p.GetNetname() == net or not p.IsOnLayer(layer) or not bb.Intersects(p.GetBoundingBox()):
            continue
        one = pcbnew.SHAPE_POLY_SET()
        p.TransformShapeToPolygon(one, layer, _clearance(board, net, p.GetNetname()) + margin, err, pcbnew.ERROR_OUTSIDE)
        keep.BooleanAdd(one)
    for t in board.GetTracks():
        if t.GetNetname() == net or not t.IsOnLayer(layer) or not bb.Intersects(t.GetBoundingBox()):
            continue
        one = pcbnew.SHAPE_POLY_SET()
        t.TransformShapeToPolygon(one, layer, _clearance(board, net, t.GetNetname()) + margin, err, pcbnew.ERROR_OUTSIDE)
        keep.BooleanAdd(one)
    if keep.OutlineCount():
        shape.BooleanSubtract(keep)
    r = MM(SLIVER_MM)  # morphological opening: shrink then grow back removes thin fingers between pins
    shape.Inflate(-r, pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, err, True)
    shape.Inflate(r, pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, err, True)


def _zone_for(board, name):
    for z in board.Zones():
        if z.GetZoneName() == name:
            return z
    return None


def make(board, net, layer_name, width_mm, style="round"):
    """Create or update the copper path for net/layer. Returns the zone (or None if no tracks)."""
    layer = board.GetLayerID(layer_name)
    shape = path_shape(board, net, layer, width_mm, style)
    prefix = "%s%s|%s|" % (TAG, net, layer_name)
    for z in list(board.Zones()):  # one path per net+layer; replace an older one
        if z.GetZoneName().startswith(prefix):
            board.Delete(z)
    if shape is None:
        return None
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNetCode(board.FindNet(net).GetNetCode())
    z.SetOutline(shape)
    shape.this.disown()  # the zone now owns the polygon; without this Python frees it too -> crash
    z.SetZoneName("%s%g|%s" % (prefix, width_mm, style))
    # above every other zone (GND pours etc.) so the path wins where they overlap
    z.SetAssignedPriority(20 + max([q.GetAssignedPriority() for q in board.Zones()] or [0]))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)  # solid like a trace
    z.SetMinThickness(MM(0.25))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    board.Add(z)
    return z


def paths(board):
    out = []
    for z in board.Zones():
        parts = z.GetZoneName().split("|")
        if z.GetZoneName().startswith(TAG) and len(parts) == 5:
            out.append((parts[1], parts[2], float(parts[3]), parts[4]))
    return out


def refresh(board, fill=True):
    """Rebuild every copper path around the current tracks. Returns [(net, layer, ok)]."""
    report = [(net, lay, make(board, net, lay, w, st) is not None) for net, lay, w, st in paths(board)]
    if fill:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    return report


def _selection_net_layer(board):
    for it in pcbnew.GetCurrentSelection():
        if hasattr(it, "GetNetname") and it.GetNetname():
            lay = it.GetLayer() if it.GetClass() in ("PCB_TRACK", "PCB_ARC") else None
            return it.GetNetname(), (board.GetLayerName(lay) if lay is not None else None)
    return None, None


class CopperPath(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Copper Path"
        self.category = "Routing"
        self.description = "Wide copper that follows a net's traces and keeps clearance; drag traces, then refresh"
        self.show_toolbar_button = False

    def Run(self):
        import wx
        board = pcbnew.GetBoard()
        net, layer = _selection_net_layer(board)
        nets = sorted(str(n) for n in board.GetNetsByName().keys() if str(n))
        layers = [board.GetLayerName(l) for l in board.GetEnabledLayers().CuStack()]
        existing = {(n, l): (w, s) for n, l, w, s in paths(board)}
        dlg = wx.Dialog(None, title="Copper Path")
        g = wx.FlexGridSizer(0, 2, 6, 8)
        cnet = wx.ComboBox(dlg, value=net or (nets[0] if nets else ""), choices=nets, style=wx.CB_READONLY)
        clay = wx.Choice(dlg, choices=layers)
        clay.SetStringSelection(layer or layers[0])
        nc = board.GetDesignSettings().m_NetSettings.GetEffectiveNetClass(cnet.GetValue()) if cnet.GetValue() else None
        w0, s0 = existing.get((cnet.GetValue(), clay.GetStringSelection()),
                              (round(max(3.0, 2.5 * mm(nc.GetTrackWidth())) if nc else 3.0, 1), "45"))
        cw = wx.TextCtrl(dlg, value="%g" % w0)
        cs = wx.RadioBox(dlg, choices=["45 deg (datasheet look)", "round"], majorDimension=2)
        cs.SetSelection(0 if s0 == "45" else 1)
        for lab, c in (("Net", cnet), ("Layer", clay), ("Width mm", cw), ("Corners", cs)):
            g.Add(wx.StaticText(dlg, label=lab), 0, wx.ALIGN_CENTER_VERTICAL)
            g.Add(c, 1, wx.EXPAND)
        hint = wx.StaticText(dlg, label="Route the net first (W), clicking the points it must touch.\n"
                                        "Later: drag the traces, press 'Refresh Copper Paths' on the toolbar.\n"
                                        "Width 0 removes the path.")
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(g, 0, wx.ALL | wx.EXPAND, 10)
        s.Add(hint, 0, wx.LEFT | wx.RIGHT, 10)
        s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizerAndFit(s)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        try:
            width = float(cw.GetValue())
        except ValueError:
            dlg.Destroy()
            return
        net, layer, style = cnet.GetValue(), clay.GetStringSelection(), ("45" if cs.GetSelection() == 0 else "round")
        dlg.Destroy()
        if width <= 0:
            for z in list(board.Zones()):
                if z.GetZoneName().startswith("%s%s|%s|" % (TAG, net, layer)):
                    board.Delete(z)
            pcbnew.Refresh()
            return
        z = make(board, net, layer, width, style)
        if z is None:
            wx.MessageBox("%s has no traces on %s yet. Route it first (W), then run Copper Path." % (net, layer), "Copper Path")
            return
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        pcbnew.Refresh()


class RefreshCopperPaths(pcbnew.ActionPlugin):
    """Own toolbar button: after dragging traces, one click re-forms every copper path."""
    def defaults(self):
        import os
        self.name = "Refresh Copper Paths"
        self.category = "Routing"
        self.description = "Rebuild all Copper Paths around the current traces and refill zones"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "copper_refresh.png")
        self.dark_icon_file_name = self.icon_file_name

    def Run(self):
        board = pcbnew.GetBoard()
        refresh(board)
        pcbnew.Refresh()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--net")
    ap.add_argument("--layer", default="F.Cu")
    ap.add_argument("--width", type=float)
    ap.add_argument("--style", default="45", choices=["45", "round"])
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    b = pcbnew.LoadBoard(a.board)
    if a.refresh:
        print(refresh(b))
    else:
        z = make(b, a.net, a.layer, a.width, a.style)
        print("path:", z.GetZoneName() if z else "no tracks for %s on %s" % (a.net, a.layer))
        pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(a.out or a.board, b)
