"""Spacing rhythm: values off the project's scale, lopsided space around one element, irregular
gaps between siblings and inner groups spaced looser than the stack that holds them.

The scale is learned from the repository (the spacing values its UI files actually use), so a
project with a 6/10/14 rhythm is judged by that rhythm, not by a generic 4 px grid. Proximity
is the rule for asymmetry: a heading or label sits closer to what it introduces than to what
precedes it; the inverse, or a control with three times more room on one side, is the defect.
Covers GPUI chains (Rust), CSS/SCSS blocks, Tailwind classes and Qt layout margins.
"""
import re
from bisect import bisect_right
from collections import Counter
from itertools import count

import core

TOLERANCE = 1.0  # 11 next to 12 is border compensation, not a new step
RATIO, DIFF = 3.0, 12.0  # lopsided: one side 3x the other and 12 px apart
HEAD_RATIO, HEAD_DIFF = 2.0, 8.0  # a heading farther from its content than from what precedes it
RHYTHM_SPREAD = 2.5
NESTED_RATIO, NESTED_DIFF = 1.5, 8.0
COMMON_SHARE = 0.01  # a value used in under 1 % of the project's spacings is not a step
MIN_SAMPLES = 150
FALLBACK_GRID = 4.0
EVEN_OUT = "even it out on one step of the scale, or keep the larger side only where proximity calls for it"

SPACING_METHODS = {"p", "px", "py", "pt", "pb", "pl", "pr", "m", "mx", "my", "mt", "mb", "ml", "mr",
                   "gap", "gap_x", "gap_y"}
SPACER_METHODS = {"h", "w", "flex_shrink_0", "flex_none", "flex_shrink", "id"}
ARG_METHODS = SPACING_METHODS | {"h", "w", "text_size"}
UNIT_METHOD_RE = re.compile(r"^(p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap_x|gap_y)_(\d+)(p5)?$")
PX_ARG_RE = re.compile(r"^\s*px\(\s*(-?\d+(?:\.\d*)?)\s*\)\s*$")
HEADING_TOKEN_RE = re.compile(r"\b(?:HEADLINE|TITLE\w*|HERO|DISPLAY)\b")
HEADING_NAME_RE = re.compile(r"(?i)head(?:ing|er)|title|(?<!accessibility_)label|caption|legend|^h[1-6]\b")
IDENT_RE = re.compile(r"[^\W\d]\w*(?:::[^\W\d]\w*)*!?")
KEYWORDS = {"if", "else", "match", "return", "move", "as", "in", "for", "while", "loop", "ref", "mut",
            "true", "false", "async", "await", "break", "continue", "where", "impl", "fn", "pub", "use"}

RS_VALUE_RE = re.compile(
    r"\.(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap_x|gap_y)\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)"
)
RS_UNIT_RE = re.compile(r"\.(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap_x|gap_y)_(\d+)(p5)?\(\)")
CSS_DECL_RE = re.compile(
    r"(?<![\w-])(padding|margin|gap|row-gap|column-gap)(-(?:top|bottom|left|right|block|inline)(?:-start|-end)?)?"
    r"\s*:\s*([^;{}]+)"
)
PX_RE = re.compile(r"^(-?\d+(?:\.\d*)?)px$|^0$")
TW_PAIR_RE = re.compile(r"(?<![\w:-])((?:[\w-]+:)*)(p|m)([tby])-(\d+(?:\.5)?)(?![\w.\]-])")
TW_HEADING_RE = re.compile(r"(?<![\w-])text-(?:[2-9]?xl)(?![\w-])|(?i:title|heading)")
TW_CLASS_RE = re.compile(r"""\bclass(?:Name)?\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*`([^`]*)`\s*\}|\{\s*"([^"]*)"\s*\})""")
TW_ARBITRARY_RE = re.compile(r"(?<![\w-])(?:p|m)[xytblr]?-\[(\d+(?:\.\d+)?)px\]|(?<![\w-])gap(?:-[xy])?-\[(\d+(?:\.\d+)?)px\]")
TW_UNIT_RE = re.compile(r"(?<![\w-])(?:p|m)[xytblr]?-(\d+(?:\.5)?)(?![\w.\]-])|(?<![\w-])gap(?:-[xy])?-(\d+(?:\.5)?)(?![\w.\]-])")
QT_RE = re.compile(r"\b(setContentsMargins|setSpacing|addSpacing)\(\s*([^)]*)\)")
TOKEN_CONST_RE = re.compile(
    r"\bconst\s+(\w*(?:INSET|GAP|SPACE|SPACING|PAD|PADDING|MARGIN|GUTTER)\w*)\s*:\s*f(?:32|64)\s*=\s*(\d+(?:\.\d*)?)"
)


