"""BOM + pick-and-place export for assembly and scripts, straight from the board (no schematic step).

Writes into the project's 03_Manufacturing/ (else <board dir>/bom/):
  <name>_BOM.csv   grouped, JLCPCB columns first: Comment, Designator, Footprint, LCSC Part #, then Qty, Manufacturer, MPN
  <name>_CPL.csv   pick-and-place, JLCPCB columns: Designator, Mid X, Mid Y, Layer, Rotation
  <name>_BOM.json  everything, machine-readable: per group and per part (fields, side, position, rotation, SMD/THT)
Parts marked DNP or "exclude from BOM" are left out (listed in the JSON under "excluded").
Part numbers come from footprint fields; any of these names work: LCSC / LCSC Part / JLCPCB ..., MPN / Manufacturer Part
Number ..., Manufacturer / MFR. Positions are relative to the board's drill/place origin, Y up (like KiCad's .pos).

Toolbar button in PCB editor, or CLI (good for CI / scripts):
  <KiCad python> bom_export.py board.kicad_pcb [outdir]
"""
import csv, json, os, re, sys
import pcbnew

try:
    from . import kpaths  # inside the MyPlugin package
except ImportError:
    import kpaths  # run as a script from this folder

ALIASES = {
    "lcsc": ("lcsc", "lcscpart", "lcscpartnumber", "lcscpart#", "jlcpcb", "jlcpcbpart", "jlc", "jlcpcbpartnumber"),
    "mpn": ("mpn", "manufacturerpartnumber", "mfrpart", "mfrpartnumber", "partnumber", "pn", "mfrpn"),
    "manufacturer": ("manufacturer", "mfr", "mfg", "manufacturername"),
}
SKIP_FIELDS = {"reference", "value", "footprint", "description", "kilib_generator"}


def _key(name):
    return re.sub(r"[\s_\-.:]", "", name.lower())


def part(fp, origin):
    fields = {f.GetName(): f.GetText() for f in fp.GetFields() if f.GetText() and f.GetName().lower() not in SKIP_FIELDS}
    std = {k: next((v for n, v in fields.items() if _key(n) in names), "") for k, names in ALIASES.items()}
    pos = fp.GetPosition()
    return {
        "ref": fp.GetReference(), "value": fp.GetValue(), "footprint": str(fp.GetFPID().GetLibItemName()),
        "library": str(fp.GetFPID().GetLibNickname()), **std, "fields": fields,
        "side": "bottom" if fp.IsFlipped() else "top",
        "x_mm": round(pcbnew.ToMM(pos.x - origin.x), 4), "y_mm": round(-pcbnew.ToMM(pos.y - origin.y), 4),
        "rotation": round(fp.GetOrientationDegrees() % 360, 2),
        "smd": bool(fp.GetAttributes() & pcbnew.FP_SMD), "dnp": fp.IsDNP(),
        "no_bom": fp.IsExcludedFromBOM(), "no_pos": fp.IsExcludedFromPosFiles(),
    }


def natural(ref):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", ref)]


def collect(board):
    origin = board.GetDesignSettings().GetAuxOrigin()
    parts = sorted((part(fp, origin) for fp in board.GetFootprints()), key=lambda p: natural(p["ref"]))
    used = [p for p in parts if not (p["dnp"] or p["no_bom"])]
    groups = {}
    for p in used:
        groups.setdefault((p["value"], p["footprint"], p["lcsc"], p["mpn"]), []).append(p)
    rows = [{"comment": v, "footprint": f, "lcsc": l, "mpn": m, "manufacturer": ps[0]["manufacturer"],
             "qty": len(ps), "refs": [p["ref"] for p in ps]} for (v, f, l, m), ps in groups.items()]
    rows.sort(key=lambda r: natural(r["refs"][0]))
    return parts, used, rows


def export(board, board_file, outdir=None):
    outdir = outdir or kpaths.project_folder(board_file, "03_Manufacturing", "bom")
    os.makedirs(outdir, exist_ok=True)
    name = os.path.splitext(os.path.basename(board_file))[0]
    parts, used, rows = collect(board)
    bom, cpl, js = (os.path.join(outdir, name + s) for s in ("_BOM.csv", "_CPL.csv", "_BOM.json"))
    with open(bom, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #", "Qty", "Manufacturer", "MPN"])
        for r in rows:
            w.writerow([r["comment"], ",".join(r["refs"]), r["footprint"], r["lcsc"], r["qty"], r["manufacturer"], r["mpn"]])
    with open(cpl, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for p in used:
            if not p["no_pos"]:
                w.writerow([p["ref"], "%.4fmm" % p["x_mm"], "%.4fmm" % p["y_mm"], p["side"].capitalize(), p["rotation"]])
    missing = [r for r in rows if not (r["lcsc"] or r["mpn"])]
    with open(js, "w", encoding="utf-8") as f:
        json.dump({"board": os.path.basename(board_file), "units": "mm", "origin": "drill/place origin, Y up",
                   "line_count": len(rows), "part_count": len(used), "bom": rows, "parts": used,
                   "excluded": [p["ref"] for p in parts if p["dnp"] or p["no_bom"]],
                   "missing_part_numbers": [",".join(r["refs"]) for r in missing]}, f, indent=2)
    return outdir, [bom, cpl, js], rows, missing


class BomExport(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "BOM + CPL Export"
        self.category = "Manufacturing"
        self.description = "Grouped BOM (CSV/JSON) and pick-and-place file for assembly and scripts"
        self.show_toolbar_button = False

    def Run(self):
        import wx
        board = pcbnew.GetBoard()
        path = board.GetFileName()
        if not path:
            wx.MessageBox("Save the board first.", "BOM Export")
            return
        outdir, files, rows, missing = export(board, path)
        msg = "%d lines, %d parts. Saved to %s:\n%s" % (len(rows), sum(r["qty"] for r in rows), outdir,
                                                       "\n".join(" - " + os.path.basename(f) for f in files))
        if missing:
            msg += ("\n\nNo LCSC / MPN field (add one in the schematic, then Update PCB):\n"
                    + "\n".join("  " + r["comment"] + "  " + ",".join(r["refs"]) for r in missing))
        wx.MessageBox(msg, "BOM Export")
        if hasattr(os, "startfile"):
            os.startfile(outdir)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit
    b = pcbnew.LoadBoard(sys.argv[1])
    d, files, rows, missing = export(b, sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print("%d lines -> %s" % (len(rows), d))
    for r in missing:
        print("no part number:", ",".join(r["refs"]))
