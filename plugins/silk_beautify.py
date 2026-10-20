"""Silkscreen beautifier for KiCad 10.

- Reference designators: uniform size, moved to the cleanest spot around each part
  (no pads, no other parts' bodies, no other text, inside the board), kept upright.
- Connectors: every pin gets its net name on silk, outside the connector body.
  Labels belong to the footprint (they move with it); rerunning updates them.
- Values: moved from silkscreen to Fab.

Toolbar button in PCB editor (Tools > External Plugins > Silk Beautify), or CLI:
  <KiCad python> silk_beautify.py board.kicad_pcb [-o out.kicad_pcb]
"""
import re
import pcbnew

REF_H = 0.8          # mm, reference text height (JLC/PCBWay min ~0.8)
REF_T = 0.15         # mm, stroke
GAP = 0.25           # mm, clearance between text and what it labels
EDGE = 0.3           # mm, keep text this far inside the board
CONN_PREFIX = {"J", "X", "P", "CN", "CON", "SV", "SL", "TB", "XT"}
CONN_LIB = re.compile(r"connector|terminal|header|socket|phoenix|mcv|molex|jst", re.I)
AUTO_NET = re.compile(r"^(N\$\d+|Net-\(|unconnected-)", re.I)
W_PAD, W_TEXT, W_BODY, W_SILK, W_VIA, W_EDGE = 10, 10, 4, 2, 1, 100
CELL = 5.0           # mm, spatial hash cell


def mm(v):
    return pcbnew.ToMM(v)


def box(b):
    return (mm(b.GetLeft()), mm(b.GetTop()), mm(b.GetRight()), mm(b.GetBottom()))


def overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


