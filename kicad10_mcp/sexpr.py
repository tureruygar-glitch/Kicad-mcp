"""Minimal KiCad S-expression reader/writer plus symbol-library helpers.

Used to read kicad-cli netlists and KiCad symbol libraries without extra
dependencies.
"""
import re
from pathlib import Path

LIBDIR = Path(r"C:\Program Files\KiCad\10.0\share\kicad\symbols")


class Q(str):
    """A quoted string atom."""


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text: str):
    stack, cur = [], []
    pos = 0
    while True:
        m = _TOKEN.match(text, pos)
        if not m:
            break
        pos = m.end()
        if m.group(1):
            stack.append(cur)
            cur = []
        elif m.group(2):
            done = cur
            cur = stack.pop()
            cur.append(done)
        elif m.group(3) is not None:
            cur.append(Q(m.group(3).replace('\\"', '"').replace("\\\\", "\\")))
        else:
            cur.append(m.group(4))
    return cur[0]


def dump(node, indent=0) -> str:
    if isinstance(node, list):
        pad = "\t" * indent
        simple = all(not isinstance(x, list) for x in node)
        if simple:
            return "(" + " ".join(dump(x) for x in node) + ")"
        parts = [dump(x, indent + 1) if isinstance(x, list) else dump(x) for x in node]
        head = [p for p, x in zip(parts, node) if not isinstance(x, list)]
        kids = [p for p, x in zip(parts, node) if isinstance(x, list)]
        return "(" + " ".join(head) + "".join("\n" + pad + "\t" + k for k in kids) + "\n" + pad + ")"
    if isinstance(node, Q):
        return '"' + node.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return str(node)


def children(node, tag):
    return [x for x in node if isinstance(x, list) and x and x[0] == tag]


def child(node, tag):
    c = children(node, tag)
    return c[0] if c else None


_LIB_CACHE = {}


def load_lib(lib: str):
    if lib not in _LIB_CACHE:
        _LIB_CACHE[lib] = parse((LIBDIR / f"{lib}.kicad_sym").read_text(encoding="utf-8"))
    return _LIB_CACHE[lib]


def get_symbol(lib: str, name: str):
    """Return a flattened copy of a library symbol (``extends`` resolved)."""
    import copy

    tree = load_lib(lib)
    sym = next(s for s in children(tree, "symbol") if s[1] == name)
    sym = copy.deepcopy(sym)
    ext = child(sym, "extends")
    if ext is None:
        return sym
    base = copy.deepcopy(get_symbol(lib, ext[1]))
    base_name = base[1]
    base[1] = Q(name)
    # Rename unit sub-symbols to the derived name.
    for sub in children(base, "symbol"):
        sub[1] = Q(str(sub[1]).replace(base_name, name, 1))
    # Derived properties override the base's.
    own = {p[1]: p for p in children(sym, "property")}
    for i, x in enumerate(base):
        if isinstance(x, list) and x and x[0] == "property" and x[1] in own:
            base[i] = own.pop(x[1])
    insert_at = max(i for i, x in enumerate(base) if isinstance(x, list) and x[0] == "property") + 1
    for p in own.values():
        base.insert(insert_at, p)
        insert_at += 1
    return base


def symbol_pins(sym):
    """[(number, name, x, y, angle, hidden)] in symbol coordinates (Y up), unit 1 / style 1."""
    pins = []
    for sub in children(sym, "symbol"):
        suffix = str(sub[1]).rsplit("_", 2)[-2:]
        unit, style = int(suffix[0]), int(suffix[1])
        if unit not in (0, 1) or style not in (0, 1):
            continue
        for p in children(sub, "pin"):
            at = child(p, "at")
            name = child(p, "name")[1]
            number = child(p, "number")[1]
            hidden = "hide" in p or any(isinstance(x, list) and x[:2] == ["hide", "yes"] for x in p)
            pins.append((str(number), str(name), float(at[1]), float(at[2]),
                         int(float(at[3])) if len(at) > 3 else 0, hidden))
    return pins
