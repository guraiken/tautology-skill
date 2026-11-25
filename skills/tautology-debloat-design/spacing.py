"""Spacing rhythm: values off the project's scale, lopsided space around one element, irregular
gaps between siblings and inner groups spaced looser than the stack that holds them.

The scale is learned from the repository (the spacing values its UI files actually use), so a
project with a 6/10/14 rhythm is judged by that rhythm, not by a generic 4 px grid. Proximity
is the rule for asymmetry: a heading or label sits closer to what it introduces than to what
precedes it; the inverse, or a control with three times more room on one side, is the defect.
Covers GPUI/egui (Rust), CSS/SCSS blocks, Tailwind classes (JSX/TSX/Vue/Svelte) and Qt layout margins.
"""
import math
import re
import sys
from bisect import bisect_right
from collections import Counter
from itertools import count
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TAUTOLOGY_DIR = _HERE.parent / "tautology"
if str(_TAUTOLOGY_DIR) not in sys.path:
    sys.path.insert(0, str(_TAUTOLOGY_DIR))

import core  # noqa: E402

TOLERANCE = 1.0  # 11 next to 12 is border compensation on bordered elements / coarse learned scales
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
BORDER_METHODS = {"border", "border_1", "border_2", "border_color", "border_t_1", "border_b_1", "border_l_1", "border_r_1"}
ARG_METHODS = SPACING_METHODS | {"h", "w", "text_size"}
UNIT_METHOD_RE = re.compile(r"^(p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap_x|gap_y)_(\d+)(p5)?$")
PX_ARG_RE = re.compile(r"^\s*px\(\s*(-?\d+(?:\.\d*)?)\s*\)\s*$")
HEADING_TOKEN_RE = re.compile(r"\b(?:HEADLINE|TITLE\w*|HERO|DISPLAY)\b")
HEADING_NAME_RE = re.compile(r"(?i)head(?:ing|er)|title|(?<!accessibility_)label|caption|legend|^h[1-6]\b")
INPUT_LIKE_RE = re.compile(r"(?i)(?:^|[_-])(?:input|field|search|select|textarea|combo|text_field|search_box)\b")
IDENT_RE = re.compile(r"[^\W\d]\w*(?:::[^\W\d]\w*)*!?")
KEYWORDS = {"if", "else", "match", "return", "move", "as", "in", "for", "while", "loop", "ref", "mut",
            "true", "false", "async", "await", "break", "continue", "where", "impl", "fn", "pub", "use"}

RS_VALUE_RE = re.compile(
    r"\.(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap_x|gap_y)\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)"
)
RS_UNIT_RE = re.compile(r"\.(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap_x|gap_y)_(\d+)(p5)?\(\)")
EGUI_SPACE_RE = re.compile(r"\bui\.add_space\(\s*(\d+(?:\.\d*)?)\s*\)")
CSS_DECL_RE = re.compile(
    r"(?<![\w-])(padding|margin|gap|row-gap|column-gap)(-(?:top|bottom|left|right|block|inline)(?:-start|-end)?)?"
    r"\s*:\s*([^;{}]+)"
)
CSS_BORDER_RE = re.compile(
    r"(?<![\w-])border(?:-(?:top|bottom|left|right|width|color|style))?\s*:\s*(?!\s*(?:0(?:\s*;|$)|none\b|transparent\b))",
    re.I,
)
CSS_LEN_RE = re.compile(r"^(-?\d+(?:\.\d*)?)(px|rem)$|^0$")
TW_SPACING_RE = re.compile(
    r"(?<![\w:-])((?:[\w\[\]/-]+:)*)(p|m)([xytrbl]?)-(?:(\d+(?:\.5)?)|\[(\d+(?:\.\d+)?)(px|rem)\])(?![\w.\]-])"
)
TW_HEADING_RE = re.compile(r"(?<![\w-])text-(?:[2-9]?xl)(?![\w-])|(?i:title|heading)")
TW_BORDER_RE = re.compile(r"(?<![\w-])border(?!-(?:0|none|transparent)\b)(?:-[\w/\[\]#-]+)?(?![\w-])")
TW_ARBITRARY_RE = re.compile(
    r"(?<![\w-])(?:p|m)[xytblr]?-\[(\d+(?:\.\d+)?)(px|rem)\]"
    r"|(?<![\w-])gap(?:-[xy])?-\[(\d+(?:\.\d+)?)(px|rem)\]"
)
TW_UNIT_RE = re.compile(
    r"(?<![\w-])(?:p|m)[xytblr]?-(\d+(?:\.5)?)(?![\w.\]-])"
    r"|(?<![\w-])gap(?:-[xy])?-(\d+(?:\.5)?)(?![\w.\]-])"
)
QT_RE = re.compile(r"\b(setContentsMargins|setSpacing|addSpacing)\(\s*([^)]*)\)")
TOKEN_CONST_RE = re.compile(
    r"\bconst\s+(\w*(?:INSET|GAP|SPACE|SPACING|PAD|PADDING|MARGIN|GUTTER)\w*)\s*:\s*f(?:32|64)\s*=\s*(\d+(?:\.\d*)?)"
)
MARKUP_EXT = {".vue", ".svelte"}
STYLE_TAG_RE = re.compile(r"<style\b[^>]*>([\s\S]*?)</style>", re.I)