# ---------------------------------------------------------------- project scale

def file_values(source, suffix):
    """Spacing values (px) written in one file."""
    out = []
    if suffix == ".rs":
        out += [float(v) for v in RS_VALUE_RE.findall(source)]
        out += [int(n) * 4 + (2 if half else 0) for n, half in RS_UNIT_RE.findall(source)]
    elif suffix in (".css", ".scss"):
        for _, _, value in CSS_DECL_RE.findall(source):
            out += [float(m.group(1)) for m in map(PX_RE.match, value.split()) if m and m.group(1)]
    elif suffix in core.JS:
        for line in source.splitlines():
            if "class" in line:
                for unit, gap in TW_UNIT_RE.findall(line):
                    out.append(float(unit or gap) * 4)
                for arbitrary, gap in TW_ARBITRARY_RE.findall(line):
                    out.append(float(arbitrary or gap))
    elif suffix == ".py":
        for _, args in QT_RE.findall(source):
            out += [float(a) for a in re.findall(r"\b\d+(?:\.\d+)?\b", args)]
    return [v for v in out if v > 0]


def learn_scale(sources):
    """(steps, tokens): the values the project uses for spacing (each at least 1 % of all uses),
    or None with too few samples, and the named spacing constants {value: name}."""
    counts, tokens = Counter(), {}
    for source, suffix in sources:
        counts.update(file_values(source, suffix))
        for name, value in TOKEN_CONST_RE.findall(source):
            tokens.setdefault(float(value), name)
    total = sum(counts.values())
    if total < MIN_SAMPLES:
        return None, tokens
    steps = tuple(sorted(v for v, n in counts.items() if n / total >= COMMON_SHARE))
    return steps or None, tokens


def on_scale(value, steps):
    if value <= 2:
        return True
    if steps is None:  # no history to learn from: 2 px steps up to 16, then the 4 px grid
        rest = value % FALLBACK_GRID
        return value <= 16 or min(rest, FALLBACK_GRID - rest) <= TOLERANCE
    return any(abs(value - step) <= TOLERANCE for step in steps)


def nearest(value, steps):
    pool = list(steps or ())
    if not pool or value > max(pool) * 1.25:  # past the scale: the grid, not the largest step
        pool = [FALLBACK_GRID * k for k in range(1, 65)]
    return min(pool, key=lambda step: (abs(step - value), step))


def _fmt(values):
    return ", ".join(f"{v:g}" for v in values)


# ---------------------------------------------------------------- GPUI chains

class Element:
    __slots__ = ("name", "line", "attrs", "order", "methods", "col", "heading", "children")

    def __init__(self, name, line):
        self.name, self.line = name, line
        self.attrs = {}  # method -> (value or None for a token, line)
        self.order = {}  # method -> call position: a later `.p()` overrides an earlier `.pt()`
        self.methods = set()
        short = name.rsplit("::", 1)[-1]
        # True: column, False: row, None: block (stacks vertically); `v_flex()`/`h_flex()` constructors set it.
        self.col = True if short == "v_flex" else False if short == "h_flex" else None
        self.heading = False
        self.children = []

    def value(self, key):
        return self.attrs.get(key, (None, 0))[0]

    def edge_key(self, kind, side):
        """pt/pb (kind "p") or mt/mb ("m"): whichever of the longhand, the axis or the all-sides
        shorthand was called last, as GPUI applies them in order."""
        keys = [key for key in (kind + side, kind + "y", kind) if key in self.attrs]
        return max(keys, key=self.order.get) if keys else None

    def edge(self, kind, side):
        key = self.edge_key(kind, side)
        return self.attrs[key][0] if key else None

    def gap(self):
        for key in ("gap_y", "gap"):
            if key in self.attrs:
                return self.attrs[key][0]
        return None

    def spacer(self):
        return (self.name == "div" and not self.children and self.methods <= SPACER_METHODS
                and self.value("h") is not None)

    def vertical(self):
        return self.col is not False

    def headed(self):
        return self.heading or bool(HEADING_NAME_RE.search(self.name.rsplit("::", 1)[-1])) or any(
            HEADING_NAME_RE.search(m) for m in self.methods if m not in SPACING_METHODS)


