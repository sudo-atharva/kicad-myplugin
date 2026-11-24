"""Pad Stretch self-check. Run with KiCad's Python:  python tests/test_pad_stretch.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins"))
import pcbnew, kpaths, pad_stretch as ps

mm, to = pcbnew.FromMM, pcbnew.ToMM
L = pcbnew.PADSTACK.ALL_LAYERS
d = tempfile.mkdtemp()
bf = os.path.join(d, "Demo_v2.kicad_pcb")
b = pcbnew.BOARD()


def add(lib, name, ref, x, y, rot=0, bottom=False):
    fp = pcbnew.FootprintLoad(os.path.join(kpaths.footprint_dir(), lib + ".pretty"), name)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    fp.SetReference(ref)
    b.Add(fp)
    fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
    fp.SetOrientationDegrees(rot)
    if bottom:
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
    return fp


r1 = add("Resistor_SMD", "R_0603_1608Metric", "R1", 10, 10)
r2 = add("Resistor_SMD", "R_0603_1608Metric", "R2", 20, 10, rot=90)
r3 = add("Resistor_SMD", "R_0603_1608Metric", "R3", 30, 10, bottom=True)
c1 = add("Capacitor_SMD", "C_0603_1608Metric", "C1", 40, 10)
u1 = add("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm", "U1", 10, 30)
u2 = add("Package_DFN_QFN", "QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm", "U2", 30, 30)
pcbnew.SaveBoard(bf, b)


def pads(fp):
    return {p.GetNumber(): (round(to(p.GetSize(L).x), 3), round(to(p.GetSize(L).y), 3),
                            round(to(p.GetFPRelativePosition().x), 3), round(to(p.GetFPRelativePosition().y), 3))
            for p in fp.Pads()}


before = {f.GetReference(): pads(f) for f in b.GetFootprints()}
# all R by prefix: 0603 pads are 0.8 x 0.95 mm at x = +-0.825 -> 1.3 wide at +-1.075
parts = ps.pick(b, "prefix", ["r"])
assert sorted(f.GetReference() for f in parts) == ["R1", "R2", "R3"]
st = ps.Stretch(parts)
assert st.apply(0.5) == 6
for r in (r1, r2, r3):
    p = pads(r)
    assert p["1"][0] == round(before["R1"]["1"][0] + 0.5, 3) and p["1"][1] == before["R1"]["1"][1], (r.GetReference(), p)
    assert p["1"][2] == round(before["R1"]["1"][2] - 0.25, 3) and p["2"][2] == round(before["R1"]["2"][2] + 0.25, 3), p
assert pads(c1) == before["C1"], "C1 not picked"
# the gap between the pads stays the same (stretched outward only)
p = pads(r1)
assert round((p["2"][2] - p["2"][0] / 2) - (p["1"][2] + p["1"][0] / 2), 3) == round(
    (before["R1"]["2"][2] - before["R1"]["2"][0] / 2) - (before["R1"]["1"][2] + before["R1"]["1"][0] / 2), 3)
st.restore()
assert pads(r1) == before["R1"] and pads(r2) == before["R2"]

# QFN: side pads grow outward, thermal pad (centre) untouched; SOIC pads too
st = ps.Stretch([u1, u2])
st.apply(0.3)
q = pads(u2)
ep = [n for n in q if q[n][2] == 0 and q[n][3] == 0][0]
assert q[ep] == before["U2"][ep], "centre pad unchanged"
assert all(q[n] != before["U2"][n] for n in q if n != ep), "every side pad stretched"
st.restore()

# keep for the project: library, fp-lib-table, parts relinked, schematic updated
sch = os.path.join(d, "Demo_v2.kicad_sch")
open(sch, "w").write('''(kicad_sch (version 20260306) (generator "eeschema")
  (lib_symbols)
  (symbol (lib_id "Device:R") (at 50 50 0) (unit 1)
    (property "Reference" "R1" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0)))
  (symbol (lib_id "Device:C") (at 60 50 0) (unit 1)
    (property "Reference" "C1" (at 0 0 0))
    (property "Footprint" "Capacitor_SMD:C_0603_1608Metric" (at 0 0 0)))
)
''')
n, mapping, changed, busy = ps.run(b, bf, parts, 0.5)
assert n == 6 and mapping == {"Resistor_SMD:R_0603_1608Metric": "Demo-pads:R_0603_1608Metric_ext0.50"}, mapping
lib = os.path.join(d, "libs", "Demo-pads.pretty", "R_0603_1608Metric_ext0.50.kicad_mod")
assert os.path.isfile(lib), os.listdir(d)
saved = pcbnew.FootprintLoad(os.path.dirname(lib), "R_0603_1608Metric_ext0.50")
sp = pads(saved)
assert sp["1"][0] == round(before["R1"]["1"][0] + 0.5, 3) and sp["1"][2] == round(before["R1"]["1"][2] - 0.25, 3), sp
assert '(name "Demo-pads")' in open(os.path.join(d, "fp-lib-table")).read()
assert all(f.GetFPIDAsString() == "Demo-pads:R_0603_1608Metric_ext0.50" for f in parts)
text = open(sch).read()
assert changed == 1 and '"Demo-pads:R_0603_1608Metric_ext0.50"' in text and '"Capacitor_SMD:C_0603_1608Metric"' in text, text
# an open schematic (lock file) is left alone
open(os.path.join(d, "~Demo_v2.kicad_sch.lck"), "w").write("x")
assert ps.update_schematics(bf, ["C1"], {"C1": "X:Y"}) == (0, ["Demo_v2.kicad_sch"])
print("pad_stretch ok")