# ---------------------------------------------------------------- JSX tag normalization

_JSX_TAG_START_RE = re.compile(r"<([A-Za-z_][\w.:-]*)(?=[\s/>]|$)")
_JSX_PREV_OK = set("([{,=?:&|!>\n\r \t")


def _local_normalize_jsx_tags(lines):
    """Joins multi-line JSX/HTML opening tags (<Tag\\n  ...\\n>) onto their opening line,
    replacing absorbed continuation lines with "" so 1-to-1 line numbers are preserved."""
    n = len(lines)
    out = list(lines)
    i = 0
    while i < n:
        line = out[i]
        if "<" not in line:
            i += 1
            continue
        # Find an opening tag on this line that is NOT closed before EOL.
        pos = 0
        opened = False
        while pos < len(line):
            ch = line[pos]
            if ch in ('"', "'", "`"):
                q = ch
                pos += 1
                while pos < len(line):
                    if line[pos] == "\\" and pos + 1 < len(line):
                        pos += 2
                        continue
                    if line[pos] == q:
                        pos += 1
                        break
                    pos += 1
                continue
            if line[pos:pos + 2] == "//":
                break
            if ch == "<":
                m = _JSX_TAG_START_RE.match(line, pos)
                if m:
                    # Exclude TypeScript generics (e.g. `useState<Foo`, `Array<T`, `React.FC<Props`).
                    prev_idx = pos - 1
                    while prev_idx >= 0 and line[prev_idx] in " \t":
                        prev_idx -= 1
                    prev_ok = prev_idx < 0 or line[prev_idx] in _JSX_PREV_OK or line[:pos].rstrip().endswith(("return", "=>"))
                    if prev_ok:
                        # Scan from m.end() to see if tag closes on this line.
                        j = m.end()
                        braces = 0
                        quote = None
                        closed = False
                        while j < len(line):
                            c = line[j]
                            if quote:
                                if c == "\\" and j + 1 < len(line):
                                    j += 2
                                    continue
                                if c == quote:
                                    quote = None
                            else:
                                if c in ('"', "'", "`"):
                                    quote = c
                                elif c == "{":
                                    braces += 1
                                elif c == "}":
                                    braces = max(0, braces - 1)
                                elif c == ">" and braces == 0:
                                    closed = True
                                    pos = j + 1
                                    break
                            j += 1
                        if closed:
                            continue
                        # Unclosed opening tag at EOL: scan forward up to 30 lines to find closing `>`.
                        parts = [line]
                        k = i + 1
                        found_end = -1
                        while k < n and k - i <= 30:
                            nxt = out[k]
                            # Strip line comments outside strings when joining
                            j2 = 0
                            while j2 < len(nxt):
                                c = nxt[j2]
                                if quote:
                                    if c == "\\" and j2 + 1 < len(nxt):
                                        j2 += 2
                                        continue
                                    if c == quote:
                                        quote = None
                                else:
                                    if nxt[j2:j2 + 2] == "//":
                                        nxt = nxt[:j2]
                                        break
                                    if c in ('"', "'", "`"):
                                        quote = c
                                    elif c == "{":
                                        braces += 1
                                    elif c == "}":
                                        braces = max(0, braces - 1)
                                    elif c == ">" and braces == 0:
                                        found_end = k
                                        break
                                j2 += 1
                            parts.append(nxt.strip())
                            if found_end >= 0:
                                break
                            if braces == 0 and quote is None and nxt.rstrip().endswith(";"):
                                break
                            k += 1
                        if found_end >= 0:
                            out[i] = " ".join(p for p in parts if p)
                            for clr in range(i + 1, found_end + 1):
                                out[clr] = ""
                            opened = True
                            i = found_end + 1
                            break
            pos += 1
        if not opened:
            i += 1
    return out


