"""Project layout + version control (git) for KiCad projects.

New project:   <parent>/<Name>/ with 01_Source (blank KiCad project) ... 07_FreeCAD, README.md, .gitignore,
               git repo with a first commit.
Current board: adds the missing layout folders and .gitignore, sets up git if needed, then "Save version" commits
               everything with your message, and shows the recent history.

CLI:
  <KiCad python> project_git.py new <parent dir> <Name>
  <KiCad python> project_git.py commit board.kicad_pcb "message"
  <KiCad python> project_git.py log board.kicad_pcb
"""
import json, os, shutil, subprocess, sys, uuid

try:
    from . import kpaths  # inside the MyPlugin package
except ImportError:
    import kpaths  # run as a script from this folder

FOLDERS = kpaths.PROJECT_FOLDERS
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GITIGNORE = """# KiCad: backups, caches, locks, local UI state
*-backups/
*.kicad_pcb-bak
*.kicad_sch-bak
*-bak
*.bak
_autosave-*
\\#auto_saved_files#
.history/
fp-info-cache
*.lck
~*.lck
*.kicad_prl
# autorouter / tool scratch
*.dsn
*.ses
freerouting.json
logs/
# FreeCAD backups
*.FCBak
# OS
Thumbs.db
.DS_Store
"""
README = """# {name}

**Status:** idea
**Spec:**

## Layout
| Folder | What goes in |
|---|---|
| `01_Source/` | KiCad project (schematic, board) |
| `02_Datasheets/` | PDFs of the parts used |
| `03_Manufacturing/` | Gerbers, drill, BOM + CPL (MyPlugin > BOM Export) |
| `04_Docs/` | Drawings, renders (MyPlugin > Docs), notes |
| `05_Testing/` | Bring-up notes, measurements |
| `06_Code/` | Firmware |
| `07_FreeCAD/` | Enclosure / mechanical (MyPlugin > FreeCAD) |
"""


def git(root, *args):
    exe = shutil.which("git")
    if not exe:
        raise RuntimeError("git is not installed. Get it from https://git-scm.com/downloads and try again.")
    r = subprocess.run([exe, *args], cwd=root, capture_output=True, text=True, creationflags=NOWIN)
    if r.returncode:
        raise RuntimeError("git %s failed:\n%s" % (args[0], (r.stderr or r.stdout).strip()))
    return r.stdout


def project_root(board_file):
    """The project folder: holds 01_Source (board in 01_Source/ or in 01_Source/<Board_vN>/), else the board's folder."""
    d = os.path.dirname(os.path.abspath(board_file))
    for cand in (os.path.dirname(d), os.path.dirname(os.path.dirname(d))):
        if os.path.basename(d)[:3] == "01_" and cand == os.path.dirname(d):
            return cand
        if os.path.isdir(os.path.join(cand, FOLDERS[0])) or os.path.isfile(os.path.join(cand, "project.json")):
            return cand
    return d


def scaffold(root, name, source_too=True):
    """Create missing layout folders (+ .gitkeep so git keeps empty ones), README.md, .gitignore. Returns what was made."""
    made = []
    for f in FOLDERS if source_too else FOLDERS[1:]:
        p = os.path.join(root, f)
        if not os.path.isdir(p):
            os.makedirs(p)
            open(os.path.join(p, ".gitkeep"), "w").close()
            made.append(f + "/")
    for fn, text in ((".gitignore", GITIGNORE), ("README.md", README.format(name=name))):
        p = os.path.join(root, fn)
        if not os.path.exists(p):
            open(p, "w", encoding="utf-8", newline="\n").write(text)
            made.append(fn)
    return made


def ensure_git(root):
    """git init + first commit if root is not inside a repo yet. Returns True if it created the repo."""
    try:
        git(root, "rev-parse", "--show-toplevel")
        return False
    except RuntimeError as e:
        if "not installed" in str(e):
            raise
    git(root, "init", "-b", "main")
    commit(root, "Project created")
    return True