class _Frame:
    __slots__ = ("elem", "parent", "inherited", "closure", "used")

    def __init__(self, parent=None, inherited=False):
        self.elem, self.parent, self.inherited, self.closure, self.used = None, parent, inherited, False, False

    @property
    def ignored(self):
        return self.inherited or self.closure


# Comments, strings (raw and multi-line included) and char literals, blanked before parsing so their
# brackets and dots never read as code. A char literal holds one char or escape and closes at once,
# which keeps lifetimes (`'a`) out of it.
RUST_NOISE_RE = re.compile(
    r"/\*[\s\S]*?\*/|//[^\n]*|\bb?r(#*)\"[\s\S]*?\"\1|b?\"(?:[^\"\\]|\\[\s\S])*\""
    r"|b?'(?:[^'\\\n]|\\(?:u\{[0-9a-fA-F]+\}|[^\n]))'"
)


def _blank_rust(text):
    return RUST_NOISE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _close_of(text, at):
    depth = 0
    for index in range(at, len(text)):
        if text[index] in "([{":
            depth += 1
        elif text[index] in ")]}":
            depth -= 1
            if depth == 0:
                return index
    return len(text)


_CALLS = count()


def _record(elem, name, args, line):
    elem.methods.add(name)
    unit = UNIT_METHOD_RE.match(name)
    if unit:
        elem.attrs[unit.group(1)] = (int(unit.group(2)) * 4 + (2 if unit.group(3) else 0), line)
        elem.order[unit.group(1)] = next(_CALLS)
    elif name in SPACING_METHODS or name in ("h", "w"):
        literal = PX_ARG_RE.match(args)
        elem.attrs[name] = (float(literal.group(1)) if literal else None, line)
        elem.order[name] = next(_CALLS)
    elif name in ("flex_col", "v_flex"):
        elem.col = True
    elif name in ("flex", "flex_row", "h_flex") and elem.col is None:
        elem.col = False
    elif name == "text_size":
        literal = re.search(r"px\(\s*(\d+(?:\.\d*)?)", args)
        if HEADING_TOKEN_RE.search(args) or (literal and float(literal.group(1)) >= 18):
            elem.heading = True