normalize_jsx_tags = getattr(core, "normalize_jsx_tags", _local_normalize_jsx_tags)


def _absorbed_look(raw_lines, norm_lines, look):
    """Wraps look(n) so an edit on a line absorbed into an earlier normalized tag still triggers."""
    absorbed = {}
    anchor = None
    for idx in range(len(raw_lines)):
        ln = idx + 1
        if norm_lines[idx] != raw_lines[idx]:
            if norm_lines[idx] != "":
                anchor = ln
                absorbed[anchor] = [anchor]
            elif anchor is not None:
                absorbed[anchor].append(ln)
        else:
            anchor = None
    if not absorbed:
        return look

    def eff_look(n):
        if look(n):
            return True
        return any(look(orig) for orig in absorbed.get(n, ()))

    return eff_look


def _parse_css_len(token):
    """Parses a CSS length token in px, rem (*16), or 0; returns float or None (e.g. for auto/var/%)."""
    m = CSS_LEN_RE.match(token)
    if not m:
        return None
    if m.group(1) is None:
        return 0.0
    val = float(m.group(1))
    return val * 16.0 if m.group(2) == "rem" else val


# ---------------------------------------------------------------- project scale

def file_values(source, suffix):
    """Spacing values (px) written in one file."""
    out = []
    if suffix == ".rs":
        out += [float(v) for v in RS_VALUE_RE.findall(source)]
        out += [int(n) * 4 + (2 if half else 0) for n, half in RS_UNIT_RE.findall(source)]
        out += [float(v) for v in EGUI_SPACE_RE.findall(source)]
    elif suffix in (".css", ".scss"):
        for _, _, value in CSS_DECL_RE.findall(source):
            cleaned = re.sub(r"\s*!important\b", "", value, flags=re.I)
            for tok in cleaned.split():
                parsed = _parse_css_len(tok)
                if parsed is not None and parsed > 0:
                    out.append(parsed)
    elif suffix in (core.JS | MARKUP_EXT):
        norm = normalize_jsx_tags(source.splitlines())
        for line in norm:
            if "class" in line or "cn(" in line or "clsx(" in line:
                for unit, gap in TW_UNIT_RE.findall(line):
                    out.append(float(unit or gap) * 4)
                for a_val, a_unit, g_val, g_unit in TW_ARBITRARY_RE.findall(line):
                    val = float(a_val or g_val)
                    unit = a_unit or g_unit
                    out.append(val * 16.0 if unit == "rem" else val)
        if suffix in MARKUP_EXT:
            for style_m in STYLE_TAG_RE.finditer(source):
                out += file_values(style_m.group(1), ".css")
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


