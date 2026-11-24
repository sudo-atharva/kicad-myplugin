"""Project Manager self-check (no window): board list, commit, push/pull with a local repo standing in for GitHub.
Run with KiCad's Python (needs git):  python tests/test_project_manager.py"""
import os, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins"))
import project_git as pg, boards, project_manager as pm

tmp = tempfile.mkdtemp()
pro = pg.new_project(tmp, "Robot")
root = os.path.dirname(os.path.dirname(pro))
main = os.path.join(root, "01_Source", "Robot.kicad_pcb")
assert [x[0] for x in pm.board_files(root)] == ["Robot v1   (not in project.json yet)"]

v2 = boards.next_version(main)
boards.new_daughter(v2, "Sensor")
labels = [("    " * d + l) for l, _, d in pm.board_files(root)]
assert labels == ["Robot v1", "Robot v2", "    Sensor v1   (on Robot v2)"], labels

# "GitHub": a bare repo on disk
hub = os.path.join(tmp, "hub.git")
subprocess.run(["git", "init", "--bare", "-q", hub], check=True)
assert pg.remote(root) == "" and pg.ahead_behind(root) == (None, None)
try:
    pg.set_remote(root, "not a url")
    raise AssertionError("bad URL accepted")
except RuntimeError:
    pass
pg.set_remote(root, hub)
pg.push(root)
tags = subprocess.run(["git", "ls-remote", "--tags", hub], capture_output=True, text=True).stdout
assert "Robot-v1" in tags, tags
assert pg.ahead_behind(root) == (0, 0)

# local change -> 1 to push
open(os.path.join(root, "05_Testing", "notes.md"), "w").write("bring-up ok\n")
assert "notes.md" in pg.changes(root)
pg.commit(root, "Bring-up notes")
assert pg.ahead_behind(root) == (1, 0)
pg.push(root)

# another PC pushes -> pull brings it here
other = os.path.join(tmp, "other")
subprocess.run(["git", "clone", "-q", "-b", "main", hub, other], check=True)
open(os.path.join(other, "Robot", "README.md") if os.path.isdir(os.path.join(other, "Robot")) else os.path.join(other, "README.md"), "a").write("\nfrom the other PC\n")
subprocess.run(["git", "-C", other, "commit", "-qam", "From the other PC"], check=True)
subprocess.run(["git", "-C", other, "push", "-q"], check=True)
assert pg.ahead_behind(root) == (0, 1)
pg.pull(root)
assert "from the other PC" in open(os.path.join(root, "README.md")).read()
assert "From the other PC" in pg.history(root)
print("project_manager ok")