def parse_rust(lines):
    """Element chains of a GPUI file: each `div()`/call with its own modifiers and ordered
    `.child(...)` children. Closures (handlers, `.when`, `.map`) are skipped: their modifiers are
    not the element's resting spacing."""
    text = _blank_rust("\n".join(lines))  # same length: offsets still map to the original lines
    starts, pos = [], 0
    for line in lines:
        starts.append(pos)
        pos += len(line) + 1
    line_of = lambda at: bisect_right(starts, at)  # noqa: E731
    elements, names = [], {}
    frames = [_Frame()]
    prev, last_ident, binding = "", "", None
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        frame = frames[-1]
        if char.isspace():
            index += 1
            continue
        if char == "." and text[index + 1:index + 2] != ".":
            match = IDENT_RE.match(text, index + 1)
            if not match:
                index += 1
                prev = "."
                continue
            after = match.end()
            while after < size and text[after] in " \t\n":
                after += 1
            if after < size and text[after] == "(":
                method = match.group(0)
                if frame.elem is not None and not frame.ignored:
                    args = text[after + 1:_close_of(text, after)] if method in ARG_METHODS else ""
                    _record(frame.elem, method, args, line_of(index))
                child_of = frame.elem if method == "child" and frame.elem is not None and not frame.ignored else None
                frames.append(_Frame(child_of, frame.ignored))
                index, prev = after + 1, "("
            else:
                index, prev = match.end(), "x"
            continue
        if char in "([{":
            # `let Some(x) =`, `let (a, b) =`: a pattern, not one name; `let x = { .. }`: a block.
            if binding is not None and (char == "{" or not (isinstance(binding, tuple) and binding[0] == "bind")):
                binding = None
            frames.append(_Frame(None, frame.ignored))
            index, prev = index + 1, char
            continue
        if char in ")]}":
            if len(frames) > 1:
                frames.pop()
            index, prev = index + 1, ")"
            continue
        if char in ",;":
            frame.elem, frame.closure = None, False
            if char == ";":
                binding = None
            index, prev = index + 1, char
            continue
        if char == "|" and (prev in ("(", ",", "=") or (prev == "x" and last_ident == "move")):
            # Skip the whole parameter list: its commas must not end the closure.
            close = text.find("|", index + 1)
            frame.closure = True
            index, prev = (close if close >= 0 else index) + 1, "|"
            continue
        if char == "=" and text[index + 1:index + 2] not in ("=", ">") and prev not in ("=", "!", "<", ">"):
            frame.elem = None
            if isinstance(binding, tuple) and binding[0] == "name":
                binding = ("bind", binding[1])
            index, prev = index + 1, "="
            continue
        if char.isalpha() or char == "_":
            match = IDENT_RE.match(text, index)
            name = match.group(0)
            index, last_ident = match.end(), name
            if frame.ignored:
                prev = "x"
                continue
            if name == "fn":  # bindings are local to a function
                names.clear()
                binding = None
            elif isinstance(binding, tuple) and binding[0] == "bind" and name in KEYWORDS:
                binding = None  # `let x = if ..`/`match ..`: the value is a branch, not the condition
            elif name == "let":
                binding = "let"
            elif binding == "let" and name != "mut":
                binding = ("name", name)
            elif isinstance(binding, tuple) and binding[0] == "name":
                pass  # the type annotation of `let name: Type =`
            elif frame.elem is None and name not in KEYWORDS:
                elem = names.get(name)
                if elem is None:
                    elem = Element(name, line_of(match.start()))
                    elements.append(elem)
                frame.elem = elem
                if isinstance(binding, tuple) and binding[0] == "bind":
                    names[binding[1]] = elem
                    binding = None
                if frame.parent is not None and not frame.used:
                    frame.parent.children.append(elem)
                    frame.used = True
            prev = "x"
            continue
        index, prev = index + 1, char
    return elements


def _lopsided(big, small):
    return big - small >= DIFF and (small <= 0 or big / small >= RATIO)


def rust_findings(rel, lines, fn_of, look, steps, tokens):
    out = []
    elements = parse_rust(lines)
    off = {}
    for elem in elements:
        if "absolute" in elem.methods:  # clearance around overlaid controls is geometry, not rhythm
            continue
        for key, (value, line) in elem.attrs.items():
            if value is None or (key in ("h", "w") and not elem.spacer()) or on_scale(value, steps):
                continue
            off.setdefault(fn_of(line), []).append((value, line))
    for fn, hits in off.items():
        out += _off_scale(rel, fn, hits, look, steps, tokens)

    for elem in elements:
        for kind, label in (("p", "padding"), ("m", "margin")):
            top, bottom = elem.edge(kind, "t"), elem.edge(kind, "b")
            if top is None or bottom is None or min(top, bottom) <= 0:
                continue
            big, small = max(top, bottom), min(top, bottom)
            leads = (elem.headed() or (elem.children and elem.children[0].headed())
                     or HEADING_NAME_RE.search(fn_of(elem.line)))
            if not _lopsided(big, small) or (leads and top > bottom):
                continue
            # Either side may be the edit that broke the balance; anchor on the larger one when both show.
            sides = [elem.attrs[elem.edge_key(kind, side)][1] for side in (("t", "b") if top > bottom else ("b", "t"))]
            line = next((line for line in sides if look(line)), None)
            if line is not None:
                out.append(core.Finding(
                    rel, line, "spacing",
                    f"{fn_of(line)}: lopsided {label} on {_short(elem.name)}: {top:g}px above, {bottom:g}px below",
                    EVEN_OUT,
                ))
        if len(elem.children) >= 2 and elem.vertical():
            out += _sibling_findings(rel, elem, fn_of, look)
    return out