def commit(root, message):
    """Stage everything (respecting .gitignore) and commit. Returns the short hash, or None if nothing changed."""
    # "." everywhere: when the project lives inside a bigger repo, only this project's folder is versioned here
    git(root, "add", "-A", ".")
    if not git(root, "status", "--porcelain", ".").strip():
        return None
    try:
        git(root, "commit", "-m", message, "--", ".")
    except RuntimeError as e:
        if "user.email" in str(e) or "identity" in str(e).lower():
            raise RuntimeError("git does not know who you are yet. Run once in a terminal:\n"
                               '  git config --global user.name "Your Name"\n'
                               '  git config --global user.email "you@example.com"')
        raise
    return git(root, "rev-parse", "--short", "HEAD").strip()


def changes(root):
    return git(root, "status", "--short", ".").strip()


def history(root, n=15):
    return git(root, "log", "-%d" % n, "--date=format:%Y-%m-%d %H:%M", "--pretty=%h  %ad  %s", "--", ".").strip()


def new_project(parent, name):
    """Full layout + blank KiCad project in 01_Source + git. Returns the .kicad_pro path."""
    root = os.path.join(parent, name)
    if os.path.exists(root) and os.listdir(root):
        raise RuntimeError("%s already exists and is not empty." % root)
    os.makedirs(root, exist_ok=True)
    scaffold(root, name)
    src = os.path.join(root, FOLDERS[0])
    os.remove(os.path.join(src, ".gitkeep"))
    pro = os.path.join(src, name + ".kicad_pro")
    json.dump({"meta": {"filename": name + ".kicad_pro", "version": 3}}, open(pro, "w"), indent=2)
    open(os.path.join(src, name + ".kicad_sch"), "w", newline="\n").write(
        '(kicad_sch\n\t(version 20260306)\n\t(generator "eeschema")\n\t(generator_version "10.0")\n'
        '\t(uuid %s)\n\t(paper "A4")\n\t(lib_symbols)\n\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n'
        '\t\t)\n\t)\n\t(embedded_fonts no)\n)\n' % uuid.uuid4())
    import pcbnew
    pcbnew.SaveBoard(os.path.join(src, name + ".kicad_pcb"), pcbnew.BOARD())
    ensure_git(root)
    return pro