def on_scale(value, steps, bordered=False):
    if value <= 2:
        return True
    eps = 1e-6
    if steps is None:  # fallback: exact 2 px steps up to 16, exact 4 px grid above (border-comp +/-1 only if bordered)
        if value <= 16:
            if min(value % 2.0, 2.0 - (value % 2.0)) <= eps:
                return True
            if bordered:
                rest4 = value % FALLBACK_GRID
                return min(rest4, FALLBACK_GRID - rest4) <= TOLERANCE + eps
            return False
        rest = value % FALLBACK_GRID
        tol = TOLERANCE if bordered else 0.0
        return min(rest, FALLBACK_GRID - rest) <= tol + eps
    if value > max(steps) * 1.25:  # values above the learned scale snap to the 4 px grid
        rest = value % FALLBACK_GRID
        tol = TOLERANCE if bordered else 0.0
        return min(rest, FALLBACK_GRID - rest) <= tol + eps
    min_gap = min((b - a for a, b in zip(steps, steps[1:])), default=FALLBACK_GRID)
    tol = TOLERANCE if (bordered or min_gap >= FALLBACK_GRID) else 0.0
    return any(abs(value - step) <= tol + eps for step in steps)


def nearest(value, steps):
    pool = list(steps or ())
    if not pool:
        if value <= 16:
            lower = math.floor(value / 2.0) * 2.0
            upper = lower + 2.0
            return max(2.0, lower if value - lower <= upper - value else upper)
        lower = math.floor(value / FALLBACK_GRID) * FALLBACK_GRID
        upper = lower + FALLBACK_GRID
        return max(FALLBACK_GRID, lower if value - lower <= upper - value else upper)
    if value > max(pool) * 1.25:  # past the scale: the grid, not the largest step
        lower = math.floor(value / FALLBACK_GRID) * FALLBACK_GRID
        upper = lower + FALLBACK_GRID
        return max(FALLBACK_GRID, lower if value - lower <= upper - value else upper)
    return min(pool, key=lambda step: (abs(step - value), step))


def _fmt(values):
    return ", ".join(f"{v:g}" for v in values)


