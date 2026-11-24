"""Boards, versions and daughter boards inside one project (one git repo).

    MyProject/
    ├── project.json          boards, their versions and which board each daughter belongs to
    ├── 01_Source/
    │   ├── Main_v1/          a KiCad project per board version: Main_v1.kicad_pro / .kicad_pcb / .kicad_sch
    │   ├── Main_v2/          copied from v1 by "next version"; v1 stays as it was (git tag Main-v1)
    │   └── Display_v1/       daughter board of Main v2: Main's outline, holes and connectors are drawn on a
    │                         user layer named "Main board", same coordinates, so the boards line up when stacked
    └── 03_Manufacturing/Main_v2/ ...   outputs per board version (kpaths.project_folder)

A project made before this (KiCad files straight in 01_Source/) keeps working: that board counts as v1.

CLI (KiCad's python):
  boards.py next     <board.kicad_pcb>                 start the next version
  boards.py daughter <main.kicad_pcb> <Name>           new daughter board of that board
  boards.py refresh  <daughter.kicad_pcb>              redraw the main board's outline from its file
  boards.py tree     <board.kicad_pcb>                 boards and versions of the project
"""
import json, os, re, shutil, sys, uuid

try:
    from . import kpaths, project_git
except ImportError:
    import kpaths, project_git

PROJECT_FILE = "project.json"
OUTLINE_LAYER_NAME = "Main board"
OUTLINE_GROUP = "Main board outline"
KICAD_EXT = (".kicad_pro", ".kicad_pcb", ".kicad_sch", ".kicad_prl", ".kicad_dru")


def split_name(folder_or_file):
    """'Main_v2' -> ('Main', 2); 'Main' -> ('Main', 1)."""
    base = os.path.splitext(os.path.basename(folder_or_file))[0]
    m = re.match(r"^(.*)_v(\d+)$", base)
    return (m.group(1), int(m.group(2))) if m else (base, 1)


def project_root(board_file):
    """Folder holding 01_Source (or project.json): works for 01_Source/<board>.kicad_pcb, 01_Source/<Board_vN>/...,
    or a board on its own (then its folder)."""
    d = os.path.dirname(os.path.abspath(board_file))
    for cand in (d, os.path.dirname(d), os.path.dirname(os.path.dirname(d))):
        if os.path.isfile(os.path.join(cand, PROJECT_FILE)) or os.path.isdir(os.path.join(cand, kpaths.PROJECT_FOLDERS[0])):
            return cand
    return d


def load(root):
    try:
        return json.load(open(os.path.join(root, PROJECT_FILE), encoding="utf-8"))
    except (OSError, ValueError):
        return {"boards": {}}