class ProjectGit(object):
    def Run(self):
        import wx, pcbnew
        board_file = pcbnew.GetBoard().GetFileName()
        opts = ["Save a version of this project (git commit)",
                "Show boards and history of this project",
                "Start the next version of this board (v1 -> v2, old one kept and tagged)",
                "New daughter board of this board (main outline on a 'Main board' layer)",
                "Refresh the main board outline (on a daughter board)",
                "Show this daughter board on its main board in 3D (stack height)",
                "New project with the standard layout..."]
        if not board_file:
            opts = opts[-1:]
        dlg = wx.SingleChoiceDialog(None, "Project folders: 01_Source ... 07_FreeCAD, each board version in 01_Source/<Board>_vN, "
                                    "versions kept with git.", "Project & Versions", opts)
        ok, pick = dlg.ShowModal() == wx.ID_OK, dlg.GetStringSelection()
        dlg.Destroy()
        if not ok:
            return
        try:
            if pick.startswith("New project"):
                self.new(wx)
            elif pick.startswith(("Start", "New daughter", "Refresh", "Show this")):
                self.boards(wx, pcbnew, board_file, pick)
            else:
                self.current(wx, pcbnew, board_file, pick.startswith("Show"))
        except RuntimeError as e:
            wx.MessageBox(str(e), "Project & Versions")

    def boards(self, wx, pcbnew, board_file, pick):
        try:
            from . import boards
        except ImportError:
            import boards
        board = pcbnew.GetBoard()
        if pick.startswith("Show this"):
            h = wx.GetTextFromUser("Gap between the main board's top and this board's bottom (standoff height, mm):",
                                   "3D stack", "10")
            if not h.strip():
                return
            pcbnew.SaveBoard(board_file, board)
            busy = wx.BusyInfo("Exporting this board as STEP...")
            try:
                main_file, step = boards.stack_preview(board_file, float(h))
            finally:
                del busy
            wx.MessageBox("Open %s: its 3D viewer (and FreeCAD StepUp) now shows this board %s mm above it, as the 3D "
                          "model of footprint DB_... (board only, not in BOM or placement files).\nRun this again after "
                          "changing this board." % (os.path.basename(main_file), h.strip()), "Project & Versions")
            return
        if pick.startswith("Refresh"):
            main, n = boards.refresh_outline(board_file, board)
            pcbnew.Refresh()
            wx.MessageBox("Redrew %d items of %s on the '%s' layer. Save to keep it." % (n, main, boards.OUTLINE_LAYER_NAME),
                          "Project & Versions")
            return
        pcbnew.SaveBoard(board_file, board)  # version what you see, not the last save
        if pick.startswith("Start"):
            note = wx.GetTextFromUser("Note for the version you are finishing (optional):", "Next version", "")
            new = boards.next_version(board_file, note.strip())
            text = "This version is saved and tagged in git. The next one is\n%s" % new
        else:
            name = wx.GetTextFromUser("Daughter board name (e.g. Display, Sensor):", "New daughter board", "")
            if not name.strip():
                return
            new, n = boards.new_daughter(board_file, name, board)
            text = ("Created %s\nwith this board's outline, holes and connectors (%d items) on the '%s' layer, at the same "
                    "coordinates. Draw the daughter board's own outline on Edge.Cuts." % (new, n, boards.OUTLINE_LAYER_NAME))
        if wx.MessageBox(text + "\n\nOpen it in KiCad now?", "Project & Versions", wx.YES_NO) == wx.YES and hasattr(os, "startfile"):
            os.startfile(new.replace(".kicad_pcb", ".kicad_pro"))

    def new(self, wx):
        dd = wx.DirDialog(None, "Folder to create the project in (e.g. your PCBs folder)")
        if dd.ShowModal() != wx.ID_OK:
            return
        parent = dd.GetPath()
        dd.Destroy()
        name = wx.GetTextFromUser("Project name (folder and file name, no spaces is safest):", "New project")
        if not name.strip():
            return
        pro = new_project(parent, name.strip())
        if wx.MessageBox("Created %s\nwith 01_Source ... 07_FreeCAD, README.md, .gitignore and a git repo.\n\n"
                         "Open the new project in KiCad now?" % os.path.dirname(os.path.dirname(pro)),
                         "New project", wx.YES_NO) == wx.YES and hasattr(os, "startfile"):
            os.startfile(pro)

    def current(self, wx, pcbnew, board_file, show_only):
        root = project_root(board_file)
        if show_only:
            from . import _show_text, boards
            _show_text("Boards and history - " + root, boards.tree(root) + "\n\n" + (history(root) or "No versions yet."))
            return
        pcbnew.SaveBoard(board_file, pcbnew.GetBoard())  # commit what you see, not the last save
        made = scaffold(root, os.path.splitext(os.path.basename(board_file))[0], source_too=False)
        created = ensure_git(root)
        pending = changes(root)
        if not pending:
            wx.MessageBox(("Set up git in %s.\n" % root if created else "") + "Nothing changed since the last version.",
                          "Project & Versions")
            return
        msg = wx.GetTextFromUser("Changed:\n%s\n\nWhat did you change? (version note)" % pending[:1200],
                                 "Save version", "")
        if not msg.strip():
            return
        h = commit(root, msg.strip())
        wx.MessageBox("Saved version %s in %s%s" % (h, root, ("\nAdded: " + ", ".join(made)) if made else ""),
                      "Project & Versions")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit
    cmd = sys.argv[1]
    if cmd == "new":
        print(new_project(sys.argv[2], sys.argv[3]))
    elif cmd == "commit":
        root = project_root(sys.argv[2])
        scaffold(root, os.path.splitext(os.path.basename(sys.argv[2]))[0], source_too=False)
        ensure_git(root)
        print(commit(root, sys.argv[3] if len(sys.argv) > 3 else "update") or "nothing to commit")
    elif cmd == "log":
        print(history(project_root(sys.argv[2])))