def _short(name):
    return name.rsplit("::", 1)[-1]


def _signature(elem):
    """Siblings of the same kind: same constructor and the same styling calls."""
    return elem.name, frozenset(m for m in elem.methods
                                if m not in SPACING_METHODS and not m.startswith(("on_", "when", "hover", "id", "child")))


def _sibling_findings(rel, parent, fn_of, look):
    """Vertical rhythm inside one container. Space between two items = the container gap once per
    slot, plus spacer heights and the margins that face each other."""
    out = []
    kids = parent.children
    gap = parent.gap() or 0.
    spacers = [kid for kid in kids if kid.spacer()]
    heights = [kid.value("h") for kid in spacers]
    fn = fn_of(parent.line)
    if len(heights) >= 3 and len(set(heights)) >= 3 and min(heights) > 0 and max(heights) / min(heights) >= RHYTHM_SPREAD:
        if any(look(s.line) for s in spacers):
            out.append(core.Finding(
                rel, spacers[0].line, "spacing",
                f"{fn}: irregular spacing between siblings of {_short(parent.name)}: {_fmt(heights)}px",
                "one gap for siblings of the same level (the container's gap) and one larger step between groups",
            ))
    items, between, explicit = [], [], []
    pending, slots, marked = 0., 0, False
    for kid in kids:
        if kid.spacer():
            pending, slots, marked = pending + kid.value("h"), slots + 1, True
            continue
        if items:
            space = gap * (slots + 1) + pending + (items[-1].edge("m", "b") or 0.) + (kid.edge("m", "t") or 0.)
            between.append(space)
            explicit.append(marked or bool(items[-1].edge("m", "b")) or bool(kid.edge("m", "t")) or gap > 0)
        items.append(kid)
        pending, slots, marked = 0., 0, False

    like = [(between[i], items[i + 1]) for i in range(len(between))
            if _signature(items[i]) == _signature(items[i + 1]) and not items[i].headed()]
    if len(like) >= 2:
        wide, narrow = max(like, key=lambda x: x[0]), min(like, key=lambda x: x[0])
        if _lopsided(wide[0], narrow[0]) and (look(wide[1].line) or look(narrow[1].line)):
            out.append(core.Finding(
                rel, wide[1].line, "spacing",
                f"{fn}: uneven spacing between like siblings ({_short(wide[1].name)}): "
                f"{wide[0]:g}px and {narrow[0]:g}px",
                "items of the same level share one gap; reserve the larger step for a new group",
            ))
    for i, item in enumerate(items):
        if not item.headed() or not look(item.line):
            continue
        name = _short(item.name)
        below = between[i] if i < len(between) else None
        above = between[i - 1] if i >= 1 and explicit[i - 1] else None
        if above is not None and below is not None and below - above >= HEAD_DIFF                 and (above <= 0 or below / above >= HEAD_RATIO):
            out.append(core.Finding(
                rel, item.line, "spacing",
                f"{fn}: {name} sits closer to what precedes it ({above:g}px) than to its content ({below:g}px)",
                "put the larger space above the heading so it groups with what it introduces",
            ))
        elif below is not None and i + 1 < len(between) and not items[i + 1].headed():
            after = between[i + 1]
            if below > after and _lopsided(below, after):
                out.append(core.Finding(
                    rel, items[i + 1].line, "spacing",
                    f"{fn}: {_short(items[i + 1].name)} sits {below:g}px from its label {name} "
                    f"but {after:g}px from the next item",
                    "keep a label tight to its control and the wider space between groups",
                ))
    if parent.col and gap > 0:
        for kid in kids:
            inner = kid.gap()
            if kid.col and inner and len(kid.children) >= 2 and inner >= gap * NESTED_RATIO and inner - gap >= NESTED_DIFF:
                line = kid.attrs.get("gap_y", kid.attrs.get("gap"))[1]
                if look(line):
                    out.append(core.Finding(
                        rel, line, "spacing",
                        f"{fn}: inner stack {_short(kid.name)} spaced looser ({inner:g}px) than its parent ({gap:g}px)",
                        "parts of a group sit closer than the groups: shrink the inner gap or widen the outer one",
                    ))
    return out