def save(root, data):
    with open(os.path.join(root, PROJECT_FILE), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def register(root, board_file, parent=None):
    """Record a board version (and its main board) in project.json. Returns (name, version)."""
    data = load(root)
    name, ver = split_name(os.path.dirname(board_file) if os.path.basename(os.path.dirname(board_file)) != kpaths.PROJECT_FOLDERS[0]
                           else board_file)
    entry = data["boards"].setdefault(name, {"versions": {}})
    v = entry["versions"].setdefault(str(ver), {})
    v["folder"] = os.path.relpath(os.path.dirname(os.path.abspath(board_file)), root).replace("\\", "/")
    v["file"] = os.path.basename(board_file)
    if parent:
        entry["main_board"] = parent[0]
        v["main_board"] = "%s v%d" % parent[:2]
        v["main_file"] = os.path.relpath(parent[2], root).replace("\\", "/")
    save(root, data)
    return name, ver


def tree(root):
    """Text overview: boards, versions, daughters under their main board."""
    data = load(root)["boards"]
    lines = []

    def show(name, indent):
        b = data[name]
        vers = sorted(b["versions"], key=int)
        desc = ", ".join("v%s%s" % (v, " (on %s)" % b["versions"][v]["main_board"] if b["versions"][v].get("main_board") else "")
                         for v in vers)
        lines.append("%s%s: %s   [%s]" % ("    " * indent, name, desc, b["versions"][vers[-1]]["folder"]))
        for d in sorted(n for n in data if data[n].get("main_board") == name):
            show(d, indent + 1)

    for name in sorted(n for n in data if not data[n].get("main_board") or data[n]["main_board"] not in data):
        show(name, 0)
    return "\n".join(lines) or "No boards recorded yet (project.json is written by next version / daughter board)."


def _copy_project(src_dir, src_base, dst_dir, dst_base):
    """Copy a KiCad project folder; files named after the project get the new name. Backups and caches stay behind."""
    skip = re.compile(r"(-backups|\.lck|-bak|fp-info-cache|_autosave)", re.I)
    os.makedirs(dst_dir)
    for n in os.listdir(src_dir):
        if skip.search(n):
            continue
        s = os.path.join(src_dir, n)
        stem, ext = os.path.splitext(n)
        d = os.path.join(dst_dir, (dst_base + ext) if stem == src_base and ext in KICAD_EXT else n)
        (shutil.copytree if os.path.isdir(s) else shutil.copy2)(s, d)
    _rename_pro(dst_dir, dst_base)
    return os.path.join(dst_dir, dst_base + ".kicad_pcb")


def _rename_pro(dir_, base):
    """The .kicad_pro stores its own file name: keep it matching after a copy."""
    pro = os.path.join(dir_, base + ".kicad_pro")
    if os.path.isfile(pro):
        try:
            with open(pro, encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("meta", {})["filename"] = base + ".kicad_pro"
            with open(pro, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except ValueError:
            pass


def next_version(board_file, note=""):
    """Tag the current version in git, copy it to <Name>_v<N+1>, commit. Returns the new board file."""
    root = project_root(board_file)
    project_git.scaffold(root, split_name(board_file)[0], source_too=False)
    project_git.ensure_git(root)
    name, ver = register(root, board_file)
    project_git.commit(root, "%s v%d%s" % (name, ver, (": " + note) if note else ""))
    tag = "%s-v%d" % (name, ver)
    if tag not in project_git.git(root, "tag", "--list", tag):
        project_git.git(root, "tag", "-a", tag, "-m", "%s version %d" % (name, ver))
    src_dir, src_base = os.path.dirname(os.path.abspath(board_file)), os.path.splitext(os.path.basename(board_file))[0]
    new_base = "%s_v%d" % (name, ver + 1)
    dst_dir = os.path.join(root, kpaths.PROJECT_FOLDERS[0], new_base)
    if os.path.exists(dst_dir):
        raise RuntimeError("%s already exists." % dst_dir)
    if os.path.basename(src_dir) == kpaths.PROJECT_FOLDERS[0]:
        # old flat layout: copy only this project's files, not the other boards' folders
        os.makedirs(dst_dir)
        for n in os.listdir(src_dir):
            stem, ext = os.path.splitext(n)
            if stem == src_base and ext in KICAD_EXT:
                shutil.copy2(os.path.join(src_dir, n), os.path.join(dst_dir, new_base + ext))
        _rename_pro(dst_dir, new_base)
        new = os.path.join(dst_dir, new_base + ".kicad_pcb")
    else:
        new = _copy_project(src_dir, src_base, dst_dir, new_base)
    data = load(root)
    old = data["boards"][name]["versions"][str(ver)]
    register(root, new, parent=_parent_of(root, old))
    project_git.commit(root, "Start %s v%d (from v%d)" % (name, ver + 1, ver))
    return new


def _parent_of(root, version_entry):
    if not version_entry.get("main_file"):
        return None
    pname, pver = version_entry["main_board"].rsplit(" v", 1)
    return pname, int(pver), os.path.join(root, version_entry["main_file"])


def _blank_project(dir_, base):
    os.makedirs(dir_)
    json.dump({"meta": {"filename": base + ".kicad_pro", "version": 3}}, open(os.path.join(dir_, base + ".kicad_pro"), "w"), indent=2)
    open(os.path.join(dir_, base + ".kicad_sch"), "w", newline="\n").write(
        '(kicad_sch\n\t(version 20260306)\n\t(generator "eeschema")\n\t(generator_version "10.0")\n'
        '\t(uuid %s)\n\t(paper "A4")\n\t(lib_symbols)\n\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n'
        '\t\t)\n\t)\n\t(embedded_fonts no)\n)\n' % uuid.uuid4())
    return os.path.join(dir_, base + ".kicad_pcb")


def new_daughter(main_file, name, main_board=None):
    """New board <Name>_v1 next to the main board, with the main board's outline on a user layer. Commits."""
    import pcbnew
    root = project_root(main_file)
    project_git.scaffold(root, split_name(main_file)[0], source_too=False)
    project_git.ensure_git(root)
    pname, pver = register(root, main_file)
    name = re.sub(r"[^\w-]+", "_", name.strip()).strip("_")
    if not name:
        raise RuntimeError("Give the daughter board a name.")
    base = "%s_v1" % name
    dir_ = os.path.join(root, kpaths.PROJECT_FOLDERS[0], base)
    if os.path.exists(dir_):
        raise RuntimeError("%s already exists." % dir_)
    pcb = _blank_project(dir_, base)
    board = pcbnew.BOARD()
    n = draw_outline(board, main_board or pcbnew.LoadBoard(main_file))
    pcbnew.SaveBoard(pcb, board)
    register(root, pcb, parent=(pname, pver, main_file))
    project_git.commit(root, "New daughter board %s v1 on %s v%d" % (name, pname, pver))
    return pcb, n


def pcbnew_mod():
    import pcbnew
    return pcbnew


def draw_outline(board, main):
    """Main board's Edge.Cuts, holes and connectors (J/P/CN refs + mounting holes) as graphics on the "Main board"
    user layer, in one group (replaced on refresh). Same coordinates as the main board. Returns items drawn."""
    pcbnew = pcbnew_mod()
    layer = pcbnew.User_1
    enabled = board.GetEnabledLayers()
    enabled.AddLayer(layer)
    board.SetEnabledLayers(enabled)
    board.SetLayerName(layer, OUTLINE_LAYER_NAME)
    for g in list(board.Groups()):
        if g.GetName() == OUTLINE_GROUP:
            for it in list(g.GetItems()):
                board.Remove(it)
            board.Remove(g)
    group = pcbnew.PCB_GROUP(board)
    group.SetName(OUTLINE_GROUP)
    board.Add(group)
    items = []
    for d in main.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts and isinstance(d, pcbnew.PCB_SHAPE):
            c = d.Duplicate()
            c.SetLayer(layer)
            items.append(c)
    conn = re.compile(r"^(J|P|CN|X|H|MH|MT)\d", re.I)
    for fp in main.GetFootprints():
        ref = fp.GetReference()
        hole = "mountinghole" in fp.GetFPIDAsString().lower().replace("_", "")
        if not (conn.match(ref) or hole):
            continue
        for p in fp.Pads():
            r = max(p.GetSize(pcbnew.F_Cu).x, p.GetSize(pcbnew.F_Cu).y) // 2 if hasattr(p, "GetSize") else pcbnew.FromMM(0.8)
            s = pcbnew.PCB_SHAPE(board)
            s.SetShape(pcbnew.SHAPE_T_CIRCLE)
            s.SetCenter(p.GetPosition())
            s.SetEnd(p.GetPosition() + pcbnew.VECTOR2I(int(r), 0))
            s.SetLayer(layer)
            s.SetWidth(pcbnew.FromMM(0.1))
            items.append(s)
        t = pcbnew.PCB_TEXT(board)
        t.SetText("%s (%s)" % (ref, OUTLINE_LAYER_NAME.lower()))
        t.SetPosition(fp.GetPosition())
        t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(1), pcbnew.FromMM(1)))
        items.append(t)
    for it in items:
        board.Add(it)
        group.AddItem(it)
    return len(items)


def refresh_outline(board_file, board=None):
    """Redraw the main board's outline on a daughter board from the main board file recorded in project.json."""
    import pcbnew
    root = project_root(board_file)
    name, ver = split_name(os.path.dirname(board_file))
    v = load(root)["boards"].get(name, {}).get("versions", {}).get(str(ver), {})
    if not v.get("main_file"):
        raise RuntimeError("%s v%d is not a daughter board (no main board in project.json)." % (name, ver))
    main_file = os.path.join(root, v["main_file"])
    if not os.path.isfile(main_file):
        raise RuntimeError("Main board file is gone: %s" % main_file)
    own = board is None
    board = board or pcbnew.LoadBoard(board_file)
    n = draw_outline(board, pcbnew.LoadBoard(main_file))
    if own:
        pcbnew.SaveBoard(board_file, board)
    return v["main_board"], n


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit
    cmd, f = sys.argv[1], os.path.abspath(sys.argv[2])
    if cmd == "next":
        print(next_version(f))
    elif cmd == "daughter":
        print(new_daughter(f, sys.argv[3]))
    elif cmd == "refresh":
        print(refresh_outline(f))
    elif cmd == "tree":
        print(tree(project_root(f)))