class Obstacles:
    """Rectangles per silk side in a spatial hash."""

    def __init__(self):
        self.grid = {}

    def add(self, r, w, owner=None):
        item = (r, w, owner)  # one object in every cell, so cost() can dedupe by id
        for cx in range(int(r[0] // CELL), int(r[2] // CELL) + 1):
            for cy in range(int(r[1] // CELL), int(r[3] // CELL) + 1):
                self.grid.setdefault((cx, cy), []).append(item)

    def cost(self, r, skip=None):
        seen, c = set(), 0.0
        for cx in range(int(r[0] // CELL), int(r[2] // CELL) + 1):
            for cy in range(int(r[1] // CELL), int(r[3] // CELL) + 1):
                for item in self.grid.get((cx, cy), ()):
                    if id(item) in seen or (skip is not None and item[2] is skip):
                        continue
                    seen.add(id(item))
                    c += overlap(r, item[0]) * item[1]
        return c


def short_net(name):
    name = name.split("/")[-1]
    return re.sub(r"~\{([^}]*)\}", r"\1", name)  # KiCad overbar markup


def is_connector(fp):
    pre = re.match(r"[A-Za-z]*", fp.GetReference()).group(0).upper()
    return pre in CONN_PREFIX or bool(CONN_LIB.search(str(fp.GetFPID().GetLibItemName())))


def body_box(fp):
    return box(fp.GetBoundingBox(False, False))


def build_obstacles(board):
    obs = {False: Obstacles(), True: Obstacles()}  # key: is bottom side
    silk = {pcbnew.F_SilkS: False, pcbnew.B_SilkS: True}
    for fp in board.GetFootprints():
        bottom = fp.IsFlipped()
        obs[bottom].add(body_box(fp), W_BODY, fp)
        for p in fp.Pads():
            r = box(p.GetBoundingBox())
            if p.IsOnLayer(pcbnew.F_Cu):
                obs[False].add(r, W_PAD)
            if p.IsOnLayer(pcbnew.B_Cu):
                obs[True].add(r, W_PAD)
        for g in fp.GraphicalItems():
            if g.GetLayer() in silk and not isinstance(g, pcbnew.PCB_TEXT):
                obs[silk[g.GetLayer()]].add(box(g.GetBoundingBox()), W_SILK)
    for d in board.GetDrawings():
        if d.GetLayer() in silk:
            obs[silk[d.GetLayer()]].add(box(d.GetBoundingBox()), W_TEXT if isinstance(d, pcbnew.PCB_TEXT) else W_SILK)
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            r = box(t.GetBoundingBox())
            obs[False].add(r, W_VIA)
            obs[True].add(r, W_VIA)
    return obs


def edge_cost(r, edge):
    # ponytail: board bbox, not the true outline; fine for rectangular boards
    inner = (edge[0] + EDGE, edge[1] + EDGE, edge[2] - EDGE, edge[3] - EDGE)
    area = (r[2] - r[0]) * (r[3] - r[1])
    return (area - overlap(r, inner)) * W_EDGE


def place_at(t, cx, cy):
    """Move text so its bounding box centre lands on (cx, cy); return the box."""
    t.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(cx), pcbnew.FromMM(cy)))
    b = box(t.GetBoundingBox())
    dx, dy = cx - (b[0] + b[2]) / 2, cy - (b[1] + b[3]) / 2
    t.Move(pcbnew.VECTOR2I(pcbnew.FromMM(dx), pcbnew.FromMM(dy)))
    return box(t.GetBoundingBox())


def place_reference(fp, obs, edge):
    t = fp.Reference()
    if not t.IsVisible() or not t.GetText():
        return 0.0
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(REF_H), pcbnew.FromMM(REF_H)))
    t.SetTextThickness(pcbnew.FromMM(REF_T))
    t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER)
    t.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
    t.SetKeepUpright(True)
    o = obs[fp.IsFlipped()]
    x0, y0, x1, y1 = body_box(fp)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    best = None
    for ang, ang_pen in ((0, 0.0), (90, 0.05)):
        t.SetTextAngleDegrees(ang)
        b = place_at(t, cx, cy)
        w, h = b[2] - b[0], b[3] - b[1]
        for ring in (0, 1, 2):
            g = GAP + ring * REF_H
            spots = [(cx, y0 - g - h / 2), (cx, y1 + g + h / 2), (x0 - g - w / 2, cy), (x1 + g + w / 2, cy),
                     (x0 - g - w / 2, y0 - g - h / 2), (x1 + g + w / 2, y0 - g - h / 2),
                     (x0 - g - w / 2, y1 + g + h / 2), (x1 + g + w / 2, y1 + g + h / 2)]
            for i, (sx, sy) in enumerate(spots):
                r = (sx - w / 2, sy - h / 2, sx + w / 2, sy + h / 2)
                c = o.cost(r, skip=fp) + edge_cost(r, edge) + ang_pen + 0.01 * i + 0.1 * ring
                if best is None or c < best[0]:
                    best = (c, ang, sx, sy)
    c, ang, sx, sy = best
    t.SetTextAngleDegrees(ang)
    o.add(place_at(t, sx, sy), W_TEXT)
    return c


def label_connector(fp, obs, edge):
    """Net name next to every pin, outside the body, on the freer side of each pin row."""
    nets = {p.GetNumber(): short_net(p.GetNetname()) for p in fp.Pads()}
    # drop labels from a previous run (footprint texts on silk equal to one of its net names)
    for g in list(fp.GraphicalItems()):
        if isinstance(g, pcbnew.PCB_TEXT) and g.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS) \
                and g.GetText() in nets.values():
            fp.Delete(g)  # Remove() leaves dangling SWIG proxies and corrupts fp.Pads()
    pads = [p for p in fp.Pads() if nets[p.GetNumber()] and not AUTO_NET.match(nets[p.GetNumber()])]
    if not pads:
        return 0, 0.0
    bottom = fp.IsFlipped()
    o = obs[bottom]
    pos = [(mm(p.GetPosition().x), mm(p.GetPosition().y)) for p in fp.Pads()]
    horizontal = (max(x for x, _ in pos) - min(x for x, _ in pos)) >= (max(y for _, y in pos) - min(y for _, y in pos))
    along = sorted({round(x if horizontal else y, 2) for x, y in pos})
    pitch = min((b - a for a, b in zip(along, along[1:])), default=2.54)
    min_h = mm(fp.GetBoard().GetDesignSettings().m_MinSilkTextHeight)  # board rule, usually 0.8
    max_size = max(min_h, min(1.0, pitch * 0.7))
    sizes = [s for s in sorted({max_size, 0.9, 0.8, 0.7, 0.6}, reverse=True) if min_h <= s <= max_size]
    bx0, by0, bx1, by1 = body_box(fp)
    mid = (by0 + by1) / 2 if horizontal else (bx0 + bx1) / 2

    def make(p, side, size):
        t = pcbnew.PCB_TEXT(fp)
        t.SetText(nets[p.GetNumber()])
        t.SetLayer(pcbnew.B_SilkS if bottom else pcbnew.F_SilkS)
        t.SetMirrored(bottom)
        t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(size), pcbnew.FromMM(size)))
        t.SetTextThickness(pcbnew.FromMM(size * 0.15))
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER)
        t.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
        t.SetKeepUpright(True)
        t.SetTextAngleDegrees(90 if horizontal else 0)  # text runs away from the pin row
        px, py = mm(p.GetPosition().x), mm(p.GetPosition().y)
        b = place_at(t, px, py)
        w, h = b[2] - b[0], b[3] - b[1]
        if horizontal:
            cy = by0 - GAP - h / 2 if side < 0 else by1 + GAP + h / 2
            r = place_at(t, px, cy)
        else:
            cx = bx0 - GAP - w / 2 if side < 0 else bx1 + GAP + w / 2
            r = place_at(t, cx, py)
        return t, r

    def coord(p):
        return mm(p.GetPosition().y) if horizontal else mm(p.GetPosition().x)

    # two-row parts: each row labels outward; one row: try both sides, keep the cheaper
    rows = [[p for p in pads if coord(p) < mid - 0.5], [p for p in pads if coord(p) > mid + 0.5]]
    if rows[0] and rows[1]:
        plan = [(rows[0], -1), (rows[1], 1)]
    else:
        plan = [(pads, None)]
    placed, cost = 0, 0.0
    for row, side in plan:
        options = [side] if side else [-1, 1]
        best = None
        for size in sizes:  # biggest text that fits; shrink only when cramped
            for s in options:
                made = [make(p, s, size) for p in row]
                c = sum(o.cost(r, skip=fp) + edge_cost(r, edge) for _, r in made)
                score = c + 0.2 * (max_size - size)
                if best is None or score < best[0]:
                    best = (score, made)
            if best[0] < 0.1:
                break
        cost += best[0]
        for t, r in best[1]:
            fp.Add(t)
            o.add(r, W_TEXT)
            placed += 1
    return placed, cost


