"""Minimal KiCad S-expression reader/writer. Atoms stay strings; quoted atoms are wrapped in Q."""
import re


class Q(str):
    """A quoted string atom."""


_TOK = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text):
    stack, cur = [], []
    for m in _TOK.finditer(text):
        op, cl, qs, atom = m.groups()
        if op:
            stack.append(cur)
            cur = []
        elif cl:
            done, cur = cur, stack.pop()
            cur.append(done)
        elif qs is not None:
            cur.append(Q(re.sub(r'\\(.)', r'\1', qs)))
        elif atom is not None:
            cur.append(atom)
    return cur[0]


def dump(node, indent=0):
    if not isinstance(node, list):
        if isinstance(node, Q):
            return '"' + node.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
        return node
    simple = all(not isinstance(x, list) for x in node)
    if simple:
        return "(" + " ".join(dump(x) for x in node) + ")"
    pad = "\t" * (indent + 1)
    k = next(i for i, x in enumerate(node) if isinstance(x, list))
    out = "(" + " ".join(dump(x) for x in node[:k])  # leading atoms inline, rest one per line, order kept
    for x in node[k:]:
        out += "\n" + pad + dump(x, indent + 1)
    return out + "\n" + "\t" * indent + ")"


def find(node, key):
    """Direct children lists whose head is `key`."""
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


def first(node, key):
    r = find(node, key)
    return r[0] if r else None


def walk(node, key):
    """All descendant lists whose head is `key`."""
    if isinstance(node, list):
        if node and node[0] == key:
            yield node
        for x in node:
            yield from walk(x, key)


def prop(sym, name):
    for p in find(sym, "property"):
        if p[1] == name:
            return p[2]
    return None


if __name__ == "__main__":
    t = '(a (b "x y" 1.5) (c (d "q\\"z")) e)'
    n = parse(t)
    assert n[0] == "a" and n[1][1] == "x y" and isinstance(n[1][1], Q) and n[2][1][1] == 'q"z'
    assert parse(dump(n)) == n
    print("sexp ok")
