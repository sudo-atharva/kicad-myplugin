"""Boards self-check: new project -> next version -> daughter board with the main outline -> refresh.
Run with KiCad's Python (needs git):  python tests/test_boards.py"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins"))
import pcbnew, kpaths, project_git, boards

mm = pcbnew.FromMM
parent = tempfile.mkdtemp()
pro = project_git.new_project(parent, "Main")  # old flat layout: 01_Source/Main.kicad_pcb
root = os.path.dirname(os.path.dirname(pro))
main_file = os.path.join(root, "01_Source", "Main.kicad_pcb")

# main board: 50 x 40 outline, a 4-pin connector J1, a mounting hole H1
b = pcbnew.LoadBoard(main_file)
for (x0, y0, x1, y1) in [(0, 0, 50, 0), (50, 0, 50, 40), (50, 40, 0, 40), (0, 40, 0, 0)]:
    s = pcbnew.PCB_SHAPE(b)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(pcbnew.VECTOR2I(mm(x0), mm(y0)))
    s.SetEnd(pcbnew.VECTOR2I(mm(x1), mm(y1)))
    s.SetLayer(pcbnew.Edge_Cuts)
    b.Add(s)
fpdir = kpaths.footprint_dir()
j1 = pcbnew.FootprintLoad(os.path.join(fpdir, "Connector_PinHeader_2.54mm.pretty"), "PinHeader_1x04_P2.54mm_Vertical")
j1.SetReference("J1"); b.Add(j1); j1.SetPosition(pcbnew.VECTOR2I(mm(10), mm(10)))
h1 = pcbnew.FootprintLoad(os.path.join(fpdir, "MountingHole.pretty"), "MountingHole_3.2mm_M3")
h1.SetReference("H1"); b.Add(h1); h1.SetPosition(pcbnew.VECTOR2I(mm(45), mm(35)))
r1 = pcbnew.FootprintLoad(os.path.join(fpdir, "Resistor_SMD.pretty"), "R_0603_1608Metric")
r1.SetReference("R1"); b.Add(r1)
pcbnew.SaveBoard(main_file, b)

# next version: v1 stays (tagged), v2 is a copy in 01_Source/Main_v2/
v2 = boards.next_version(main_file, "first spin")
assert v2.endswith(os.path.join("Main_v2", "Main_v2.kicad_pcb")) and os.path.isfile(v2), v2
assert os.path.isfile(v2.replace(".kicad_pcb", ".kicad_pro")) and os.path.isfile(v2.replace(".kicad_pcb", ".kicad_sch"))
assert json.load(open(v2.replace(".kicad_pcb", ".kicad_pro")))["meta"]["filename"] == "Main_v2.kicad_pro"
tags = project_git.git(root, "tag", "--list")
assert "Main-v1" in tags, tags

# daughter board on v2: same coordinates, outline + J1 + H1 on the "Main board" layer, R1 not
d, n = boards.new_daughter(v2, "Display")
db = pcbnew.LoadBoard(d)
assert db.GetLayerName(pcbnew.User_1) == boards.OUTLINE_LAYER_NAME, db.GetLayerName(pcbnew.User_1)
on = [it for it in db.GetDrawings() if it.GetLayer() == pcbnew.User_1]
edges = [it for it in on if isinstance(it, pcbnew.PCB_SHAPE) and it.GetShape() == pcbnew.SHAPE_T_SEGMENT]
texts = sorted(it.GetText() for it in on if isinstance(it, pcbnew.PCB_TEXT))
assert len(edges) == 4 and texts == ["H1 (main board)", "J1 (main board)"], (len(edges), texts)
assert not [it for it in db.GetDrawings() if it.GetLayer() == pcbnew.Edge_Cuts], "own Edge.Cuts stays empty"
bb = pcbnew.BOX2I()
for e in edges:
    bb.Merge(e.GetBoundingBox())
assert abs(pcbnew.ToMM(bb.GetWidth()) - 50) < 0.2, pcbnew.ToMM(bb.GetWidth())

# main board changes (J1 moves) -> refresh redraws, no duplicates
mb = pcbnew.LoadBoard(v2)
[f for f in mb.GetFootprints() if f.GetReference() == "J1"][0].SetPosition(pcbnew.VECTOR2I(mm(20), mm(10)))
pcbnew.SaveBoard(v2, mb)
main_name, n2 = boards.refresh_outline(d)
db = pcbnew.LoadBoard(d)
on = [it for it in db.GetDrawings() if it.GetLayer() == pcbnew.User_1]
assert main_name == "Main v2" and len(on) == n == n2, (main_name, len(on), n, n2)
j1t = [it for it in on if isinstance(it, pcbnew.PCB_TEXT) and it.GetText().startswith("J1")][0]
assert abs(pcbnew.ToMM(j1t.GetPosition().x) - 20) < 0.01

# daughter's next version keeps its main board
d2 = boards.next_version(d)
t = boards.tree(root)
assert "Main: v1, v2" in t and "Display: v1 (on Main v2), v2 (on Main v2)" in t, t
assert "Display-v1" in project_git.git(root, "tag", "--list")
# outputs go to a per-board folder
assert kpaths.project_folder(d2, "03_Manufacturing", "bom").endswith(os.path.join("03_Manufacturing", "Display_v2"))
assert kpaths.project_folder(main_file, "03_Manufacturing", "bom").endswith("03_Manufacturing")
print(t)
print(project_git.history(root))
print("boards ok")