def _off_scale(rel, where, hits, look, steps, tokens):
    """One finding per off-scale value, on its first reviewed line. The problem text names only
    the value: the learned scale shifts as the repository grows, so it lives in the fix, where it
    cannot change the finding's key (accepts and already-shown dedup)."""
    scale = _fmt(steps) if steps else f"{FALLBACK_GRID:g}px grid"
    out, done = [], set()
    for value, line in sorted(hits, key=lambda hit: hit[1]):
        if value in done or not look(line):
            continue
        done.add(value)
        step = nearest(value, steps)
        token = f" ({tokens[step]})" if step in tokens else ""
        out.append(core.Finding(
            rel, line, "spacing",
            f"{where}: off-scale spacing {value:g}px",
            f"snap to {step:g}px{token}, the nearest step of the project scale ({scale})",
        ))
    return out


# ---------------------------------------------------------------- CSS / Tailwind / Qt

def _css_edges(decls):
    edges = {}
    for prop, side, value, line in decls:
        if prop not in ("padding", "margin"):
            continue
        parts = value.split()
        nums = [PX_RE.match(p) for p in parts]
        if not all(nums):
            continue
        vals = [float(m.group(1) or 0) for m in nums]
        if side in ("-top", "-bottom"):
            edges[(prop, side[1:])] = (vals[0], line)
        elif side == "-block" and vals:
            edges[(prop, "top")] = (vals[0], line)
            edges[(prop, "bottom")] = (vals[-1], line)
        elif not side and vals:
            top, bottom = vals[0], vals[2] if len(vals) >= 3 else vals[0]
            edges[(prop, "top")], edges[(prop, "bottom")] = (top, line), (bottom, line)
    return edges


def _css_blocks(text):
    """(selector, segments) per rule, nested SCSS rules included. The segments are the (start, end)
    stretches of the rule's own body between its nested rules, so a parent's declarations are read
    and never fused into the selector of the rule nested after them."""
    stack, head = [], 0
    for index, char in enumerate(text):
        if char == "{":
            begin = head + text[head:index].rfind(";") + 1
            stack.append((text[begin:index], begin, index + 1, []))
            head = index + 1
        elif char == "}":
            if stack:
                selector, begin, start, nested = stack.pop()
                segments, at = [], start
                for span_start, span_end in nested:
                    segments.append((at, span_start))
                    at = span_end
                segments.append((at, index))
                yield selector, segments
                if stack:
                    stack[-1][3].append((begin, index + 1))
            head = index + 1


def css_findings(rel, lines, look, steps, tokens):
    out = []
    text = "\n".join(lines)
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
    for raw_selector, segments in _css_blocks(text):
        selector = " ".join(raw_selector.split())[-40:] or "rule"
        decls = []
        for start, end in segments:
            for decl in CSS_DECL_RE.finditer(text, start, end):
                line = bisect_right(starts, decl.start())
                decls.append((decl.group(1), decl.group(2) or "", decl.group(3).strip(), line))
        off = [(float(m.group(1)), line) for _, _, value, line in decls
               for m in map(PX_RE.match, value.split()) if m and m.group(1) and not on_scale(float(m.group(1)), steps)]
        out += _off_scale(rel, selector, off, look, steps, tokens)
        edges = _css_edges(decls)
        heading = bool(re.search(r"(?i)\bh[1-6]\b|title|heading|label|legend", selector))
        for prop in ("padding", "margin"):
            if (prop, "top") not in edges or (prop, "bottom") not in edges:
                continue
            (top, top_line), (bottom, bottom_line) = edges[(prop, "top")], edges[(prop, "bottom")]
            if min(top, bottom) <= 0 or not _lopsided(max(top, bottom), min(top, bottom)) \
                    or (heading and top > bottom):
                continue
            # Either side may be the edit that broke the balance; anchor on the larger one when both show.
            sides = (top_line, bottom_line) if top > bottom else (bottom_line, top_line)
            line = next((line for line in sides if look(line)), None)
            if line is not None:
                out.append(core.Finding(
                    rel, line, "spacing",
                    f"{selector}: lopsided {prop}: {top:g}px above, {bottom:g}px below",
                    EVEN_OUT,
                ))
    return out


