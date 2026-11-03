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
    report = eagle3d.pick_and_apply(board, os.path.dirname(board.GetFileName()) or HERE)
    if report is None:
        return
    pcbnew.Refresh()
    _show_text("3D Models", (eagle3d.summarize(report) if report else "Nothing to change.")
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
    ("project", "Project & Versions", "New project with 01_Source...07_FreeCAD + git; save versions, see history.",
     _tool("project_git", "ProjectGit")),
    ("fab", "Fab Rules", "Set DRC to Lion Circuits capabilities (MII / Custom / Rush). Do this first.",
     _tool("fab_rules", "FabRules")),
    ("net_setup", "Net Setup", "Volts + amps per net -> trace width, clearance, vias. Netclasses + DRC rules.",
     _tool("net_setup", "NetSetup")),
    ("array", "Array Place", "Place one LED+R (any channel) by hand, select it: all the others follow, live spacing.",
     _tool("array_place", "ArrayPlace")),
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
    ("docs", "Docs", "Front/back drawings + 3D renders into 04_Docs. Quick (seconds) or PcbDraw (pretty, slow).",
     _tool("docs_pcbdraw", "PcbDrawDocs")),
    ("bom", "BOM + CPL Export", "Grouped BOM (CSV for JLCPCB, JSON for scripts) + pick-and-place into 03_Manufacturing.",
     _tool("bom_export", "BomExport")),
    ("freecad", "FreeCAD (StepUp)", "Open in FreeCAD/KiCad StepUp and stay in sync both ways, no restarts.",
     _tool("freecad_stepup", "FreeCADStepUp")),
    ("models3d", "3D Models", "Pick each package's 3D model with a live preview: library matches or your own 3d_models/.",
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
        grid = wx.FlexGridSizer(0, 3, 10, 18)
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
        hint = wx.StaticText(self.dlg, label="Suggested order: Project -> Fab Rules -> Net Setup -> place parts / Array Place -> Power Pours -> "
                                              "Via Stitching -> Autoroute H/V / Power Trace -> Silk Beautify -> DRC -> Docs -> BOM")
        hint.Wrap(grid.CalcMin().x)
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
        self.description = "Project + git, net setup, array placement, pours, H/V autoroute, silkscreen, docs, BOM, 3D models"
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
