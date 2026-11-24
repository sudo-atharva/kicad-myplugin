"""Footprint (+ symbol + 3D model) of an LCSC / EasyEDA part, by its LCSC number (e.g. C2040).

Uses easyeda2kicad (https://github.com/uPesy/easyeda2kicad.py, offered as a one-click pip install into KiCad's
Python). Everything lands next to the board, so the project stays portable:
    libs/easyeda.pretty/       footprints      -> library "EasyEDA" in the project's fp-lib-table
    libs/easyeda.kicad_sym     symbols         -> library "EasyEDA" in the project's sym-lib-table
    libs/easyeda.3dshapes/     3D models       -> referenced as ${KIPRJMOD}/libs/easyeda.3dshapes/...
The footprint is placed on the board with an "LCSC" field (BOM + CPL Export puts it in the JLCPCB column).

CLI (KiCad's python): easyeda_import.py <board.kicad_pcb> C2040
"""
import os, re, subprocess, sys

try:
    from . import kpaths
except ImportError:
    import kpaths

LIB = "EasyEDA"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def lib_base(board_file):
    return os.path.join(os.path.dirname(os.path.abspath(board_file)), "libs", "easyeda")


def have_converter():
    r = subprocess.run([kpaths.kicad_python(), "-c", "import easyeda2kicad"], capture_output=True, creationflags=NOWIN)
    return r.returncode == 0


def add_lib(table_file, kind, uri):
    """Add LIB to a project fp-lib-table / sym-lib-table (created if missing). Returns True if added."""
    head = "fp_lib_table" if kind == "fp" else "sym_lib_table"
    entry = '  (lib (name "%s")(type "KiCad")(uri "%s")(options "")(descr "LCSC / EasyEDA parts (easyeda2kicad)"))\n' % (LIB, uri)
    if os.path.isfile(table_file):
        text = open(table_file, encoding="utf-8").read()
        if re.search(r'\(name\s+"?%s"?\)' % LIB, text):
            return False
        i = text.rstrip().rfind(")")
        text = text[:i].rstrip() + "\n" + entry + ")\n"
    else:
        text = "(%s\n  (version 7)\n%s)\n" % (head, entry)
    with open(table_file, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


def fix_models(mod_file):
    """3D model paths -> ${KIPRJMOD}/libs/easyeda.3dshapes/<file> (the converter may write absolute paths)."""
    text = open(mod_file, encoding="utf-8").read()
    new = re.sub(r'\(model\s+"?([^"\s)]+)"?',
                 lambda m: '(model "${KIPRJMOD}/libs/easyeda.3dshapes/%s"' % os.path.basename(m.group(1).replace("\\", "/")),
                 text)
    if new != text:
        with open(mod_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)


def snapshot(d):
    return {n: os.path.getmtime(os.path.join(d, n)) for n in os.listdir(d)} if os.path.isdir(d) else {}


def convert(board_file, lcsc):
    """Run easyeda2kicad for one part. Returns the footprint names it wrote (new or updated)."""
    lcsc = lcsc.strip().upper()
    if not re.match(r"^C\d+$", lcsc):
        raise RuntimeError("'%s' is not an LCSC number (like C2040: the 'C' number on lcsc.com / JLCPCB)." % lcsc)
    base = lib_base(board_file)
    os.makedirs(os.path.dirname(base), exist_ok=True)
    pretty = base + ".pretty"
    before = snapshot(pretty)
    r = subprocess.run([kpaths.kicad_python(), "-m", "easyeda2kicad", "--full", "--lcsc_id", lcsc, "--output", base, "--overwrite"],
                       capture_output=True, text=True, creationflags=NOWIN)
    after = snapshot(pretty)
    made = sorted(os.path.splitext(n)[0] for n, t in after.items() if n.endswith(".kicad_mod") and before.get(n) != t)
    if not made:
        raise RuntimeError("easyeda2kicad made no footprint for %s:\n%s" % (lcsc, (r.stderr or r.stdout).strip()[-1500:]))
    for n in made:
        fix_models(os.path.join(pretty, n + ".kicad_mod"))
    d = os.path.dirname(os.path.abspath(board_file))
    add_lib(os.path.join(d, "fp-lib-table"), "fp", "${KIPRJMOD}/libs/easyeda.pretty")
    if os.path.isfile(base + ".kicad_sym"):
        add_lib(os.path.join(d, "sym-lib-table"), "sym", "${KIPRJMOD}/libs/easyeda.kicad_sym")
    return made


def place(board, board_file, name, lcsc, at=None):
    """Put the footprint on the board (library EasyEDA, field LCSC). Returns the footprint."""
    import pcbnew
    fp = pcbnew.FootprintLoad(lib_base(board_file) + ".pretty", name)
    if fp is None:
        raise RuntimeError("KiCad could not read the footprint %s." % name)
    fp.SetFPID(pcbnew.LIB_ID(LIB, name))
    fp.SetReference("U?" if fp.GetReference() in ("", "REF**") else fp.GetReference())
    fp.SetField("LCSC", lcsc)  # BOM + CPL Export reads it into the JLCPCB "LCSC Part #" column
    if fp.HasField("LCSC"):
        fp.GetField("LCSC").SetVisible(False)
    board.Add(fp)
    if at is None:
        bb = board.GetBoardEdgesBoundingBox()
        at = bb.GetCenter() if bb.GetWidth() > 0 else pcbnew.VECTOR2I(pcbnew.FromMM(100), pcbnew.FromMM(100))
    fp.SetPosition(at)
    return fp


class EasyEdaImport(object):
    def Run(self):
        import wx, pcbnew
        board = pcbnew.GetBoard()
        board_file = board.GetFileName()
        if not board_file:
            wx.MessageBox("Save the board first: the part's library goes into libs/ next to it.", "LCSC / EasyEDA part")
            return
        if not have_converter():
            if wx.MessageBox("Getting parts from EasyEDA needs easyeda2kicad (free, open source) in KiCad's Python.\n"
                             "Install it now (pip, needs internet)?", "LCSC / EasyEDA part", wx.YES_NO) != wx.YES:
                return
            busy = wx.BusyInfo("Installing easyeda2kicad...")
            try:
                kpaths.pip_install("easyeda2kicad")
            finally:
                del busy
        lcsc = wx.GetTextFromUser("LCSC part number (the 'C' number on lcsc.com / JLCPCB parts), e.g. C2040:",
                                  "LCSC / EasyEDA part", "")
        if not lcsc.strip():
            return
        try:
            busy = wx.BusyInfo("Getting %s from EasyEDA..." % lcsc.strip().upper())
            try:
                names = convert(board_file, lcsc)
            finally:
                del busy  # always close the "please wait" box, also on errors
        except RuntimeError as e:
            wx.MessageBox(str(e), "LCSC / EasyEDA part")
            return
        fps = [place(board, board_file, n, lcsc.strip().upper()) for n in names]
        pcbnew.Refresh()
        wx.MessageBox("Placed %s (LCSC %s) in the middle of the board: move it into place.\n\n"
                      "Library 'EasyEDA' (libs/ next to the board) now has its footprint, symbol and 3D model, "
                      "so you can also use it in the schematic." % (", ".join(n for n in names), lcsc.strip().upper()),
                      "LCSC / EasyEDA part")
        return fps


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit
    print(convert(sys.argv[1], sys.argv[2]))
