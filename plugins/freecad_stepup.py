"""KiCad -> FreeCAD (KiCad StepUp) in one click, kept in sync both ways. Works on any machine:
missing pieces (KiCad StepUp in FreeCAD, kipy in KiCad's Python) are installed on request.

- first press: saves the board, starts FreeCAD with freecad_kicad_bridge.FCMacro, which activates KiCad StepUp
  and loads the board (no workbench / file picking); FreeCAD files go to the project's 07_FreeCAD/ if present
- later presses: just saves; the bridge in FreeCAD sees the change and reloads
- FreeCAD -> KiCad: buttons in FreeCAD's "KiCad Bridge" panel push via StepUp and make KiCad reload
  (needs KiCad's API server: Preferences > Plugins > Enable KiCad API)

KiCad StepUp (https://github.com/easyw/kicadStepUpMod, AGPL) is installed into FreeCAD, not bundled here.
"""
import glob, io, json, os, subprocess, urllib.request, zipfile
import pcbnew

try:
    from . import kpaths  # inside the MyPlugin package
except ImportError:
    import kpaths  # run as a script from this folder

HERE = os.path.dirname(os.path.abspath(__file__))
MACRO = os.path.join(HERE, "freecad_kicad_bridge.FCMacro")
STEPUP_ZIP = "https://github.com/easyw/kicadStepUpMod/archive/refs/heads/master.zip"


def mod_dirs():
    """Existing FreeCAD user Mod folders, newest version first (v1-1/Mod on FreeCAD 1.x, Mod on 0.x)."""
    out = []
    for base in kpaths.freecad_user_dirs():
        out += sorted(glob.glob(os.path.join(base, "v*", "Mod")), reverse=True) + [os.path.join(base, "Mod")]
    return out


def stepup_installed():
    return any(glob.glob(os.path.join(m, "kicadStepUpMod*")) for m in mod_dirs())


def _target_mod_dir():
    """Mod folder FreeCAD will read: an existing versioned one (v1-1/Mod), else the one matching the installed
    FreeCAD (1.x uses <user>/FreeCAD/v<major>-<minor>/Mod even before its first start), else <user>/FreeCAD/Mod."""
    import re
    existing = [m for m in mod_dirs() if os.path.isdir(m)]
    if existing:
        return existing[0]
    base = kpaths.freecad_user_dirs()[0]
    exe = kpaths.freecad() or ""
    m = re.search(r"FreeCAD[ _-]?(\d+)\.(\d+)", exe)
    if m and int(m.group(1)) >= 1:
        return os.path.join(base, "v%s-%s" % (m.group(1), m.group(2)), "Mod")
    return os.path.join(base, "Mod")


def install_stepup():
    """Download KiCad StepUp into the FreeCAD user Mod folder FreeCAD actually reads."""
    dest = _target_mod_dir()
    data = urllib.request.urlopen(urllib.request.Request(STEPUP_ZIP, headers={"User-Agent": "MyPlugin"}), timeout=180).read()
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(dest)  # -> <Mod>/kicadStepUpMod-master
    return dest


def api_enabled():
    """KiCad's IPC API server switch (kicad_common.json -> api.enable_server). None if unknown."""
    try:
        cfg = os.path.join(pcbnew.SETTINGS_MANAGER.GetUserSettingsPath(), "kicad_common.json")
        return bool(json.load(open(cfg, encoding="utf-8")).get("api", {}).get("enable_server"))
    except Exception:
        return None


def bridge_running(board_file):
    try:
        pid = int(open(board_file + ".fcbridge.lock").read().strip())
    except (OSError, ValueError):
        return False
    return kpaths.pid_alive(pid)


def launch(board_file, freecad):
    env = dict(os.environ, MYPLUGIN_PCB=board_file, MYPLUGIN_KICAD_PY=kpaths.kicad_python())
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
        T = "FreeCAD (StepUp)"
        board = pcbnew.GetBoard()
        path = board.GetFileName()
        if not path:
            wx.MessageBox("Save the board first.", T)
            return
        freecad = kpaths.freecad()
        if not freecad:
            wx.MessageBox("FreeCAD was not found on this computer.\nInstall it from https://www.freecad.org "
                          "(or set FREECAD_EXE to freecad's path), then try again.", T)
            return
        if not stepup_installed():
            if wx.MessageBox("KiCad StepUp is not installed in FreeCAD.\nDownload and install it now?\n"
                             "(github.com/easyw/kicadStepUpMod, AGPL)", T, wx.YES_NO) != wx.YES:
                return
            busy = wx.BusyInfo("Installing KiCad StepUp...")
            try:
                install_stepup()
            except Exception as e:
                del busy
                wx.MessageBox("Install failed: %s" % e, T)
                return
            del busy
        if not kpaths.py_has("kipy"):  # FreeCAD talks to KiCad through kipy (KiCad's API client)
            if wx.MessageBox("kicad-python (kipy) is needed for FreeCAD -> KiCad push/pull.\nInstall it into "
                             "KiCad's Python now?", T, wx.YES_NO) == wx.YES:
                busy = wx.BusyInfo("Installing kicad-python...")
                ok, text = kpaths.pip_install("kicad-python")
                del busy
                if not ok:
                    wx.MessageBox("Install failed:\n" + text, T)
        notes = ""
        api = api_enabled()
        if api is False:
            notes = ("\n\nKiCad's API is OFF, so FreeCAD can load the board but not push back.\n"
                     "Turn it on: Preferences > Preferences > Plugins > 'Enable KiCad API', then restart KiCad.")
        elif api is None:
            notes = ("\n\nIf pushing from FreeCAD fails: Preferences > Preferences > Plugins > "
                     "'Enable KiCad API', then restart KiCad.")
        pcbnew.SaveBoard(path, board)  # FreeCAD reads the file on disk; an open bridge reloads on this save
        if bridge_running(path):
            wx.MessageBox("Saved. FreeCAD is already open on this board and reloads by itself." + notes, T)
            return
        launch(path, freecad)
        wx.MessageBox("FreeCAD is starting with the board loaded.\n\n"
                      "In FreeCAD, the 'KiCad Bridge' panel (right side):\n"
                      " - reloads automatically when you save in KiCad\n"
                      " - Push part moves / outline: sends changes back, KiCad reloads (no restart)" + notes, T)
