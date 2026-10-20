"""Board documentation with PcbDraw (https://github.com/yaqwsx/PcbDraw, MIT, by Jan Mrazek).

Makes front/back stylized drawings (SVG) and front/back 3D renders (PNG) of the board.
Output: <project>/../04_Docs/ if that folder exists (PCB repo layout), else <board dir>/docs/.
PcbDraw runs in a separate KiCad-Python process; it uses kicad-cli, not pcbnew.

CLI: python docs_pcbdraw.py board.kicad_pcb [outdir]
"""
import os, subprocess, sys
import pcbnew

KICAD_BIN = os.path.abspath(os.path.join(os.path.dirname(pcbnew.__file__), "..", "..")) # bin/Lib/site-packages -> bin
PY = os.path.join(KICAD_BIN, "python.exe" if os.name == "nt" else "python3")
CLI = os.path.join(KICAD_BIN, "kicad-cli.exe" if os.name == "nt" else "kicad-cli")
RUN = "import sys; from pcbdraw.ui import run; sys.argv[0] = 'pcbdraw'; run()"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def installed():
    r = subprocess.run([PY, "-c", "import pcbdraw.ui"], capture_output=True, creationflags=NOWIN)
    return r.returncode == 0


def install():
    """pip --user into KiCad's Python (LnkParse3 is a Windows-only dependency pcbdraw forgets)."""
    r = subprocess.run([PY, "-m", "pip", "install", "--user", "--disable-pip-version-check", "pcbdraw", "LnkParse3"],
                       capture_output=True, text=True, creationflags=NOWIN)
    return r.returncode == 0, (r.stdout + r.stderr)[-1500:]


def out_dir(board_file):
    proj = os.path.dirname(os.path.abspath(board_file))
    repo_docs = os.path.join(proj, "..", "04_Docs")
    return os.path.abspath(repo_docs if os.path.isdir(repo_docs) else os.path.join(proj, "docs"))


def make_docs(board_file, outdir=None, log=print):
    outdir = outdir or out_dir(board_file)
    os.makedirs(outdir, exist_ok=True)
    env = dict(os.environ, KICAD_CLI=CLI)
    name = os.path.splitext(os.path.basename(board_file))[0]
    jobs = [("plot", "front", "%s_drawing_front.svg"), ("plot", "back", "%s_drawing_back.svg"),
            ("render", "front", "%s_render_front.png"), ("render", "back", "%s_render_back.png")]
    made, problems = [], []
    for cmd, side, pattern in jobs:
        out = os.path.join(outdir, pattern % name)
        args = [PY, "-c", RUN, cmd, "--side", side] + (["--silent"] if cmd == "plot" else ["--transparent"]) + [board_file, out]
        r = subprocess.run(args, capture_output=True, text=True, env=env, creationflags=NOWIN,
                           cwd=os.path.dirname(os.path.abspath(board_file)))  # anything PcbDraw writes lands by the board
        if r.returncode == 0 and os.path.exists(out):
            made.append(out)
            log("ok   " + out)
        else:
            problems.append("%s %s: %s" % (cmd, side, (r.stderr or r.stdout).strip()[-400:]))
            log("FAIL %s %s" % (cmd, side))
    return outdir, made, problems


class PcbDrawDocs(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Docs (PcbDraw)"
        self.category = "Documentation"
        self.description = "Front/back drawings and renders of the board with PcbDraw"
        self.show_toolbar_button = False

    def Run(self):
        import wx
        board = pcbnew.GetBoard()
        path = board.GetFileName()
        if not path:
            wx.MessageBox("Save the board first.", "Docs (PcbDraw)")
            return
        if not installed():
            if wx.MessageBox("PcbDraw is not installed in KiCad's Python.\nInstall it now (pip, needs internet)?",
                             "Docs (PcbDraw)", wx.YES_NO) != wx.YES:
                return
            busy = wx.BusyInfo("Installing PcbDraw...")
            ok, text = install()
            del busy
            if not ok:
                wx.MessageBox("Install failed:\n" + text, "Docs (PcbDraw)")
                return
        pcbnew.SaveBoard(path, board)  # PcbDraw reads the file on disk
        busy = wx.BusyInfo("PcbDraw: drawing front/back and rendering...")
        try:
            outdir, made, problems = make_docs(path, log=lambda *_: None)
        finally:
            del busy
        msg = "Saved to %s:\n" % outdir + "\n".join(" - " + os.path.basename(m) for m in made)
        if problems:
            msg += "\n\nProblems:\n" + "\n".join(problems)
        wx.MessageBox(msg, "Docs (PcbDraw)")
        if made and hasattr(os, "startfile"):
            os.startfile(outdir)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit
    if not installed():
        print("installing pcbdraw...", install()[0])
    d, made, problems = make_docs(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print("->", d)
    for p in problems:
        print("PROBLEM", p)