# ---------------------------------------------------------------- GPUI / egui chains

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
        """pt/pb/pl/pr (kind "p") or mt/mb/ml/mr ("m"): whichever of the longhand, axis, or
        all-sides shorthand was called last, as GPUI applies them in order."""
        axis = "y" if side in ("t", "b") else "x"
        keys = [key for key in (kind + side, kind + axis, kind) if key in self.attrs]
        return max(keys, key=self.order.get) if keys else None

    def edge(self, kind, side):
        key = self.edge_key(kind, side)
        return self.attrs[key][0] if key else None

    def bordered(self):
        return bool(self.methods & BORDER_METHODS)

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

    def input_like(self):
        short = self.name.rsplit("::", 1)[-1]
        return bool(INPUT_LIKE_RE.search(short)) or any(INPUT_LIKE_RE.search(m) for m in self.methods)


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
        is_bordered = elem.bordered()
        for key, (value, line) in elem.attrs.items():
            if value is None or (key in ("h", "w") and not elem.spacer()) or on_scale(value, steps, bordered=is_bordered):
                continue
            off.setdefault(fn_of(line), []).append((value, line))
    # egui spacing calls: ui.add_space(px)
    blanked = _blank_rust("\n".join(lines)).splitlines()
    for number, line in enumerate(blanked, 1):
        for m in EGUI_SPACE_RE.finditer(line):
            value = float(m.group(1))
            if not on_scale(value, steps, bordered=False):
                off.setdefault(fn_of(number), []).append((value, number))
    for fn, hits in off.items():
        out += _off_scale(rel, fn, hits, look, steps, tokens)

    for elem in elements:
        for kind, label in (("p", "padding"), ("m", "margin")):
            # Vertical lopsided padding/margin (preserves all existing proximity rules)
            top, bottom = elem.edge(kind, "t"), elem.edge(kind, "b")
            if top is not None and bottom is not None and min(top, bottom) > 0:
                big, small = max(top, bottom), min(top, bottom)
                leads = (elem.headed() or (elem.children and elem.children[0].headed())
                         or HEADING_NAME_RE.search(fn_of(elem.line)))
                if _lopsided(big, small) and not (leads and top > bottom):
                    sides = [elem.attrs[elem.edge_key(kind, side)][1] for side in (("t", "b") if top > bottom else ("b", "t"))]
                    line = next((line for line in sides if look(line)), None)
                    if line is not None:
                        out.append(core.Finding(
                            rel, line, "spacing",
                            f"{fn_of(line)}: lopsided {label} on {_short(elem.name)}: {top:g}px above, {bottom:g}px below",
                            EVEN_OUT,
                        ))
            # Horizontal lopsided padding/margin (skips input/search fields with leading icons)
            if not elem.input_like():
                left, right = elem.edge(kind, "l"), elem.edge(kind, "r")
                if left is not None and right is not None and min(left, right) > 0:
                    big, small = max(left, right), min(left, right)
                    if _lopsided(big, small):
                        sides = [elem.attrs[elem.edge_key(kind, side)][1] for side in (("l", "r") if left > right else ("r", "l"))]
                        line = next((line for line in sides if look(line)), None)
                        if line is not None:
                            out.append(core.Finding(
                                rel, line, "spacing",
                                f"{fn_of(line)}: lopsided {label} on {_short(elem.name)}: {left:g}px left, {right:g}px right",
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
        if above is not None and below is not None and below - above >= HEAD_DIFF and (above <= 0 or below / above >= HEAD_RATIO):
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
        above_scale = bool(steps) and value > max(steps) * 1.25
        target_desc = (
            f"the nearest {FALLBACK_GRID:g}px grid step above the project scale ({scale})"
            if above_scale
            else f"the nearest step of the project scale ({scale})"
        )
        out.append(core.Finding(
            rel, line, "spacing",
            f"{where}: off-scale spacing {value:g}px",
            f"snap to {step:g}px{token}, {target_desc}",
        ))
    return out


# ---------------------------------------------------------------- CSS / Tailwind / Qt / Vue / Svelte

def _css_edges(decls):
    edges = {}
    for prop, side, value, line in decls:
        if prop not in ("padding", "margin"):
            continue
        cleaned = re.sub(r"\s*!important\b", "", value, flags=re.I).strip()
        parts = cleaned.split()
        if not parts:
            continue
        vals = [_parse_css_len(p) for p in parts]
        if side in ("-top", "-bottom", "-left", "-right"):
            if vals[0] is not None:
                edges[(prop, side[1:])] = (vals[0], line)
        elif side == "-block":
            if vals[0] is not None:
                edges[(prop, "top")] = (vals[0], line)
            if vals[-1] is not None:
                edges[(prop, "bottom")] = (vals[-1], line)
        elif side == "-block-start" and vals[0] is not None:
            edges[(prop, "top")] = (vals[0], line)
        elif side == "-block-end" and vals[0] is not None:
            edges[(prop, "bottom")] = (vals[0], line)
        elif side == "-inline":
            if vals[0] is not None:
                edges[(prop, "left")] = (vals[0], line)
            if vals[-1] is not None:
                edges[(prop, "right")] = (vals[-1], line)
        elif side == "-inline-start" and vals[0] is not None:
            edges[(prop, "left")] = (vals[0], line)
        elif side == "-inline-end" and vals[0] is not None:
            edges[(prop, "right")] = (vals[0], line)
        elif not side:
            n = len(vals)
            top = vals[0]
            right = vals[1] if n >= 2 else vals[0]
            bottom = vals[2] if n >= 3 else vals[0]
            left = vals[3] if n >= 4 else (vals[1] if n >= 2 else vals[0])
            if top is not None:
                edges[(prop, "top")] = (top, line)
            if bottom is not None:
                edges[(prop, "bottom")] = (bottom, line)
            if left is not None:
                edges[(prop, "left")] = (left, line)
            if right is not None:
                edges[(prop, "right")] = (right, line)
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
        rule_body = "\n".join(text[start:end] for start, end in segments)
        is_bordered = bool(CSS_BORDER_RE.search(rule_body))
        for start, end in segments:
            for decl in CSS_DECL_RE.finditer(text, start, end):
                line = bisect_right(starts, decl.start())
                decls.append((decl.group(1), decl.group(2) or "", decl.group(3).strip(), line))
        off = []
        for _, _, value, line in decls:
            cleaned = re.sub(r"\s*!important\b", "", value, flags=re.I)
            for tok in cleaned.split():
                parsed = _parse_css_len(tok)
                if parsed is not None and parsed > 0 and not on_scale(parsed, steps, bordered=is_bordered):
                    off.append((parsed, line))
        out += _off_scale(rel, selector, off, look, steps, tokens)
        edges = _css_edges(decls)
        heading = bool(re.search(r"(?i)\bh[1-6]\b|title|heading|label|legend", selector))
        input_like = bool(INPUT_LIKE_RE.search(selector))
        for prop in ("padding", "margin"):
            if (prop, "top") in edges and (prop, "bottom") in edges:
                (top, top_line), (bottom, bottom_line) = edges[(prop, "top")], edges[(prop, "bottom")]
                if min(top, bottom) > 0 and _lopsided(max(top, bottom), min(top, bottom)) and not (heading and top > bottom):
                    sides = (top_line, bottom_line) if top > bottom else (bottom_line, top_line)
                    line = next((line for line in sides if look(line)), None)
                    if line is not None:
                        out.append(core.Finding(
                            rel, line, "spacing",
                            f"{selector}: lopsided {prop}: {top:g}px above, {bottom:g}px below",
                            EVEN_OUT,
                        ))
            if not input_like and (prop, "left") in edges and (prop, "right") in edges:
                (left, left_line), (right, right_line) = edges[(prop, "left")], edges[(prop, "right")]
                if min(left, right) > 0 and _lopsided(max(left, right), min(left, right)):
                    sides = (left_line, right_line) if left > right else (right_line, left_line)
                    line = next((line for line in sides if look(line)), None)
                    if line is not None:
                        out.append(core.Finding(
                            rel, line, "spacing",
                            f"{selector}: lopsided {prop}: {left:g}px left, {right:g}px right",
                            EVEN_OUT,
                        ))
    return out


def _tw_side(pairs, axis, all_sides, prefix, kind, edge):
    """One edge under a variant: its own pt/pb/pl/pr, then its py/px, then all-sides p/m,
    then each outer variant (`md:hover:` falls back to `md:`), then the base classes."""
    parts = prefix.split(":")[:-1]
    for scope in [":".join(parts[:k]) + ":" for k in range(len(parts), 0, -1)] + [""]:
        for table in (pairs, axis, all_sides):
            if (scope, kind, edge) in table:
                return table[(scope, kind, edge)]
    return None


def _tw_elements(line):
    """(tag, classes) per class/className attribute on the line (including balanced `{cn(...)}`),
    or `[("", line)]` for standalone helper lines (`cn(...)`)."""
    found = []
    pos = 0
    size = len(line)
    for m in re.finditer(r"\bclass(?:Name)?\s*=\s*", line):
        if m.start() < pos:
            continue
        start = m.end()
        if start >= size:
            continue
        ch = line[start]
        body = None
        end = start
        if ch in ('"', "'"):
            j = start + 1
            while j < size:
                if line[j] == "\\" and j + 1 < size:
                    j += 2
                    continue
                if line[j] == ch:
                    body = line[start + 1:j]
                    end = j + 1
                    break
                j += 1
        elif ch == "{":
            depth = 0
            quote = None
            j = start
            while j < size:
                c = line[j]
                if quote:
                    if c == "\\" and j + 1 < size:
                        j += 2
                        continue
                    if c == quote:
                        quote = None
                else:
                    if c in ('"', "'", "`"):
                        quote = c
                    elif c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            body = line[start + 1:j]
                            end = j + 1
                            break
                j += 1
        if body is not None:
            opening = line.rfind("<", 0, m.start())
            tag = re.match(r"<\s*([\w.-]+)", line[opening:]) if opening >= 0 else None
            found.append((tag.group(1) if tag else "", body))
            pos = end
    return found or [("", line)]


_CN_CALL_RE = re.compile(r"\b(?:cn|clsx|twMerge|cva)\s*\(")


def _join_cn_calls(lines):
    """Joins standalone multi-line cn(...)/clsx(...)/twMerge(...) calls onto their opening line,
    replacing absorbed continuation lines with '' so 1-to-1 line numbers stay intact."""
    n = len(lines)
    out = list(lines)
    i = 0
    while i < n:
        line = out[i]
        m = _CN_CALL_RE.search(line)
        if not m:
            i += 1
            continue
        depth = 0
        quote = None
        closed = False
        for j in range(m.end() - 1, len(line)):
            c = line[j]
            if quote:
                if c == "\\":
                    continue
                if c == quote:
                    quote = None
            else:
                if c in ('"', "'", "`"):
                    quote = c
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        closed = True
                        break
        if closed or depth <= 0:
            i += 1
            continue
        parts = [line]
        k = i + 1
        found_end = -1
        while k < n and k - i <= 20:
            nxt = out[k]
            for j in range(len(nxt)):
                c = nxt[j]
                if quote:
                    if c == "\\":
                        continue
                    if c == quote:
                        quote = None
                else:
                    if nxt[j:j + 2] == "//":
                        nxt = nxt[:j]
                        break
                    if c in ('"', "'", "`"):
                        quote = c
                    elif c == "(":
                        depth += 1
                    elif c == ")":
                        depth -= 1
                        if depth == 0:
                            found_end = k
                            break
            parts.append(nxt.strip())
            if found_end >= 0:
                break
            k += 1
        if found_end >= 0:
            out[i] = " ".join(p for p in parts if p)
            for clr in range(i + 1, found_end + 1):
                out[clr] = ""
            i = found_end + 1
        else:
            i += 1
    return out


def tailwind_findings(rel, lines, fn_of, look, steps, tokens):
    out = []
    norm_lines = _join_cn_calls(normalize_jsx_tags(lines))
    eff_look = _absorbed_look(lines, norm_lines, look)
    for number, line in enumerate(norm_lines, 1):
        if not eff_look(number):
            continue
        if "class" not in line and "cn(" not in line and "clsx(" not in line and "twMerge(" not in line:
            continue
        for tag, classes in _tw_elements(line):
            is_bordered = bool(TW_BORDER_RE.search(classes))
            arbitrary = []
            for a_val, a_unit, g_val, g_unit in TW_ARBITRARY_RE.findall(classes):
                val = float(a_val or g_val)
                unit = a_unit or g_unit
                px_val = val * 16.0 if unit == "rem" else val
                if not on_scale(px_val, steps, bordered=is_bordered):
                    arbitrary.append((px_val, number))
            out += _off_scale(rel, fn_of(number), arbitrary, eff_look, steps, tokens)
            out += _tw_lopsided(rel, number, fn_of(number), tag, classes)
    return out


def _tw_lopsided(rel, number, fn, tag, classes):
    """One element's classes. Per variant (`md:`, `hover:`): Tailwind applies pt/pb over py over p
    whatever the class order, and a variant only overrides outer variants when it applies."""
    out = []
    pairs, axis, all_sides = {}, {}, {}
    for prefix, kind, side, unit, arb_val, arb_unit in TW_SPACING_RE.findall(classes):
        if unit:
            px_val = float(unit) * 4.0
        else:
            px_val = float(arb_val) * (16.0 if arb_unit == "rem" else 1.0)
        if side == "":
            for edge in "tblr":
                all_sides[(prefix, kind, edge)] = px_val
        elif side == "y":
            for edge in "tb":
                axis[(prefix, kind, edge)] = px_val
        elif side == "x":
            for edge in "lr":
                axis[(prefix, kind, edge)] = px_val
        else:
            pairs[(prefix, kind, side)] = px_val
    heading = bool(re.fullmatch(r"(?i)h[1-6]", tag)) or bool(TW_HEADING_RE.search(classes))
    input_like = bool(INPUT_LIKE_RE.search(tag)) or bool(re.search(r"(?i)\b(?:search|input)\b", tag))
    for prefix in sorted({key[0] for key in (*pairs, *axis, *all_sides)}):
        for kind, label in (("p", "padding"), ("m", "margin")):
            # Vertical check (t / b)
            own_v = any((prefix, kind, edge) in table for table in (pairs, axis, all_sides) for edge in "tb")
            top, bottom = (_tw_side(pairs, axis, all_sides, prefix, kind, edge) for edge in "tb")
            if own_v and top and bottom and _lopsided(max(top, bottom), min(top, bottom)):
                if not (heading and top > bottom):
                    out.append(core.Finding(
                        rel, number, "spacing",
                        f"{fn}: lopsided {label}{f' ({prefix})' if prefix else ''}: {top:g}px above, {bottom:g}px below",
                        EVEN_OUT,
                    ))
            # Horizontal check (l / r)
            if not input_like:
                own_h = any((prefix, kind, edge) in table for table in (pairs, axis, all_sides) for edge in "lr")
                left, right = (_tw_side(pairs, axis, all_sides, prefix, kind, edge) for edge in "lr")
                if own_h and left and right and _lopsided(max(left, right), min(left, right)):
                    out.append(core.Finding(
                        rel, number, "spacing",
                        f"{fn}: lopsided {label}{f' ({prefix})' if prefix else ''}: {left:g}px left, {right:g}px right",
                        EVEN_OUT,
                    ))
    return out


def _sfc_style_lines(lines):
    """Returns a same-length line list keeping only `<style>...</style>` CSS content for Vue/Svelte."""
    text = "\n".join(lines)
    if not STYLE_TAG_RE.search(text):
        return None
    out = [""] * len(lines)
    starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
    for m in STYLE_TAG_RE.finditer(text):
        first = bisect_right(starts, m.start(1)) - 1
        last = bisect_right(starts, m.end(1)) - 1
        for idx in range(max(0, first), min(len(lines), last + 1)):
            out[idx] = re.sub(r"</?style\b[^>]*>", "", lines[idx], flags=re.I)
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
                left, top, right, bottom = values
                if min(top, bottom) > 0 and _lopsided(max(top, bottom), min(top, bottom)):
                    out.append(core.Finding(
                        rel, number, "spacing",
                        f"{fn_of(number)}: lopsided margins: {top:g}px above, {bottom:g}px below",
                        EVEN_OUT,
                    ))
                if min(left, right) > 0 and _lopsided(max(left, right), min(left, right)):
                    out.append(core.Finding(
                        rel, number, "spacing",
                        f"{fn_of(number)}: lopsided margins: {left:g}px left, {right:g}px right",
                        EVEN_OUT,
                    ))
    return out


def check(rel, lines, suffix, fn_of, look, steps, tokens):
    if suffix == ".rs":
        return rust_findings(rel, lines, fn_of, look, steps, tokens)
    if suffix in (".css", ".scss"):
        return css_findings(rel, lines, look, steps, tokens)
    if suffix in MARKUP_EXT:
        out = tailwind_findings(rel, lines, fn_of, look, steps, tokens)
        style_lines = _sfc_style_lines(lines)
        if style_lines is not None:
            out += css_findings(rel, style_lines, look, steps, tokens)
        return out
    if suffix in core.JS:
        return tailwind_findings(rel, lines, fn_of, look, steps, tokens)
    if suffix == ".py":
        return qt_findings(rel, lines, fn_of, look, steps, tokens)
    return []
