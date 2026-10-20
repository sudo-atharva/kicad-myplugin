"""Freerouting with layer directions: horizontal on top, vertical on bottom (alternating on inner layers).

Uses the board's own netclasses/DRC (Net Setup widths and clearances go into the DSN), adds Freerouting
per-layer preferred directions with a high cost for going against them, routes, imports the result,
and reports how much of each layer actually follows its direction.

Toolbar button in PCB editor, or CLI:
  python autoroute_hv.py board.kicad_pcb [-o out.kicad_pcb] [--against 8] [--passes 20]
"""
import json, math, os, subprocess, tempfile, urllib.request
import pcbnew

try:
    from . import kpaths  # inside the MyPlugin package
except ImportError:
    import kpaths  # run as a script from this folder

JAR_URL = "https://github.com/freerouting/freerouting/releases"
JAVA_URL = "https://adoptium.net/temurin/releases/"


def find_jar():
    """FREEROUTING_JAR env, then MyPlugin's per-user folder, then next to this file, then ~/.kicad-mcp (MCP setups)."""
    for p in (os.environ.get("FREEROUTING_JAR"), os.path.join(kpaths.user_data_dir(), "freerouting.jar"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "freerouting.jar"),
              os.path.join(os.path.expanduser("~"), ".kicad-mcp", "freerouting.jar")):
        if p and os.path.exists(p):
            return p
    return None


def download_jar():
    """Latest official Freerouting release jar -> MyPlugin's per-user folder. Returns the path."""
    api = "https://api.github.com/repos/freerouting/freerouting/releases/latest"
    rel = json.load(urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent": "MyPlugin"}), timeout=60))
    asset = next(a for a in rel["assets"] if a["name"].endswith(".jar") and "freerouting" in a["name"].lower())
    data = urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "MyPlugin"}),
                                  timeout=300).read()
    out = os.path.join(kpaths.user_data_dir(), "freerouting.jar")
    open(out, "wb").write(data)
    return out
AGAINST = 8.0     # cost multiplier for routing against a layer's direction (Freerouting default ~2.5)
VIA_COST = 50


def rules_text(board, against=AGAINST, design="board"):
    """design must equal the DSN's (pcb <name>) or Freerouting may drop the rules."""
    layers = list(board.GetEnabledLayers().CuStack())
    out = ["(rules PCB %s" % design, "  (snap_angle fortyfive_degree)", "  (autoroute_settings",
           "    (fanout off) (autoroute on) (postroute on) (vias on)",
           "    (via_costs %d) (plane_via_costs 5) (start_ripup_costs 100) (start_pass_no 1)" % VIA_COST]
    for i, l in enumerate(layers):
        d = "horizontal" if i % 2 == 0 else "vertical"  # F.Cu horizontal, next vertical, ...
        out.append("    (layer_rule %s (active on) (preferred_direction %s)"
                   " (preferred_direction_trace_costs 1.0) (against_preferred_direction_trace_costs %g))"
                   % (board.GetLayerName(l), d, against))
    return "\n".join(out + ["  )", ")"]) + "\n"


def direction_report(board):
    """{layer: (horizontal_mm, vertical_mm, diagonal_mm)} of routed track length."""
    rep = {}
    for t in board.GetTracks():
        if t.GetClass() != "PCB_TRACK":
            continue
        dx, dy = t.GetEnd().x - t.GetStart().x, t.GetEnd().y - t.GetStart().y
        ln = pcbnew.ToMM(math.hypot(dx, dy))
        a = math.degrees(math.atan2(abs(dy), abs(dx)))
        rep.setdefault(board.GetLayerName(t.GetLayer()), [0.0, 0.0, 0.0])[0 if a < 10 else 1 if a > 80 else 2] += ln
    return rep