def _tw_side(pairs, axis, prefix, kind, edge):
    """One edge under a variant: its own pt/pb, then its py, then each outer variant
    (`md:hover:` falls back to `md:`), then the base classes."""
    parts = prefix.split(":")[:-1]
    for scope in [":".join(parts[:k]) + ":" for k in range(len(parts), 0, -1)] + [""]:
        for table in (pairs, axis):
            if (scope, kind, edge) in table:
                return table[(scope, kind, edge)]
    return None


def _tw_elements(line):
    """(tag, classes) per class attribute on the line; a line with only a class helper call
    (`cn(...)`) is one element with no tag."""
    found = []
    for match in TW_CLASS_RE.finditer(line):
        opening = line.rfind("<", 0, match.start())
        tag = re.match(r"<\s*([\w.-]+)", line[opening:]) if opening >= 0 else None
        found.append((tag.group(1) if tag else "", next(group for group in match.groups() if group is not None)))
    return found or [("", line)]


def tailwind_findings(rel, lines, fn_of, look, steps, tokens):
    out = []
    for number, line in enumerate(lines, 1):
        if "class" not in line or not look(number):
            continue
        arbitrary = [float(a or g) for a, g in TW_ARBITRARY_RE.findall(line)]
        out += _off_scale(rel, fn_of(number), [(v, number) for v in arbitrary if not on_scale(v, steps)],
                          look, steps, tokens)
        for tag, classes in _tw_elements(line):
            out += _tw_lopsided(rel, number, fn_of(number), tag, classes)
    return out


def _tw_lopsided(rel, number, fn, tag, classes):
    """One element's classes. Per variant (`md:`, `hover:`): Tailwind applies pt/pb over py whatever
    the class order, and a variant only overrides the classes outside it when it applies."""
    out = []
    pairs, axis = {}, {}
    for prefix, kind, side, unit in TW_PAIR_RE.findall(classes):
        target = axis if side == "y" else pairs
        for edge in (("t", "b") if side == "y" else (side,)):
            target[(prefix, kind, edge)] = float(unit) * 4
    heading = bool(re.fullmatch(r"(?i)h[1-6]", tag)) or bool(TW_HEADING_RE.search(classes))
    for prefix in sorted({key[0] for key in (*pairs, *axis)}):
        for kind, label in (("p", "padding"), ("m", "margin")):
            own = any((prefix, kind, edge) in table for table in (pairs, axis) for edge in "tb")
            top, bottom = (_tw_side(pairs, axis, prefix, kind, edge) for edge in "tb")
            if not own or not top or not bottom or not _lopsided(max(top, bottom), min(top, bottom)):
                continue
            if heading and top > bottom:
                continue
            out.append(core.Finding(
                rel, number, "spacing",
                f"{fn}: lopsided {label}{f' ({prefix})' if prefix else ''}: {top:g}px above, {bottom:g}px below",
                EVEN_OUT,
            ))
    return out


def qt_findings(rel, lines, fn_of, look, steps, tokens):
    out = []
    for number, line in enumerate(lines, 1):
        if not look(number):
            continue
        for call, args in QT_RE.findall(line):
            values = [float(v) for v in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", args)]
            out += _off_scale(rel, fn_of(number), [(v, number) for v in values if not on_scale(v, steps)],
                              look, steps, tokens)
            if call == "setContentsMargins" and len(values) == 4:
                top, bottom = values[1], values[3]
                if min(top, bottom) > 0 and _lopsided(max(top, bottom), min(top, bottom)):
                    out.append(core.Finding(
                        rel, number, "spacing",
                        f"{fn_of(number)}: lopsided margins: {top:g}px above, {bottom:g}px below",
                        EVEN_OUT,
                    ))
    return out


def check(rel, lines, suffix, fn_of, look, steps, tokens):
    if suffix == ".rs":
        return rust_findings(rel, lines, fn_of, look, steps, tokens)
    if suffix in (".css", ".scss"):
        return css_findings(rel, lines, look, steps, tokens)
    if suffix in core.JS:
        return tailwind_findings(rel, lines, fn_of, look, steps, tokens)
    if suffix == ".py":
        return qt_findings(rel, lines, fn_of, look, steps, tokens)
    return []
