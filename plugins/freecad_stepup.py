"""KiCad -> FreeCAD (KiCad StepUp) in one click, kept in sync both ways.

- first press: saves the board, starts FreeCAD in the board's folder with freecad_kicad_bridge.FCMacro,
  which activates KiCad StepUp and loads the board (no workbench / file picking)
- later presses: just saves; the bridge in FreeCAD sees the change and reloads
- FreeCAD -> KiCad: buttons in FreeCAD's "KiCad Bridge" panel push via StepUp and make KiCad reload
  (KiCad's IPC API must be on: Preferences > Plugins > Enable KiCad API)

KiCad StepUp (https://github.com/easyw/kicadStepUpMod, AGPL) is installed into FreeCAD, not bundled here.
"""
import ctypes, glob, io, os, subprocess, urllib.request, zipfile
import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
MACRO = os.path.join(HERE, "freecad_kicad_bridge.FCMacro")
KICAD_PY = os.path.join(os.path.abspath(os.path.join(os.path.dirname(pcbnew.__file__), "..", "..")), "python.exe")
STEPUP_ZIP = "https://github.com/easyw/kicadStepUpMod/archive/refs/heads/master.zip"


def find_freecad():
    if os.environ.get("FREECAD_EXE") and os.path.exists(os.environ["FREECAD_EXE"]):
        return os.environ["FREECAD_EXE"]
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")]
    found = []
    for r in roots:
        found += glob.glob(os.path.join(r, "FreeCAD*", "bin", "freecad.exe"))
    return sorted(found)[-1] if found else None  # newest version name sorts last


def user_mod_dir():
    """Newest FreeCAD user Mod folder (%APPDATA%/FreeCAD/v1-1/Mod on 1.x, %APPDATA%/FreeCAD/Mod on 0.x)."""
    base = os.path.join(os.environ.get("APPDATA", ""), "FreeCAD")
    versioned = sorted(glob.glob(os.path.join(base, "v*", "Mod")))
    return versioned[-1] if versioned else os.path.join(base, "Mod")


def stepup_installed():
    base = os.path.join(os.environ.get("APPDATA", ""), "FreeCAD")
    return bool(glob.glob(os.path.join(base, "Mod", "kicadStepUpMod*")) + glob.glob(os.path.join(base, "*", "Mod", "kicadStepUpMod*")))


def install_stepup():
    data = urllib.request.urlopen(urllib.request.Request(STEPUP_ZIP, headers={"User-Agent": "MyPlugin"}), timeout=120).read()
    dest = user_mod_dir()
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(dest)  # -> <Mod>/kicadStepUpMod-master
    return dest


def bridge_running(board_file):
    lock = board_file + ".fcbridge.lock"
    try:
        pid = int(open(lock).read().strip())
    except (OSError, ValueError):
        return False
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid) if os.name == "nt" else None  # QUERY_LIMITED_INFORMATION
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def launch(board_file, freecad):
    env = dict(os.environ, MYPLUGIN_PCB=board_file, MYPLUGIN_KICAD_PY=KICAD_PY)
    board_dir = os.path.dirname(os.path.abspath(board_file))
    fc_dir = os.path.join(board_dir, "..", "07_FreeCAD")  # project layout: FreeCAD files live in 07_FreeCAD/
    subprocess.Popen([freecad, MACRO], cwd=os.path.abspath(fc_dir if os.path.isdir(fc_dir) else board_dir), env=env)


class FreeCADStepUp(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "FreeCAD (StepUp)"
        self.category = "Mechanical"
        self.description = "Open the board in FreeCAD/KiCad StepUp and keep both in sync"
        self.show_toolbar_button = False

    def Run(self):
        import wx
        board = pcbnew.GetBoard()
        path = board.GetFileName()
        if not path:
            wx.MessageBox("Save the board first.", "FreeCAD (StepUp)")
            return
        freecad = find_freecad()
        if not freecad:
            wx.MessageBox("FreeCAD not found. Install it from https://www.freecad.org or set FREECAD_EXE.", "FreeCAD (StepUp)")
            return
        if not stepup_installed():
            if wx.MessageBox("KiCad StepUp is not installed in FreeCAD.\nDownload and install it now?\n(github.com/easyw/kicadStepUpMod, AGPL)",
                             "FreeCAD (StepUp)", wx.YES_NO) != wx.YES:
                return
            busy = wx.BusyInfo("Installing KiCad StepUp...")
            try:
                install_stepup()
            except Exception as e:
                del busy
                wx.MessageBox("Install failed: %s" % e, "FreeCAD (StepUp)")
                return
            del busy
        pcbnew.SaveBoard(path, board)  # FreeCAD reads the file on disk; an open bridge reloads on this save
        if bridge_running(path):
            wx.MessageBox("Saved. FreeCAD is already open on this board and reloads by itself.", "FreeCAD (StepUp)")
            return
        launch(path, freecad)
        wx.MessageBox("FreeCAD is starting with the board loaded.\n\n"
                      "In FreeCAD, the 'KiCad Bridge' panel (right side):\n"
                      " - reloads automatically when you save in KiCad\n"
                      " - Push part moves / outline: sends changes back, KiCad reloads (no restart)\n\n"
                      "Needs: Preferences > Plugins > Enable KiCad API (for push/pull).", "FreeCAD (StepUp)")