def route(board, against=AGAINST, passes=20, jar=None, log=print, tick=None):
    jar = jar or find_jar()
    if not jar:
        raise RuntimeError("Freerouting not found. Download freerouting-<ver>.jar from %s and save it as %s"
                           % (JAR_URL, os.path.join(kpaths.user_data_dir(), "freerouting.jar")))
    java = kpaths.java()
    if not java:
        raise RuntimeError("Java not found. Install a current Java (e.g. Temurin 25) from %s" % JAVA_URL)
    d = tempfile.mkdtemp(prefix="autoroute_hv_")
    dsn, ses, rules = (os.path.join(d, n) for n in ("board.dsn", "board.ses", "hv.rules"))
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        raise RuntimeError("DSN export failed")
    open(rules, "w").write(rules_text(board, against, "board"))  # Freerouting's design name = DSN file stem
    # fanout off: its neck-down stubs ignore netclass widths (0.15 mm traces on a 0.2 mm board);
    # it must be a CLI setting, Freerouting 2.5 ignores "(fanout off)" in the rules file
    cmd = [java, "-jar", jar, "-de", dsn, "-dr", rules, "-do", ses,
           "--router.autorouter.max_passes=%d" % passes, "--router.fanout.enabled=false",
           # never shrink traces below the netclass width to squeeze a net through ("necked retry")
           "--router.automatic_neckdown=false"]
    log("running: " + " ".join(cmd))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console window popping up on Windows
    # cwd = its temp folder: Freerouting drops a logs/ folder into its working directory
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=flags, cwd=d)
    tail = []
    for line in p.stdout:  # progress lines; tick() may return False to cancel
        tail = (tail + [line])[-30:]
        if tick and tick(line.strip()) is False:
            p.kill()
            raise RuntimeError("cancelled")
    p.wait()
    if not os.path.exists(ses):
        out = "".join(tail)
        if "UnsupportedClassVersionError" in out:
            raise RuntimeError("This Freerouting needs a newer Java than %s.\nInstall the current Java (Temurin) from %s "
                               "and run Autoroute again." % (java, JAVA_URL))
        raise RuntimeError("Freerouting produced no session:\n" + out[-2000:])
    if not pcbnew.ImportSpecctraSES(board, ses):
        raise RuntimeError("SES import failed")
    return ses


def summary(board):
    lines = []
    for layer, (h, v, dg) in sorted(direction_report(board).items()):
        tot = h + v + dg or 1
        lines.append("%-6s horizontal %4.0f%%  vertical %4.0f%%  diagonal %3.0f%%  (%.0f mm)"
                     % (layer, 100 * h / tot, 100 * v / tot, 100 * dg / tot, tot))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())  # GND pads connect through zones; count like DRC does
    board.BuildConnectivity()
    c = board.GetConnectivity()
    c.RecalculateRatsnest()
    lines.append("unrouted connections: %d" % c.GetUnconnectedCount(False))
    return "\n".join(lines)


class AutorouteHV(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Autoroute H/V (Freerouting)"
        self.category = "Routing"
        self.description = "Freerouting with horizontal top / vertical bottom, using the board's netclasses"
        self.show_toolbar_button = True

    def Run(self):
        import wx
        board = pcbnew.GetBoard()
        if not kpaths.java():
            wx.MessageBox("Java was not found on this computer.\nFreerouting needs a current Java - install Temurin from\n%s\n"
                          "then run Autoroute again." % JAVA_URL, "Autoroute H/V")
            return
        if not find_jar():
            if wx.MessageBox("Freerouting is not installed.\nDownload the latest official release now (~65 MB, from\n%s)?"
                             % JAR_URL, "Autoroute H/V", wx.YES_NO) != wx.YES:
                return
            busy = wx.BusyInfo("Downloading Freerouting...")
            try:
                download_jar()
            except Exception as e:
                del busy
                wx.MessageBox("Download failed: %s\n\nGet freerouting-<version>.jar from %s and save it as\n%s"
                              % (e, JAR_URL, os.path.join(kpaths.user_data_dir(), "freerouting.jar")), "Autoroute H/V")
                return
            del busy
        prog = wx.ProgressDialog("Autoroute H/V", "Starting Freerouting...", parent=None,
                                 style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT | wx.PD_ELAPSED_TIME)

        def tick(line):
            msg = line[line.find("INFO") + 5:][:110] if "pass #" in line or "stage" in line else None
            cont, _ = prog.Pulse(msg) if msg else prog.Pulse()
            return cont
        try:
            route(board, log=lambda *_: None, tick=tick)
        except Exception as e:
            prog.Destroy()
            wx.MessageBox(str(e), "Autoroute H/V")
            return
        prog.Destroy()
        pcbnew.Refresh()
        wx.MessageBox(summary(board) + "\n\nRun DRC to confirm.", "Autoroute H/V")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("-o", "--out")
    ap.add_argument("--against", type=float, default=AGAINST)
    ap.add_argument("--passes", type=int, default=20)
    a = ap.parse_args()
    b = pcbnew.LoadBoard(a.board)
    route(b, a.against, a.passes)
    pcbnew.SaveBoard(a.out or a.board, b)
    print(summary(b))
