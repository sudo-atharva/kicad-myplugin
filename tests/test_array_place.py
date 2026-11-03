"""Array Place self-check: 6 LED+resistor channels with shuffled references, wired to a connector in reverse.
Run with KiCad's Python:  python tests/test_array_place.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins"))
import pcbnew, kpaths, array_place as ap

FP = kpaths.footprint_dir()


def part(board, lib, name, ref, x, y, pads):
    fp = pcbnew.FootprintLoad(os.path.join(FP, lib + ".pretty"), name)
    fp.SetReference(ref)
    board.Add(fp)
    fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
    for p in fp.Pads():
        if p.GetNumber() in pads:
            p.SetNet(net(board, pads[p.GetNumber()]))
    return fp


def net(board, name):
    n = board.FindNet(name)
    if n is None:
        n = pcbnew.NETINFO_ITEM(board, name)
        board.Add(n)
    return n


b = pcbnew.BOARD()
N = 6
# resistor numbers deliberately don't follow the LED numbers: matching must come from the wiring
r_of = {1: 1, 2: 5, 3: 2, 4: 6, 5: 3, 6: 4}
for k in range(1, N + 1):
    part(b, "LED_SMD", "LED_0805_2012Metric", "D%d" % k, 10 * k, 40 + 3 * k, {"1": "GND", "2": "A%d" % k})
    part(b, "Resistor_SMD", "R_0805_2012Metric", "R%d" % r_of[k], 50 - 5 * k, 70, {"1": "A%d" % k, "2": "IN%d" % k})
# connector pin p carries IN(N+1-p): pin order is the reverse of LED order
part(b, "Connector_PinHeader_2.54mm", "PinHeader_1x06_P2.54mm_Vertical", "J1", 0, 0,
     {str(p): "IN%d" % (N + 1 - p) for p in range(1, N + 1)})
d1, r1 = (f for ref in ("D1", "R1") for f in b.GetFootprints() if f.GetReference() == ref)
r1.SetPosition(d1.GetPosition() + pcbnew.VECTOR2I(0, pcbnew.FromMM(2)))  # the hand-placed template

chans = ap.find_channels(b, [d1, r1])
pairs = [(c[0].GetReference(), c[1].GetReference()) for c in chans]
assert pairs == [("D%d" % k, "R%d" % r_of[k]) for k in range(1, N + 1)], pairs

lay = ap.Layout(b, chans)
lay.apply(4.0, 0, N, "connector")
xs = {c[0].GetReference(): pcbnew.ToMM(c[0].GetPosition().x) for c in chans}
assert sorted(xs, key=xs.get) == ["D%d" % k for k in range(N, 0, -1)], xs  # follows connector pins
for c in chans:  # every resistor sits 2 mm under its LED, like the template
    d = c[1].GetPosition() - c[0].GetPosition()
    assert (d.x, d.y) == (0, pcbnew.FromMM(2)), (c[1].GetReference(), d.x, d.y)
lay.apply(4.0, 5.0, 3, "reference")
assert pcbnew.ToMM(chans[3][0].GetPosition().y - chans[0][0].GetPosition().y) == 5.0  # wraps to a 2nd row
lay.restore()
assert pcbnew.ToMM(chans[2][0].GetPosition().x) == 30.0  # Cancel puts everything back
print("array_place ok:", pairs)
