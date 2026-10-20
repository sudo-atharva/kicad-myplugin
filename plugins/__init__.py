"""MyPlugin: one toolbar button -> hub window with all tools.

Tools live in this folder and also run as command-line scripts (see each file's docstring).
"""
import os
import traceback
import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.join(HERE, "icons")


def _run_models3d():
    import wx
    from . import eagle3d
    board = pcbnew.GetBoard()
    busy = wx.BusyInfo("Matching footprints to KiCad 3D models...\n(first run indexes the library, ~20 s)")
    try:
        report = eagle3d.attach_models(board, os.path.dirname(board.GetFileName()) or HERE)
    finally:
        del busy
    pcbnew.Refresh()
    _show_text("3D Models", (eagle3d.summarize(report) if report else "Every footprint already has a 3D model.")
               + "\n\nOpen the 3D viewer (Alt+3) to check. Copper was not changed.")


def _run_symbols():
    import wx
    from . import sch_swap
    sch = os.path.splitext(pcbnew.GetBoard().GetFileName())[0] + ".kicad_sch"
    if not os.path.exists(sch):
        wx.MessageBox("No schematic found:\n%s" % sch, "Eagle -> KiCad Symbols")
        return
    if wx.MessageBox("Replace imported Eagle symbols with KiCad library symbols in\n%s\n\n"
                     "Close the schematic editor first. A backup (.eagle_backup.kicad_sch) is kept, and nothing "
                     "is saved unless every net still connects exactly the same pins." % sch,
                     "Eagle -> KiCad Symbols", wx.OK | wx.CANCEL) != wx.OK:
        return
    busy = wx.BusyInfo("Swapping symbols and checking the netlist...")
    try:
        ok, text = sch_swap.swap_file(sch)
    finally:
        del busy
    _show_text("Eagle -> KiCad Symbols", text + ("\n\nReopen the schematic to see it." if ok else ""))


def _tool(module, cls):
    def run():
        from importlib import import_module
        getattr(import_module("." + module, __name__), cls)().Run()
    return run


TOOLS = [
    ("fab", "Fab Rules", "Set DRC to Lion Circuits capabilities (MII / Custom / Rush). Do this first.",
     _tool("fab_rules", "FabRules")),
    ("net_setup", "Net Setup", "Volts + amps per net -> trace width, clearance, vias. Netclasses + DRC rules.",
     _tool("net_setup", "NetSetup")),
    ("power_trace", "Power Trace", "Selected net: size it from V/A, widen its traces, then route (W, or X on default keys).",
     _tool("power_trace", "PowerTrace")),
    ("copper_path", "Copper Path", "Wide copper that follows a net's traces (datasheet-style), keeps clearance; drag + refresh.",
     _tool("copper_path", "CopperPath")),
    ("power_pours", "Power Pours", "Copper around regulators, GND pours, plane vias for cut-off pads.",
     _tool("power_pours", "PowerPours")),
    ("stitch", "Via Stitching", "ViaStitcher by weirdgyn: select a zone, fill it with a stitching via grid.",
     _tool("viastitcher", "ViaStitcherPlugin")),
    ("autoroute", "Autoroute H/V", "Freerouting: horizontal on top, vertical on bottom. Never shrinks traces.",
     _tool("autoroute_hv", "AutorouteHV")),
    ("silk", "Silk Beautify", "Clean reference placement, net names on connector pins, values to Fab.",
     _tool("silk_beautify", "SilkBeautify")),
    ("docs", "Docs (PcbDraw)", "PcbDraw by yaqwsx: front/back drawings + renders into the project's docs folder.",
     _tool("docs_pcbdraw", "PcbDrawDocs")),
    ("freecad", "FreeCAD (StepUp)", "Open in FreeCAD/KiCad StepUp and stay in sync both ways, no restarts.",
     _tool("freecad_stepup", "FreeCADStepUp")),
    ("models3d", "3D Models", "Give every footprint without a model the matching KiCad 3D model.",
     _run_models3d),
    ("symbols", "Eagle -> KiCad Symbols", "Swap Eagle schematic symbols for KiCad ones, netlist-checked.",
     _run_symbols),
]


def _show_text(title, text):
    import wx
    dlg = wx.Dialog(None, title=title, size=(760, 480), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
    t = wx.TextCtrl(dlg, value=text, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
    t.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
    s = wx.BoxSizer(wx.VERTICAL)
    s.Add(t, 1, wx.EXPAND | wx.ALL, 8)
    s.Add(dlg.CreateButtonSizer(wx.OK), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
    dlg.SetSizer(s)
    dlg.ShowModal()
    dlg.Destroy()


class Hub(object):
    def __init__(self):
        import wx
        self.choice = None
        self.dlg = wx.Dialog(None, title="MyPlugin", style=wx.DEFAULT_DIALOG_STYLE)
        grid = wx.FlexGridSizer(0, 2, 10, 18)
        for i, (icon, name, desc, _) in enumerate(TOOLS):
            bmp = wx.Bitmap(os.path.join(ICONS, icon + ".png"), wx.BITMAP_TYPE_PNG)
            btn = wx.BitmapButton(self.dlg, bitmap=bmp)
            btn.SetToolTip(desc)
            btn.Bind(wx.EVT_BUTTON, lambda e, i=i: self.pick(i))
            title = wx.StaticText(self.dlg, label=name)
            title.SetFont(title.GetFont().Bold().Scaled(1.15))
            text = wx.StaticText(self.dlg, label=desc)
            text.Wrap(260)
            col = wx.BoxSizer(wx.VERTICAL)
            col.Add(title)
            col.Add(text, 0, wx.TOP, 2)
            row = wx.BoxSizer(wx.HORIZONTAL)
            row.Add(btn, 0, wx.ALIGN_TOP)
            row.Add(col, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 10)
            grid.Add(row, 0, wx.EXPAND)
        hint = wx.StaticText(self.dlg, label="Suggested order: Fab Rules -> Net Setup -> place parts -> Power Pours -> "
                                              "Via Stitching -> Autoroute H/V / Power Trace -> Silk Beautify -> DRC -> Docs")
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(grid, 0, wx.ALL, 16)
        s.Add(hint, 0, wx.LEFT | wx.RIGHT, 16)
        s.Add(self.dlg.CreateButtonSizer(wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.dlg.SetSizerAndFit(s)
        self.dlg.CentreOnScreen()

    def pick(self, i):
        self.choice = i
        self.dlg.EndModal(1)

    def show(self):
        self.dlg.ShowModal()
        self.dlg.Destroy()
        return self.choice


class MyPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "MyPlugin"
        self.category = "MyPlugin"
        self.description = "Net setup, power traces and pours, H/V autoroute, silkscreen, 3D models, Eagle symbols"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(ICONS, "myplugin.png")
        self.dark_icon_file_name = os.path.join(ICONS, "myplugin.png")

    def Run(self):
        import wx
        i = Hub().show()
        if i is None:
            return
        try:
            TOOLS[i][3]()
        except Exception:
            _show_text("MyPlugin - %s failed" % TOOLS[i][1], traceback.format_exc())


MyPlugin().register()

# second toolbar button: re-form Copper Paths after dragging traces (used often, so one click)
from .copper_path import RefreshCopperPaths  # noqa: E402
RefreshCopperPaths().register()
