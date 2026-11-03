"""Board documentation: front/back drawings (SVG) and front/back 3D renders (PNG).

Quick (default): kicad-cli export svg + render, a few seconds, nothing to install.
Pretty: PcbDraw (https://github.com/yaqwsx/PcbDraw, MIT, by Jan Mrazek) stylized drawings; slow.
Output: the project's 04_Docs/ (see kpaths.project_folder), else <board dir>/docs/.

CLI: python docs_pcbdraw.py board.kicad_pcb [outdir] [--pretty]
"""
import os, subprocess, sys
import pcbnew

try:
    from . import kpaths  # inside the MyPlugin package
except ImportError:
    import kpaths  # run as a script from this folder

PY = kpaths.kicad_python()
CLI = kpaths.kicad_cli()
RUN = "import sys; from pcbdraw.ui import run; sys.argv[0] = 'pcbdraw'; run()"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def installed():
    return kpaths.py_has("pcbdraw.ui")


def install():
    """pip --user into KiCad's Python (LnkParse3 is a Windows-only dependency pcbdraw forgets)."""
    return kpaths.pip_install(*(["pcbdraw", "LnkParse3"] if os.name == "nt" else ["pcbdraw"]))


def out_dir(board_file):
    return kpaths.project_folder(board_file, "04_Docs", "docs")


FRONT = "F.Cu,F.Paste,F.Mask,F.SilkS,Edge.Cuts"
BACK = "B.Cu,B.Paste,B.Mask,B.SilkS,Edge.Cuts"


def _jobs(board_file, outdir, pretty):
    """[(label, args, out)]. Quick: kicad-cli only (seconds, nothing to install). Pretty: PcbDraw (slow)."""
    name = os.path.splitext(os.path.basename(board_file))[0]
    o = lambda pattern: os.path.join(outdir, pattern % name)
    if pretty:
        return [("%s %s" % (cmd, side), [PY, "-c", RUN, cmd, "--side", side]
                 + (["--silent"] if cmd == "plot" else ["--transparent"]) + [board_file, o(pattern)], o(pattern))
                for cmd, side, pattern in [("plot", "front", "%s_drawing_front.svg"), ("plot", "back", "%s_drawing_back.svg"),
                                           ("render", "front", "%s_render_front.png"),
                                           ("render", "back", "%s_render_back.png")]]
    svg = lambda layers, out, mirror: (out, [CLI, "pcb", "export", "svg", "--mode-single", "-l", layers,
                                             "--page-size-mode", "2", "--exclude-drawing-sheet", "-o", out]
                                       + (["--mirror"] if mirror else []) + [board_file], out)
    png = lambda side, out: (out, [CLI, "pcb", "render", "--side", side, "--quality", "basic", "-w", "1600",
                                   "--height", "1000", "-o", out, board_file], out)
    return [svg(FRONT, o("%s_drawing_front.svg"), False), svg(BACK, o("%s_drawing_back.svg"), True),
            png("top", o("%s_render_front.png")), png("bottom", o("%s_render_back.png"))]


def make_docs(board_file, outdir=None, log=print, pretty=False):
    outdir = outdir or out_dir(board_file)
    os.makedirs(outdir, exist_ok=True)
    env = dict(os.environ, KICAD_CLI=CLI)
    made, problems = [], []
    # all four at once: each is its own process, so wall time ~= the slowest one
    procs = [(label, out, subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                           creationflags=NOWIN, cwd=os.path.dirname(os.path.abspath(board_file))))
             for label, args, out in _jobs(board_file, outdir, pretty)]
    for label, out, p in procs:
        text = p.communicate()[0]
        if p.returncode == 0 and os.path.exists(out):
            made.append(out)
            log("ok   " + out)
        else:
            problems.append("%s: %s" % (os.path.basename(label), text.strip()[-400:]))
            log("FAIL " + label)
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
        mode = wx.SingleChoiceDialog(None, "Front/back drawings (SVG) and 3D renders (PNG) of the board.",
                                     "Docs", ["Quick: KiCad's own exporter (seconds, nothing to install)",
                                              "Pretty: PcbDraw stylized drawings (slow, heavy on CPU/RAM)"])
        ok, pretty = mode.ShowModal() == wx.ID_OK, mode.GetSelection() == 1
        mode.Destroy()
        if not ok:
            return
        if pretty and not installed():
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
        busy = wx.BusyInfo("Drawing front/back and rendering...")
        try:
            outdir, made, problems = make_docs(path, log=lambda *_: None, pretty=pretty)
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
    pretty = "--pretty" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--pretty"]
    if pretty and not installed():
        print("installing pcbdraw...", install()[0])
    d, made, problems = make_docs(args[0], args[1] if len(args) > 1 else None, pretty=pretty)
    print("->", d)
    for p in problems:
        print("PROBLEM", p)
