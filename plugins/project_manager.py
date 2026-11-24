"""MyPlugin Project Manager: projects, boards, versions and git/GitHub without opening KiCad first.

Runs on KiCad's own Python (it has wxPython and pcbnew); start it with "Project Manager.exe" (next to this file) or
  "<KiCad>/bin/pythonw.exe" project_manager.py [project folder]
It uses the same files as the plugin (project.json, 01_Source/<Board>_vN/, git tags <Board>-vN), so both always agree.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if __package__ in (None, ""):
    sys.path.insert(0, HERE)
    import boards, kpaths, project_git  # run as a script
else:
    from . import boards, kpaths, project_git

import wx

RECENT = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MyPlugin", "recent_projects.json")


def recent():
    try:
        return [p for p in json.load(open(RECENT, encoding="utf-8")) if os.path.isdir(p)]
    except (OSError, ValueError):
        return []


def remember(root):
    lst = [root] + [p for p in recent() if os.path.normcase(p) != os.path.normcase(root)]
    os.makedirs(os.path.dirname(RECENT), exist_ok=True)
    json.dump(lst[:12], open(RECENT, "w", encoding="utf-8"), indent=1)


def board_files(root):
    """Every KiCad board of the project: [(label, .kicad_pcb, depth)], main boards first, daughters indented."""
    data = boards.load(root)["boards"]
    out, seen = [], set()

    def show(name, depth):
        b = data[name]
        for v in sorted(b["versions"], key=int):
            e = b["versions"][v]
            pcb = os.path.join(root, e["folder"], e["file"])
            seen.add(os.path.normcase(pcb))
            on = "   (on %s)" % e["main_board"] if e.get("main_board") else ""
            out.append(("%s v%s%s" % (name, v, on), pcb, depth))
        for d in sorted(n for n in data if data[n].get("main_board") == name):
            show(d, depth + 1)

    for name in sorted(n for n in data if not data[n].get("main_board") or data[n]["main_board"] not in data):
        show(name, 0)
    src = os.path.join(root, kpaths.PROJECT_FOLDERS[0])
    for d, _, files in os.walk(src) if os.path.isdir(src) else []:
        if d.count(os.sep) - src.count(os.sep) > 1 or "backups" in d:
            continue
        for f in files:
            pcb = os.path.join(d, f)
            if f.endswith(".kicad_pcb") and os.path.normcase(pcb) not in seen:
                out.append(("%s v%d   (not in project.json yet)" % boards.split_name(f), pcb, 0))
    return out


def pretty_change(line):
    """'?? 05_Testing/a.md' -> 'new       05_Testing/a.md' (git status --short in words)."""
    code, path = line[:2], line[3:]
    word = {"??": "new", "A": "new", "M": "changed", "D": "deleted", "R": "renamed"}.get(code.strip()[:1] if code != "??" else "??", "changed")
    return "%-9s %s" % (word, path)


class Manager(wx.Frame):
    def __init__(self, root=None):
        super().__init__(None, title="Project Manager - MyPlugin", size=(1100, 720))
        ico = os.path.join(HERE, "icons", "project.png")
        if os.path.isfile(ico):
            self.SetIcon(wx.Icon(ico, wx.BITMAP_TYPE_PNG))
        self.root, self.items = None, []
        p = self.panel = wx.Panel(self)

        top = wx.BoxSizer(wx.HORIZONTAL)
        for label, fn in (("New project...", self.on_new), ("Open project...", self.on_open), ("Recent", self.on_recent)):
            b = wx.Button(p, label=label)
            b.Bind(wx.EVT_BUTTON, fn)
            top.Add(b, 0, wx.RIGHT, 6)
        self.path = wx.StaticText(p, label="No project open")
        top.Add(self.path, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)

        # boards
        left = wx.StaticBoxSizer(wx.VERTICAL, p, "Boards")
        lb = left.GetStaticBox()  # controls in a group box belong to the box
        self.boards = wx.ListBox(lb, size=(380, -1))
        self.boards.Bind(wx.EVT_LISTBOX_DCLICK, self.on_kicad)
        left.Add(self.boards, 1, wx.EXPAND | wx.ALL, 4)
        grid = wx.GridSizer(0, 2, 4, 4)
        for label, fn, tip in (("Open in KiCad", self.on_kicad, "Or double-click the board"),
                               ("Next version", self.on_next, "Tag this version in git, copy it to <Board>_v<N+1>"),
                               ("New daughter board...", self.on_daughter, "New board with this board's outline on a 'Main board' layer"),
                               ("Open folder", self.on_folder, "Show the board's folder in Explorer")):
            b = wx.Button(lb, label=label)
            b.SetToolTip(tip)
            b.Bind(wx.EVT_BUTTON, fn)
            grid.Add(b, 0, wx.EXPAND)
        left.Add(grid, 0, wx.EXPAND | wx.ALL, 4)

        # git
        right = wx.StaticBoxSizer(wx.VERTICAL, p, "Version control (git + GitHub)")
        rb = right.GetStaticBox()
        self.status = wx.StaticText(rb, label="", style=wx.ST_ELLIPSIZE_MIDDLE | wx.ST_NO_AUTORESIZE)
        right.Add(self.status, 0, wx.EXPAND | wx.ALL, 4)
        right.Add(wx.StaticText(rb, label="Changed since the last commit:"), 0, wx.LEFT | wx.TOP, 4)
        self.changes = wx.ListBox(rb, size=(-1, 140))
        right.Add(self.changes, 0, wx.EXPAND | wx.ALL, 4)
        right.Add(wx.StaticText(rb, label="What did you change?"), 0, wx.LEFT, 4)
        self.msg = wx.TextCtrl(rb, style=wx.TE_MULTILINE, size=(-1, 54))
        right.Add(self.msg, 0, wx.EXPAND | wx.ALL, 4)
        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, fn in (("Commit", self.on_commit), ("Commit && Push", self.on_commit_push), ("Push", self.on_push),
                          ("Pull", self.on_pull), ("Connect to GitHub...", self.on_github), ("Refresh", self.refresh)):
            b = wx.Button(rb, label=label)
            b.Bind(wx.EVT_BUTTON, fn)
            row.Add(b, 0, wx.RIGHT, 4)
        right.Add(row, 0, wx.ALL, 4)
        right.Add(wx.StaticText(rb, label="History:"), 0, wx.LEFT | wx.TOP, 4)
        self.log = wx.TextCtrl(rb, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.log.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        right.Add(self.log, 1, wx.EXPAND | wx.ALL, 4)

        mid = wx.BoxSizer(wx.HORIZONTAL)
        mid.Add(left, 0, wx.EXPAND | wx.RIGHT, 8)
        mid.Add(right, 1, wx.EXPAND)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(top, 0, wx.EXPAND | wx.ALL, 8)
        s.Add(mid, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        p.SetSizer(s)
        root = root or (recent()[0] if recent() else None)
        if root:
            self.load(root)

    # ---- helpers ----
    def fail(self, e):
        wx.MessageBox(str(e), "Project Manager", wx.ICON_WARNING)

    def busy(self, text, fn, *a):
        with wx.BusyInfo(text, self):
            return fn(*a)

    def current(self):
        i = self.boards.GetSelection()
        if i == wx.NOT_FOUND:
            raise RuntimeError("Pick a board in the list first.")
        return self.items[i][1]

    def locked(self, pcb):
        return os.path.exists(os.path.join(os.path.dirname(pcb), "~%s.lck" % os.path.basename(pcb)))

    def load(self, root):
        self.root = root
        remember(root)
        self.path.SetLabel(root)
        self.refresh()

    def refresh(self, _=None):
        if not self.root:
            return
        self.items = board_files(self.root)
        self.boards.Set(["    " * d + label for label, _, d in self.items])
        try:
            files = project_git.changes(self.root).splitlines()
            url = project_git.remote(self.root)
            ahead, behind = project_git.ahead_behind(self.root)
            sync = "" if ahead is None else ("   |   %d to push, %d on GitHub not here" % (ahead, behind))
            self.status.SetLabel("Branch %s   |   %s%s" % (project_git.branch(self.root), url or "not connected to GitHub", sync))
            self.changes.Set([pretty_change(f) for f in files] or ["(nothing changed)"])
            tags = project_git.git(self.root, "tag", "--list").split()
            self.log.SetValue(project_git.history(self.root, 40) + ("\n\nVersions (tags): " + ", ".join(tags) if tags else ""))
        except RuntimeError as e:
            self.status.SetLabel("No git repo here yet: the first Commit sets it up." if "not a git" in str(e).lower()
                                 or "rev-parse" in str(e) else str(e).splitlines()[0])
            self.changes.Set([])
            self.log.SetValue("")
        self.panel.Layout()

    # ---- project ----
    def on_new(self, _):
        dd = wx.DirDialog(self, "Folder to create the project in (e.g. your PCBs folder)")
        if dd.ShowModal() != wx.ID_OK:
            return
        name = wx.GetTextFromUser("Project name (folder and file name):", "New project", parent=self).strip()
        if not name:
            return
        try:
            pro = project_git.new_project(dd.GetPath(), name)
        except RuntimeError as e:
            return self.fail(e)
        self.load(os.path.dirname(os.path.dirname(pro)))

    def on_open(self, _):
        dd = wx.DirDialog(self, "Project folder (the one with 01_Source in it)")
        if dd.ShowModal() == wx.ID_OK:
            self.load(dd.GetPath())

    def on_recent(self, _):
        lst = recent()
        if not lst:
            return wx.MessageBox("No recent projects yet.", "Project Manager")
        d = wx.SingleChoiceDialog(self, "Recent projects", "Open", lst)
        if d.ShowModal() == wx.ID_OK:
            self.load(d.GetStringSelection())

    # ---- boards ----
    def on_kicad(self, _):
        try:
            pro = self.current().replace(".kicad_pcb", ".kicad_pro")
        except RuntimeError as e:
            return self.fail(e)
        os.startfile(pro) if hasattr(os, "startfile") else None

    def on_folder(self, _):
        try:
            d = os.path.dirname(self.current())
        except RuntimeError:
            d = self.root
        if d and hasattr(os, "startfile"):
            os.startfile(d)

    def on_next(self, _):
        try:
            pcb = self.current()
            if self.locked(pcb):
                raise RuntimeError("This board is open in KiCad: save it there first (and close it), then try again.")
            note = wx.GetTextFromUser("Note for the version you are finishing (optional):", "Next version", parent=self)
            new = self.busy("Saving this version and copying it...", boards.next_version, pcb, note.strip())
        except RuntimeError as e:
            return self.fail(e)
        self.refresh()
        wx.MessageBox("Tagged the old version in git and started\n%s" % new, "Next version")

    def on_daughter(self, _):
        try:
            pcb = self.current()
            name = wx.GetTextFromUser("Daughter board name (e.g. Display):", "New daughter board", parent=self).strip()
            if not name:
                return
            new, n = self.busy("Creating the daughter board...", boards.new_daughter, pcb, name)
        except RuntimeError as e:
            return self.fail(e)
        self.refresh()
        wx.MessageBox("Created %s with the main board's outline (%d items) on the 'Main board' layer." % (new, n),
                      "New daughter board")

    # ---- git ----
    def commit(self):
        msg = self.msg.GetValue().strip()
        if not msg:
            raise RuntimeError("Write what you changed first (it becomes the commit message).")
        project_git.scaffold(self.root, os.path.basename(self.root), source_too=False)
        project_git.ensure_git(self.root)
        h = project_git.commit(self.root, msg)
        self.msg.SetValue("")
        return h

    def on_commit(self, _):
        try:
            h = self.commit()
        except RuntimeError as e:
            return self.fail(e)
        self.refresh()
        wx.MessageBox("Committed %s." % h if h else "Nothing changed since the last commit.", "Commit")

    def on_commit_push(self, _):
        try:
            self.commit()
            out = self.busy("Pushing to GitHub...", project_git.push, self.root)
        except RuntimeError as e:
            self.refresh()
            return self.fail(e)
        self.refresh()
        wx.MessageBox("Committed and pushed.\n\n" + out[-800:], "Commit & Push")

    def on_push(self, _):
        try:
            out = self.busy("Pushing to GitHub...", project_git.push, self.root)
        except RuntimeError as e:
            return self.fail(e)
        self.refresh()
        wx.MessageBox(out[-800:], "Push")

    def on_pull(self, _):
        try:
            out = self.busy("Getting changes from GitHub...", project_git.pull, self.root)
        except RuntimeError as e:
            return self.fail(e)
        self.refresh()
        wx.MessageBox(out[-800:] or "Up to date.", "Pull")

    def on_github(self, _):
        if not self.root:
            return
        try:
            project_git.ensure_git(self.root)
        except RuntimeError as e:
            return self.fail(e)
        gh = project_git.gh_cli()
        choices = (["Create a new private repo on my GitHub (GitHub CLI)"] if gh else []) + ["Paste the address of a repo I made on github.com"]
        d = wx.SingleChoiceDialog(self, "Current: %s" % (project_git.remote(self.root) or "not connected"), "Connect to GitHub", choices)
        if d.ShowModal() != wx.ID_OK:
            return
        try:
            if d.GetStringSelection().startswith("Create"):
                name = wx.GetTextFromUser("Repo name:", "New GitHub repo", os.path.basename(self.root), parent=self).strip()
                if not name:
                    return
                url = self.busy("Creating the repo and pushing...", project_git.create_github_repo, self.root, name)
                wx.MessageBox("Connected and pushed:\n%s" % url, "GitHub")
            else:
                url = wx.GetTextFromUser("Repo address (HTTPS, from the green 'Code' button on GitHub):",
                                         "Connect to GitHub", parent=self).strip()
                if not url:
                    return
                project_git.set_remote(self.root, url)
                if wx.MessageBox("Connected. Push the project there now?", "GitHub", wx.YES_NO) == wx.YES:
                    self.busy("Pushing to GitHub...", project_git.push, self.root)
        except RuntimeError as e:
            self.fail(e)
        self.refresh()


def main():
    app = wx.App(False)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    f = Manager(args[0] if args and os.path.isdir(args[0]) else None)
    f.Show()
    if "--screenshot" in sys.argv:  # self-check: draw the window into a PNG and quit
        out = sys.argv[sys.argv.index("--screenshot") + 1]

        def shot():
            f.Raise()
            f.Update()
            x, y = f.GetScreenPosition()
            w, h = f.GetSize()
            bmp = wx.Bitmap(w, h)
            mdc = wx.MemoryDC(bmp)
            mdc.Blit(0, 0, w, h, wx.ScreenDC(), x, y)  # what is on screen: the native controls too
            mdc.SelectObject(wx.NullBitmap)
            bmp.SaveFile(out, wx.BITMAP_TYPE_PNG)
            f.Close()

        wx.CallLater(1500, shot)
    app.MainLoop()


if __name__ == "__main__":
    main()
