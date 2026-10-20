"""Power Trace for KiCad 10: select a pad/trace, enter volts + amps -> that net gets its width,
clearance and via size (via Net Setup: netclass + DRC rule), the router switches to netclass width,
and existing thinner traces of the net can be widened. Then press X and drag: it routes at that width.

Toolbar button in PCB editor, or CLI:
  python power_trace.py board.kicad_pcb --net +24V --volts 24 --amps 3 [--no-widen]
"""
import pcbnew

try:
    from . import net_setup as ns_mod  # inside the MyPlugin package
except ImportError:
    import net_setup as ns_mod  # run as a script from this folder


def power_trace(board, net, volts, amps, widen=True):
    """Returns (params, n_widened). params None = signal level, net left in Default."""
    saved, nets = ns_mod.load_settings(board)
    nets[net] = (volts, amps)
    classes = ns_mod.apply(board, nets, saved.get("rise_c", 10), saved.get("oz_outer", 1.0), saved.get("oz_inner", 0.5))
    params = next((p for p, members in classes.values() if net in members), None)
    n = 0
    if params and widen:
        w = pcbnew.FromMM(params["width"])
        for t in board.GetTracks():
            if t.GetNetname() == net and t.GetClass() in ("PCB_TRACK", "PCB_ARC") and t.GetWidth() < w:
                t.SetWidth(w)
                n += 1
    ds = board.GetDesignSettings()
    ds.UseCustomTrackViaSize(False)
    ds.SetTrackWidthIndex(0)      # index 0 = "use netclass width" for the interactive router
    ds.SetViaSizeIndex(0)
    ds.m_UseConnectedTrackWidth = False  # don't copy the width of the thin trace you start from
    return params, n


def selected_net(board):
    for it in pcbnew.GetCurrentSelection():
        if hasattr(it, "GetNetname") and it.GetNetname():
            return it.GetNetname()
    return None


class PowerTrace(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Power Trace (V / A -> trace width)"
        self.category = "Routing"
        self.description = "Size the selected net's traces and vias from voltage and current, then route with X"
        self.show_toolbar_button = True

    def Run(self):
        import wx
        board = pcbnew.GetBoard()
        saved, nets = ns_mod.load_settings(board)
        names = sorted(nets)
        net = selected_net(board) or (names[0] if names else "")
        mins = ns_mod.board_mins(board)
        dlg = wx.Dialog(None, title="Power Trace")
        s = wx.FlexGridSizer(0, 2, 6, 8)
        cb = wx.ComboBox(dlg, value=net, choices=names, style=wx.CB_READONLY)
        v = wx.TextCtrl(dlg, value="%g" % nets.get(net, (3.3, 1))[0])
        a = wx.TextCtrl(dlg, value="%g" % max(1.0, nets.get(net, (3.3, 1))[1]))
        widen = wx.CheckBox(dlg, label="Widen existing thinner traces of this net")
        widen.SetValue(True)
        out = wx.StaticText(dlg, label="")
        for label, ctl in (("Net", cb), ("Voltage V", v), ("Current A", a)):
            s.Add(wx.StaticText(dlg, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            s.Add(ctl, 1, wx.EXPAND)

        def calc(_=None):
            try:
                p = ns_mod.compute(float(v.GetValue()), float(a.GetValue()), saved.get("rise_c", 10),
                                   saved.get("oz_outer", 1.0), saved.get("oz_inner", 0.5), mins)
                out.SetLabel("Width %.2f mm   clearance %.2f mm   via %.1f/%.1f mm x%d" %
                             (p["width"], p["clearance"], p["via_d"], p["via_drill"], p["vias"]))
            except ValueError:
                out.SetLabel("")

        def pick(_):
            vv, aa = nets.get(cb.GetValue(), (3.3, 1))
            v.SetValue("%g" % vv)
            a.SetValue("%g" % max(1.0, aa))
        cb.Bind(wx.EVT_COMBOBOX, pick)
        v.Bind(wx.EVT_TEXT, calc)
        a.Bind(wx.EVT_TEXT, calc)
        calc()
        top = wx.BoxSizer(wx.VERTICAL)
        top.Add(s, 0, wx.ALL | wx.EXPAND, 10)
        top.Add(out, 0, wx.LEFT | wx.RIGHT, 10)
        top.Add(widen, 0, wx.ALL, 10)
        top.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizerAndFit(top)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        try:
            args = (cb.GetValue(), float(v.GetValue()), float(a.GetValue()), widen.GetValue())
        except ValueError:
            dlg.Destroy()
            return
        dlg.Destroy()
        p, n = power_trace(board, *args)
        pcbnew.Refresh()
        if p is None:
            wx.MessageBox("%s is signal level (<= %.1f A, <= 30 V): stays in Default." % (args[0], ns_mod.SIGNAL_A))
        else:
            wx.MessageBox("%s: %.2f mm traces, via %.1f/%.1f (x%d per layer change).\n%d existing traces widened.\n\n"
                          "Press X on a %s pad and drag: the router uses this width.\nRun DRC to check the widened traces."
                          % (args[0], p["width"], p["via_d"], p["via_drill"], p["vias"], n, args[0]), "Power Trace")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--net", required=True)
    ap.add_argument("--volts", type=float, required=True)
    ap.add_argument("--amps", type=float, required=True)
    ap.add_argument("--no-widen", action="store_true")
    x = ap.parse_args()
    b = pcbnew.LoadBoard(x.board)
    p, n = power_trace(b, x.net, x.volts, x.amps, not x.no_widen)
    pcbnew.SaveBoard(x.board, b)
    print(x.net, p, "widened:", n)
