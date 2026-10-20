"""Regression check for eagle3d model placement (run with KiCad's python).
A = library footprint rotated at board level: KiCad's own, correct 3D.
B = same copper with rotation + offset baked into local geometry (what an Eagle import looks like).
After eagle3d, B's model must sit exactly where A's does (convention verified by render)."""
import math, os, subprocess, sys, tempfile
import pcbnew

L = os.path.join(os.path.dirname(os.path.dirname(sys.executable)), "share", "kicad", "footprints")
PARTS = [("Connector_JST", "JST_XH_B6B-XH-A_1x06_P2.50mm_Vertical"),
         ("Package_TO_SOT_THT", "TO-220-3_Horizontal_TabDown"),
         ("Package_TO_SOT_SMD", "SOT-223-3_TabPin2"),
         ("Connector_PinHeader_2.54mm", "PinHeader_1x06_P2.54mm_Vertical")]
mm = pcbnew.FromMM
d = tempfile.mkdtemp()
src, out = os.path.join(d, "t.kicad_pcb"), os.path.join(d, "o.kicad_pcb")
b = pcbnew.BOARD()
for i, (lib, name) in enumerate(PARTS):
    for j, ang in enumerate((0, 90, 180, 270)):
        x, y = mm(15 + j * 45), mm(20 + i * 30)
        a = pcbnew.FootprintLoad(os.path.join(L, lib + ".pretty"), name)
        a.SetReference("A%d_%d" % (i, ang)); b.Add(a)
        a.SetPosition(pcbnew.VECTOR2I(x, y)); a.SetOrientationDegrees(ang)
        c = pcbnew.FootprintLoad(os.path.join(L, lib + ".pretty"), name)
        c.SetReference("B%d_%d" % (i, ang)); c.Models().clear(); b.Add(c)
        c.SetPosition(pcbnew.VECTOR2I(x + mm(20), y))
        for it in list(c.Pads()) + list(c.GraphicalItems()):
            it.Rotate(c.GetPosition(), pcbnew.EDA_ANGLE(ang, pcbnew.DEGREES_T))
            it.Move(pcbnew.VECTOR2I(mm(3), mm(5)))
b.Save(src)
subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "eagle3d.py"), src, "-o", out],
               check=True, stdout=subprocess.DEVNULL)
b = pcbnew.LoadBoard(out)


def pose(fp):
    """Model origin relative to pad 1 (board mm, y-down) and CCW angle. KiCad: offset y-up, rot z clockwise."""
    m = fp.Models()[0]
    r = math.radians(fp.GetOrientationDegrees())
    ox, oy = m.m_Offset.x, -m.m_Offset.y
    wx = ox * math.cos(r) + oy * math.sin(r); wy = -ox * math.sin(r) + oy * math.cos(r)
    p1 = [p for p in fp.Pads() if p.GetNumber() == "1"][0].GetPosition()
    return (pcbnew.ToMM(fp.GetPosition().x - p1.x) + wx, pcbnew.ToMM(fp.GetPosition().y - p1.y) + wy,
            (fp.GetOrientationDegrees() - m.m_Rotation.z) % 360, os.path.basename(m.m_Filename))


fails = 0
for i, (_, name) in enumerate(PARTS):
    for ang in (0, 90, 180, 270):
        A, B = (b.FindFootprintByReference("%s%d_%d" % (k, i, ang)) for k in "AB")
        pa, pb = pose(A), pose(B)
        ok = abs(pa[0] - pb[0]) < 0.01 and abs(pa[1] - pb[1]) < 0.01 and abs((pa[2] - pb[2] + 180) % 360 - 180) < 0.01 and pa[3] == pb[3]
        fails += not ok
        print("%-4s %-40s %3d  A=%s  B=%s" % ("ok" if ok else "FAIL", name, ang,
              tuple(round(v, 2) if isinstance(v, float) else v for v in pa[:3]), tuple(round(v, 2) if isinstance(v, float) else v for v in pb[:3]) + (pb[3],)))
sys.exit(1 if fails else 0)