def run(board, labels=True, values_to_fab=True):
    edge = box(board.GetBoardEdgesBoundingBox())
    fps = list(board.GetFootprints())
    if values_to_fab:
        for fp in fps:
            v = fp.Value()
            if v.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                v.SetLayer(pcbnew.B_Fab if fp.IsFlipped() else pcbnew.F_Fab)
    obs = build_obstacles(board)
    n_labels, bad = 0, []
    for fp in fps:
        if labels and is_connector(fp):
            n, c = label_connector(fp, obs, edge)
            n_labels += n
            if c > 0.5 * max(n, 1):
                bad.append(fp.GetReference() + " pin labels")
    # biggest parts first: they have the fewest good spots
    fps.sort(key=lambda f: -(lambda b: (b[2] - b[0]) * (b[3] - b[1]))(body_box(f)))
    bad += [fp.GetReference() for fp in fps if place_reference(fp, obs, edge) > 0.5]
    return n_labels, len(fps), bad


class SilkBeautify(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Silk Beautify"
        self.category = "Silkscreen"
        self.description = "Place references cleanly, label connector pins with net names, move values to Fab"
        self.show_toolbar_button = True

    def Run(self):
        board = pcbnew.GetBoard()
        n_labels, n, bad = run(board)
        pcbnew.Refresh()
        import wx
        msg = "Placed %d references, %d connector pin labels." % (n, n_labels)
        if bad:
            msg += "\n\nNo clean spot for (check by hand):\n" + ", ".join(bad)
        wx.MessageBox(msg, "Silk Beautify")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("-o", "--out")
    ap.add_argument("--no-labels", action="store_true")
    ap.add_argument("--keep-values", action="store_true")
    a = ap.parse_args()
    b = pcbnew.LoadBoard(a.board)
    n_labels, n, bad = run(b, labels=not a.no_labels, values_to_fab=not a.keep_values)
    b.Save(a.out or a.board)
    print("references: %d, pin labels: %d, no clean spot: %d %s" % (n, n_labels, len(bad), " ".join(bad)))
