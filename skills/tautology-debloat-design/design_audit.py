"""Static visual-design auditor for screens: overloaded hierarchy, visual repetition, excessive
padding/clutter, hardcoded styles outside theme files, UI state inventory, and PNG visual checks.

    python design_audit.py --find settings                  # locate screen files by name/symbol/title
    python design_audit.py --states path/to/screen          # list interactive UI states, modals, and hooks
    python design_audit.py --all path/to/screen             # full audit of the target screen
    python design_audit.py --diff                           # changed UI lines against HEAD
    python design_audit.py --only design,style ...          # checks: design, style, bloat
    python design_audit.py --check-image shot.png           # verify screenshot is non-blank and measure density
    python design_audit.py --compare before.png after.png   # diff regions & visual density between captures
    python design_audit.py --accept "<line>" --reason "<why>"
    python design_audit.py --selftest
"""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAUTOLOGY_DIR = HERE.parent / "tautology"
MODULAR_DIR = HERE.parent / "modular"
CASES = HERE / "cases"
for extra in (HERE, TAUTOLOGY_DIR, MODULAR_DIR):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import core  # noqa: E402
import copy_rules  # noqa: E402
import hunt  # noqa: E402
import visual_check  # noqa: E402

CHECKS = {"design", "style", "bloat"}
STYLE_EXT = {".css", ".scss"}
SCAN_EXT = core.CODE | STYLE_EXT
DEFAULT_THEME_RE = re.compile(r"(^|/)(?:(?:theme|tokens|palette|colors)(?:/|\.)|styles?/)", re.I)
GENERIC_UI_DIRS = {"screens", "views", "pages", "components", "dialogs", "modals", "ui", "src"}
SCREEN_SUBDIRS = {"view", "views", "ui", "dialogs", "modals", "detail"}

# Patterns for structural UI cues across GPUI (Rust), React/HTML (JSX/TSX), and Qt (Python).
STYLED_BOX_RE = re.compile(
    r"\b(?:solid_card|surface_card|form_card|modal_card|panel|card|QFrame|QGroupBox)\s*\("
    r"|<(?:Card|Paper|Panel|Box|Modal|Dialog)\b"
    r"|\.(?:bg|border_[1-9]\w*|border_color|shadow_\w+)\("
    r"|<(?:div|section|aside|article)\b[^>]*(?:bg-|shadow|card|rounded|\bborder(?!-0\b))"
)
CARD_CALL_RE = re.compile(
    r"\b(?:solid_card|surface_card|form_card|modal_card)\s*\(|"
    r"<(?:Card|Paper|Panel)\b|\.rounded\([^)]*\)\s*\.bg\("
)
MICRO_RE = re.compile(
    r"\b(?:button|reveal_button|secondary_button|primary_button|close_button|icon|tag|badge|chip|switch|check_box)\s*\("
    r"|\bsize\(px\((?:[1-9]|1[0-9]|2[0-9]|3[0-6])\.?\)"
)
DIVIDER_RE = re.compile(
    r"\b(?:divider|separator|row_separator)\s*\(|<hr\b|<Divider\b|QFrame[\.:]+HLine|\.h\(px\([12]\.?\)\)"
)
BADGE_RE = re.compile(r"\b(?:visual::tag|tag|badge|chip|status_pill)\s*\(|<(?:Badge|Chip|Tag)\b")
ICON_RE = re.compile(r"\b(?:visual::icon|icon)\s*\(|<(?:[A-Z]\w*Icon|Icon)\b")
INFO_HINT_RE = re.compile(r"\binfo_hint\s*\(|<(?:InfoHint|HelpTip|HintIcon)\b")
PRIMARY_ACCENT_RE = re.compile(
    r"\bprimary_button\s*\("
    r"|\.bg\(\s*\w+\.primary\s*\)"
    r"|\.pill\([^;\n]*,\s*\w+\.primary\b"
    r"|\bvariant\s*=\s*[\"']primary[\"']"
    r"|\bbg-primary\b(?!/)"
    r"|<PrimaryButton\b"
    r"|\.setProperty\(\s*[\"']primary[\"']\s*,\s*True\s*\)"
)
FILTER_TRIGGER_RE = re.compile(
    r"\b(?:menu_trigger|menu_trigger_with_state)\s*\("
    r"|<(?:Select|select|Dropdown|FilterSelect|SortSelect)\b"
    r"|\bQComboBox\s*\("
)
FILTER_CHIP_RE = re.compile(r"\bfilter_chip\s*\(|<(?:FilterChip|FilterPill|ToggleButton)\b")
MODAL_CALLEE_RE = re.compile(r"(?:^|_)(?:modal|dialog|overlay|popup)(?:s|_|$)", re.I)
FIXED_TRIGGER_WIDTH_RE = re.compile(
    r"\.w\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)"
    r"|\bw-\[(\d+(?:\.\d*)?)px\]"
    r"|\.setFixedWidth\(\s*(\d+)\s*\)"
)
LOOP_START_RE = re.compile(r"^\s*(?:for\s+\w|\.for_each\s*\(|\.map\(\s*\|)")
SPACER_RE = re.compile(
    r"\bdiv\(\)\s*\.(?:h|w)\(px\((?P<px>\d+(?:\.\d*)?)\)\)\s*(?:[,;)]|$)|<Spacer\b|\.addSpacing\("
)
TEXT_STACK_CALL_RE = re.compile(
    r"\.(?:child)\(\s*(?:div\(\)[^;{}]*\.text_size|\"|format!)|<(?:p|span|small|Caption|Text)\b"
)
HEADER_NAME_RE = re.compile(r"(?i)header|title|heading|subtitle|intro|banner|card_head")

# Patterns for hardcoded styling literals.
CSS_FIX = "use CSS custom property or theme token"
QSS_FIX = "use theme palette/QSS variables"
RS_HEX_COLOR_RE = re.compile(r"\b(?:gpui::)?(rgb|rgba|hsla)\(\s*(0x[0-9a-fA-F_]{6,12}|[^)]+)\)")
RS_RGBA_LIT_RE = re.compile(r"\bRgba\s*\{\s*(?![^}]*\.\.)[^}]*\}")
CSS_COLOR_RE = re.compile(r"(#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(\s*\d[^)]*\))")
RS_FONT_SIZE_RE = re.compile(r"\.text_size\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)")
RS_RADIUS_RE = re.compile(r"\.rounded\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)")
RS_CIRCLE_SIZE_RE = re.compile(r"\b(?:size|h|w)\(\s*px\(\s*(\d+(?:\.\d*)?)\s*\)\s*\)")
JSX_INLINE_STYLE_RE = re.compile(r"""\bstyle=\{\{([^}]+)\}\}""")
QSS_INLINE_RE = re.compile(r"""\.setStyleSheet\(\s*["']([^"']+)["']""")
JSX_FONT_PX_RE = re.compile(r"\bfontSize\s*:\s*['\"]?(\d+)")
JSX_RADIUS_PX_RE = re.compile(r"\bborderRadius\s*:\s*['\"]?(\d+)")
CSS_FONT_PX_RE = re.compile(r"\bfont-size\s*:\s*(\d+(?:\.\d*)?)\s*px")
CSS_RADIUS_PX_RE = re.compile(r"\bborder-radius\s*:\s*(\d+(?:\.\d*)?)\s*px")
CSS_VAR_DECL_RE = re.compile(r"^\s*--[\w-]+\s*:")
CSS_SCOPE_RE = re.compile(r"^\s*(?::root\b|@theme\b|\[data-theme\b)")
TW_COLOR_RE = re.compile(
    r"\b(?:bg|text|border|ring|fill|stroke|from|via|to|outline|shadow|accent|decoration)-\[(#[0-9a-fA-F]{3,8}|(?:rgba?|hsla?)\([^\]\n]+\))\]"
)
TW_RAW_COLOR_RE = re.compile(
    r"\b(?:bg|text|border|ring|from|via|to|fill|stroke|outline|shadow)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|"
    r"yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b"
)
TW_FONT_RE = re.compile(r"\btext-\[(\d+(?:\.\d+)?)(px|rem|em)\]")
TW_RADIUS_RE = re.compile(r"\brounded(?:-[trbl]{1,2})?-\[(\d+(?:\.\d+)?)(px|rem|em)\]")
THEME_STYLE_HINT_RE = re.compile(r"\b(?:visual::\w+|theme::\w+|\w+_radius|var\(--|tokens\.)")
URL_HASH_RE = re.compile(r"https?://\S*#")


def is_theme_file(rel):
    """Theme/token definition files hold color and metric literals on purpose."""
    if core.C.theme_paths and any(rel.startswith(p) for p in core.C.theme_paths):
        return True
    return DEFAULT_THEME_RE.search(rel) is not None


def _own_debloat_case(path, rel):
    return not core.SELFTEST and (path.resolve().is_relative_to(CASES) or core.own_cases(path, rel))


def _tracked_candidates(root=None, exts=SCAN_EXT):
    base = Path(root or core.REPO).resolve()
    import patterns as mod_patterns
    for rel in mod_patterns.tracked(base):
        path = (base / rel).resolve()
        suffix = path.suffix.lower()
        if suffix not in exts or _own_debloat_case(path, rel):
            continue
        if core.C.skip_dirs & set(Path(rel).parts) or core.is_test(rel):
            continue
        if not path.is_file() or path.stat().st_size > core.MAX_BYTES:
            continue
        yield rel, path, suffix


def _is_target_file(rel, suffix, source):
    if suffix in STYLE_EXT:
        return not is_theme_file(rel)
    return core.is_ui(rel, source)


def repo_ui_files(root=None):
    """All non-test, non-theme UI and stylesheet files under root."""
    out = []
    for rel, path, suffix in _tracked_candidates(root):
        source = path.read_text(encoding="utf-8", errors="replace")
        if _is_target_file(rel, suffix, source):
            out.append((rel, path, source))
    return out


def discover_project_tokens(root=None):
    """Detects palette, typography, radius tokens, and tooltips; prioritizes theme/style files first."""
    tokens = {
        "has_palette": bool(core.C.design.get("style_tokens")),
        "has_font_tokens": bool(core.C.design.get("style_tokens")),
        "has_radius_tokens": False,
        "uses_tooltips": bool(core.C.raw.get("tooltip_calls")),
    }
    candidates = list(_tracked_candidates(root))
    theme_first = [c for c in candidates if is_theme_file(c[0]) or c[2] in STYLE_EXT or "tailwind.config" in Path(c[0]).name.lower()]
    general = [c for c in candidates if c not in set(theme_first)]

    def inspect(rel, suffix, source):
        is_theme = is_theme_file(rel) or suffix in STYLE_EXT or "tailwind.config" in Path(rel).name.lower()
        if (re.search(r"\bPalette\b|\bcreateTheme\b|--bg\b|--surface\b|--[\w-]*(?:color|bg|surface|accent|card)\s*:|\bp\.(surface|card|text|primary)\b|\b(?:bg|c|b--)-(?:base|action-primary|muted-[1-5]|on-base|danger|warning|success)\b", source)
                or (is_theme and re.search(r"\bcolors\s*:", source))):
            tokens["has_palette"] = True
        if (re.search(r"\b(?:visual|typography)::(?:BODY|FOOTNOTE|CAPTION|EMPHASIS|HEADLINE|TITLE)\b|--[\w-]*(?:font|text)[-_]?size\b|typography\.|\btext-(?:xs|sm|base|lg|xl|2xl)\b|\bt-(?:heading-[1-6]|body|small|mini|action)\b", source)
                or (is_theme and re.search(r"\b(?:BODY|FOOTNOTE|CAPTION|EMPHASIS|HEADLINE|TITLE|fontSize)\b", source))):
            tokens["has_font_tokens"] = True
        if re.search(r"\b\w+_radius\b|--[\w-]*radius\b|\brounded\w*:\s*[\d.]+|card_radius|control_radius|\brounded-(?:sm|md|lg|xl|2xl)\b|\bbr[0-4]\b", source):
            tokens["has_radius_tokens"] = True
        if core.C.tooltip_call.search(source) or re.search(r"<Tooltip\b|data-tooltip|\btitle=", source):
            tokens["uses_tooltips"] = True

    for rel, path, suffix in theme_first:
        inspect(rel, suffix, path.read_text(encoding="utf-8", errors="replace"))
    checked = 0
    for rel, path, suffix in general:
        source = path.read_text(encoding="utf-8", errors="replace")
        if not core.is_ui(rel, source):
            continue
        inspect(rel, suffix, source)
        checked += 1
        if checked >= 50 and all(tokens.values()):
            break
    return tokens


def find_screens(query, root=None):
    """Matches screen files by path, symbol, or visible UI title and lists sibling screen files."""
    terms = [t for t in core.plain(query).split() if len(t) >= 2]
    if not terms:
        return []
    files = repo_ui_files(root)
    by_rel = {rel: path for rel, path, _ in files}
    scored = []
    for rel, path, source in files:
        norm_rel = core.plain(rel)
        score = sum(8 for t in terms if f" {t} " in f" {norm_rel} ") + sum(4 for t in terms if t in norm_rel)
        symbols = " ".join(re.findall(r"\b(?:struct|fn|class|function|const)\s+([A-Za-z_]\w*)", source))
        norm_sym = core.plain(symbols)
        score += sum(3 for t in terms if t in norm_sym)
        if score == 0:
            titles = " ".join(re.findall(r'"([^"\n]{3,80})"', source[:12000]))
            norm_titles = core.plain(titles)
            score += sum(2 for t in terms if t in norm_titles)
        if score > 0:
            if re.search(r"(^|/)(?:screens|views|pages|dialogs|modals)/", rel):
                score += 3
            elif re.search(r"(^|/)components/", rel):
                score += 2
            if "view" in Path(rel).stem:
                score += 1
            if re.search(r"<[A-Za-z]", source):
                score += 2
            if re.search(r"(^|/)(?:hooks|utils|typings|services|graphql)/", rel):
                score = max(1, score - 6)
            elif len(source.splitlines()) <= 15 and not re.search(r"<[A-Za-z]", source):
                score = max(1, score - 4)
            if path.suffix.lower() in STYLE_EXT:
                score = max(1, score - 2)
            siblings = sorted(r for r in by_rel if Path(r).parent == Path(rel).parent and r != rel)
            scored.append((score, rel, siblings[:8]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[:10]


def _container_indent(line):
    indent = len(line) - len(line.lstrip())
    stripped = line.lstrip()
    return max(0, indent - 4) if stripped.startswith(".") else indent


def _function_spans(lines, suffix, test_from):
    """Splits UI file lines into (start_line, name, end_line) spans keyed by render_owner."""
    limit = (test_from - 1) if (suffix == ".rs" and test_from) else len(lines)
    spans = []
    current = None
    for number in range(1, limit + 1):
        owner = copy_rules.render_owner(lines, number)
        if owner[0] == 0:
            continue
        if current is None or owner != current[:2]:
            if current is not None:
                spans.append((current[0], current[1], number - 1))
            current = (owner[0], owner[1])
    if current is not None:
        spans.append((current[0], current[1], limit))
    return spans


def _is_text_stack_line(lines, number, raw, suffix):
    if TEXT_STACK_CALL_RE.search(raw) and any(len(lit.split()) >= 4 for lit in core.literals(raw, suffix)):
        return True
    if raw.rstrip().endswith((".child(", ".child([")) and number < len(lines):
        nxt = lines[number]
        if ("div()" in nxt or "hint_text" in nxt or ".text_size(" in nxt) and any(
            len(lit.split()) >= 4 for lit in core.literals(nxt, suffix)
        ):
            return True
    return False


NONE_ARG_RE = re.compile(
    r"^(?:None|null|undefined)$|^(?:hint|help|tooltip)\s*[:=]\s*(?:None|null|undefined)$"
)
JSX_OPEN_TAG_RE = re.compile(
    r"<(?:[A-Z]\w*|(?:div|section|aside|article|main|header|footer|nav|ul|ol|li|form|table|p|span)\b)(?=[\s/>]|$)"
)
JSX_CLOSE_TAG_RE = re.compile(r"</[A-Za-z]\w*>|/>")


def _has_top_level_none(lines, number, open_col, suffix):
    """Checks across wrapped arguments whether this call directly passes None/null/undefined."""
    span_text, _ = copy_rules.call_span(lines, number, open_col, limit=25)
    if not re.search(r"\b(?:None|null|undefined)\b", span_text):
        return False
    clean_lines = []
    for ln in span_text.splitlines():
        if core.comment_of(ln, suffix) is not None:
            continue
        stripped_lit = core.strip_literals(ln)
        stripped_lit = re.sub(r"#.*$" if suffix == ".py" else r"//.*$", "", stripped_lit)
        clean_lines.append(stripped_lit)
    if not clean_lines:
        return False
    return any(NONE_ARG_RE.search(arg.strip()) for arg in core.split_top("\n".join(clean_lines)))


def _aggregate_branches(entries, widths=1):
    """Combines counts across mutually exclusive (group_id, arm) paths via max()."""
    base = [0] * widths if widths > 1 else 0
    by_group = defaultdict(lambda: defaultdict(list))
    for path, val in entries:
        if not path:
            if widths > 1:
                for i in range(widths):
                    base[i] += val[i]
            else:
                base += val
        else:
            gid, arm = path[0]
            by_group[gid][arm].append((path[1:], val))
    for arms in by_group.values():
        arm_totals = [_aggregate_branches(arm_entries, widths) for arm_entries in arms.values()]
        if widths > 1:
            for i in range(widths):
                base[i] += max((t[i] for t in arm_totals), default=0)
        else:
            base += max(arm_totals, default=0)
    return base


def _pop_closed_branches(branch_stack, confirmed, indent, stripped, suffix):
    """Pops/advances branch_stack frames ([indent, gid, arm, kind, has_else, has_return]) for current line."""
    for idx in range(len(branch_stack) - 1, -1, -1):
        frame = branch_stack[idx]
        if (frame[3] == "match" and indent <= frame[0] + 4 and "=>" in stripped
                and not stripped.startswith(".") and not re.match(r"^(?:\}\s*)?(?:if|else)\b", stripped)):
            del branch_stack[idx + 1:]
            frame[2] += 1
            confirmed.add(frame[1])
            return True
    while branch_stack and indent < branch_stack[-1][0]:
        branch_stack.pop()
    while branch_stack and indent == branch_stack[-1][0]:
        top = branch_stack[-1]
        if top[3] == "ret_rest":
            break
        if top[3] == "if" and (re.match(r"^(?:\}\s*)?(?:else\b|elif\b)", stripped)
                               or (suffix in core.JSX and re.match(r"^\)\s*:\s*\(", stripped))):
            top[2] += 1
            top[4] = True
            confirmed.add(top[1])
            return True
        if stripped.startswith("}") or (suffix == ".py" and not re.match(r"^(?:elif|else|match)\b", stripped)) or (
            suffix in core.JSX and stripped.startswith(")}")
        ):
            popped = branch_stack.pop()
            if popped[3] == "if" and popped[5] and not popped[4]:
                confirmed.add(popped[1])
                branch_stack.append([popped[0], popped[1], 1, "ret_rest", True, False])
            continue
        break
    return False


def _blank_literals(line):
    """Replaces string literal contents with same-length spaces to preserve column offsets."""
    line = re.sub(r'"(?:[^"\\\n]|\\.)*"', lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', line)
    line = re.sub(r"(?<![\w&])'(?:[^'\\\n]|\\.){3,}'", lambda m: "'" + " " * (len(m.group(0)) - 2) + "'", line)
    return re.sub(r"\x60[^\x60\n]*\x60", lambda m: "`" + " " * (len(m.group(0)) - 2) + "`", line)


def _track_line_boxes(raw, code, suffix, delim_state, stack, in_loop, card_lines, raw_items, bp, number, max_depth, max_depth_line):
    """Tracks delimiter/JSX nesting alongside indent so single-line chains and wrappers count accurately."""
    stripped = raw.lstrip()
    c_indent = _container_indent(raw)
    indent = len(raw) - len(stripped)
    cur_n = delim_state[0] + delim_state[1]
    while stack and (
        cur_n < stack[-1][0][0]
        or (cur_n == stack[-1][0][0] and (indent < stack[-1][0][1] or (indent == stack[-1][0][1] and not stripped.startswith("."))))
    ):
        stack.pop()

    box_text = raw if suffix in core.JSX else code
    is_micro = MICRO_RE.search(code) is not None
    box_matches = [] if is_micro else list(STYLED_BOX_RE.finditer(box_text))
    tokens = []  # (pos, kind, match_obj)
    for m in box_matches:
        tokens.append((m.start(), "box", m))
    for pos, ch in enumerate(code):
        if ch in "([":
            tokens.append((pos, "open", None))
        elif ch in ")]":
            tokens.append((pos, "close", None))
    if suffix in core.JSX:
        for m in JSX_OPEN_TAG_RE.finditer(code):
            tokens.append((m.start(), "jsx_open", None))
        for m in JSX_CLOSE_TAG_RE.finditer(code):
            tokens.append((m.start(), "jsx_close", None))
    order = {"box": 0, "jsx_open": 1, "open": 2, "close": 3, "jsx_close": 4}
    tokens.sort(key=lambda t: (t[0], order[t[1]]))

    for pos, kind, m in tokens:
        if kind == "open":
            delim_state[0] += 1
        elif kind == "close":
            delim_state[0] = max(0, delim_state[0] - 1)
            cur_n = delim_state[0] + delim_state[1]
            while stack and cur_n < stack[-1][0][0]:
                stack.pop()
        elif kind == "jsx_open":
            delim_state[1] += 1
        elif kind == "jsx_close":
            delim_state[1] = max(0, delim_state[1] - 1)
            cur_n = delim_state[0] + delim_state[1]
            while stack and (cur_n < stack[-1][0][0] or (stack[-1][4] and cur_n <= stack[-1][0][0])):
                stack.pop()
        elif kind == "box":
            cur_n = delim_state[0] + delim_state[1]
            box_key = (cur_n, c_indent)
            is_jsx = m.group(0).startswith("<")
            while stack and stack[-1][0] > box_key:
                stack.pop()
            if not stack or stack[-1][0] < box_key:
                stack.append([box_key, number, set(), number in card_lines, is_jsx])
                if len(stack) > max_depth:
                    max_depth, max_depth_line = len(stack), number
            if stack:
                tok = m.group(0)
                if ".bg(" in tok or "bg-" in raw:
                    stack[-1][2].add("bg")
                if re.search(r"\.(?:border_[1-9]\w*|border_color)\(|\bborder(?!-0\b)", tok) or re.search(r"\bborder-(?!0\b)", box_text):
                    stack[-1][2].add("border")
                if not in_loop and not stack[-1][3] and {"bg", "border"} <= stack[-1][2]:
                    raw_items["cards"].append((bp, stack[-1][1]))
                    card_lines.add(stack[-1][1])
                    stack[-1][3] = True
    return max_depth, max_depth_line


def _analyze_fn(lines, suffix, start, name, end, look, skip, known_callees):
    cfg = core.C.design
    stack = []  # [[(nesting, indent), line_number, flags, card_counted, is_jsx]]
    branch_stack = []  # [[indent, group_id, arm_idx, kind, has_else, has_return]]
    confirmed = set()
    delim_state = [0, 0]  # [paren_bracket_depth, jsx_depth]
    next_group = 1
    max_depth, max_depth_line = 0, start
    raw_items = defaultdict(list)  # field -> [(branch_path, line)]
    spacers, gap_calls = [], 0
    header_runs, current_run, raw_calls = [], [], []
    inline_chains = defaultdict(list)
    fixed_width_triggers = []
    loop_indent = None
    loop_bp = ()
    loop_primary_seen = False
    loop_filter_chip_seen = False
    card_lines = set()

    for number in range(start, end + 1):
        if number in skip:
            continue
        raw = lines[number - 1]
        if core.comment_of(raw, suffix) is not None or not raw.strip():
            continue
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)

        if loop_indent is not None and indent <= loop_indent and stripped not in ("{", "}", ");", ")"):
            loop_indent = None
            loop_primary_seen = False
            loop_filter_chip_seen = False
        if loop_indent is None and LOOP_START_RE.search(raw):
            loop_indent = indent
            loop_bp = tuple((f[1], f[2]) for f in branch_stack)
            loop_primary_seen = False
            loop_filter_chip_seen = False
        in_loop = loop_indent is not None and indent > loop_indent

        continued = _pop_closed_branches(branch_stack, confirmed, indent, stripped, suffix)
        code = _blank_literals(raw)
        if branch_stack and branch_stack[-1][3] == "if" and indent <= branch_stack[-1][0] + 4 and re.search(r"\breturn\b", code):
            branch_stack[-1][5] = True

        single_ret_if = (not continued and re.search(r"(?:^|\s)if\b.*\{.*\breturn\b.*\}", code)
                         and not re.search(r"\belse\b", code))
        opened_branch = None
        if single_ret_if:
            gid = next_group
            next_group += 1
            confirmed.add(gid)
            opened_branch = ("single_ret", gid)
        elif re.search(r"\bmatch\b.*\{\s*$", code) or (suffix == ".py" and re.match(r"^match\b.*:\s*$", stripped)):
            opened_branch = ("match", next_group)
            confirmed.add(next_group)
            next_group += 1
        elif not continued and (
            re.search(r"(?:^|[\s=(])if\b[^{};]*\{\s*$", code)
            or (suffix == ".py" and re.match(r"^if\b.*:\s*$", stripped))
            or (suffix in core.JSX and re.search(r"\{\s*[^?{}]+\?\s*\(\s*$", code))
        ):
            opened_branch = ("if", next_group)
            if suffix in core.JSX and "?" in code:
                confirmed.add(next_group)
            next_group += 1

        line_bp = tuple((f[1], f[2]) for f in branch_stack)
        if opened_branch and opened_branch[0] == "single_ret":
            eval_bp = line_bp + ((opened_branch[1], 0),)
        else:
            eval_bp = line_bp

        if ".gap(" in raw or re.search(r"\bgap-\d+", raw):
            gap_calls += 1
        if in_loop:
            if not loop_primary_seen and PRIMARY_ACCENT_RE.search(raw):
                raw_items["primary_accents"].append((loop_bp, number))
                loop_primary_seen = True
            if not loop_filter_chip_seen and FILTER_CHIP_RE.search(raw):
                raw_items["filter_chip_strips"].append((loop_bp, number))
                loop_filter_chip_seen = True
        else:
            if DIVIDER_RE.search(raw):
                raw_items["dividers"].append((eval_bp, number))
            if BADGE_RE.search(raw):
                raw_items["badges"].append((eval_bp, number))
            if ICON_RE.search(raw):
                raw_items["icons"].append((eval_bp, number))
            if INFO_HINT_RE.search(raw):
                raw_items["info_hints"].append((eval_bp, number))
            if PRIMARY_ACCENT_RE.search(raw):
                raw_items["primary_accents"].append((eval_bp, number))
            if FILTER_TRIGGER_RE.search(raw):
                raw_items["filter_triggers"].append((eval_bp, number))
                window_lines = "\n".join(lines[number - 1:min(end, number + 8)])
                wm = FIXED_TRIGGER_WIDTH_RE.search(window_lines)
                if wm:
                    width = int(float(wm.group(1) or wm.group(2) or wm.group(3)))
                    if width >= 140:
                        fixed_width_triggers.append((number, width))
            spacer_m = SPACER_RE.search(raw)
            if spacer_m and float(spacer_m.group("px") or 20) >= 16:
                spacers.append(number)
            if CARD_CALL_RE.search(raw):
                raw_items["cards"].append((eval_bp, number))
                card_lines.add(number)

        # Track calls to other UI helpers (local or sibling module) with nesting depth & branch path.
        call_depth = len(stack)
        for cm in re.finditer(r"(?:\b([A-Za-z_]\w*)::|\bself\.)?\b([A-Za-z_]\w*)\s*\(|<([A-Z]\w*)\b", code):
            qual = cm.group(1) or ""
            target = cm.group(2) or cm.group(3)
            if target in known_callees and target != name:
                if cm.group(2):
                    none_hint = _has_top_level_none(lines, number, cm.end(), suffix)
                else:
                    none_hint = re.search(r"\b(?:null|undefined|None)\b|\bhint=\{?(?:null|undefined|false)\b", code) is not None
                raw_calls.append((qual, target, call_depth, number, eval_bp, none_hint))

        chain_m = re.findall(r"\.(rounded\w*|bg|border\w*|shadow\w*|px?\d?|py?\d?)\(", raw)
        if len(chain_m) >= 3:
            inline_chains[tuple(chain_m[:4])].append(number)

        max_depth, max_depth_line = _track_line_boxes(
            raw, code, suffix, delim_state, stack, in_loop, card_lines, raw_items, eval_bp,
            number, max_depth, max_depth_line,
        )

        if opened_branch:
            kind, gid = opened_branch
            if kind == "single_ret":
                branch_stack.append([indent, gid, 1, "ret_rest", True, False])
            else:
                branch_stack.append([indent, gid, 0, kind, kind != "if" or (suffix in core.JSX and "?" in code), False])

        if _is_text_stack_line(lines, number, raw, suffix):
            current_run.append(number)
        elif stripped not in ("{", "}", ");", ")"):
            if len(current_run) > cfg["max_header_stack"]:
                header_runs.append(list(current_run))
            current_run.clear()
    if len(current_run) > cfg["max_header_stack"]:
        header_runs.append(current_run)

    clean_bp = lambda bp: tuple(pair for pair in bp if pair[0] in confirmed)
    count_keys = (
        "cards", "dividers", "badges", "icons", "info_hints",
        "primary_accents", "filter_triggers", "filter_chip_strips",
    )
    items = {k: [n for _, n in raw_items[k]] for k in count_keys}
    counts = {k: _aggregate_branches([(clean_bp(bp), 1) for bp, _ in raw_items[k]]) for k in count_keys}
    calls = [(qual, target, d, ln, clean_bp(bp), nh) for qual, target, d, ln, bp, nh in raw_calls]

    return {
        "start": start, "end": end, "name": name,
        "max_depth": max_depth, "max_depth_line": max_depth_line,
        **items, "counts": counts, "spacers": spacers,
        "fixed_width_triggers": fixed_width_triggers,
        "gap_calls": gap_calls, "header_runs": header_runs,
        "inline_chains": inline_chains, "calls": calls,
        "touched": any(look(n) for n in range(start, end + 1)),
    }


def _emit_fn_findings(rel, stats, uses_tooltips, look, out):
    cfg = core.C.design
    name = stats["name"]
    if not stats["touched"]:
        return
    if stats["max_depth"] > cfg["max_container_depth"]:
        out.append(core.Finding(
            rel, stats["max_depth_line"], "design",
            f"{name}: overloaded hierarchy with {stats['max_depth']} nested styled boxes (limit {cfg['max_container_depth']})",
            "flatten wrappers or remove inner card borders and backgrounds",
        ))
    count_specs = [
        ("cards", "max_cards", "boxed cards/panels in one view",
         "group sections with whitespace and typography instead of wrapping every block in a box"),
        ("dividers", "max_dividers", "dividers/separators in one view",
         "replace horizontal rules with spacing between groups"),
        ("badges", "max_badges", "badges/chips cluttering one view",
         "keep only status chips that drive user action"),
        ("icons", "max_icons", "icons in one view",
         "drop decorative icons where labels already explain the element"),
        ("info_hints", "max_info_hints", "info_hint badges in one view",
         "make labels self-explanatory and keep help icons only for non-obvious behavior"),
        ("primary_accents", "max_primary_accents", "competing primary accents in one view",
         "keep solid primary accent for the main CTA only; use neutral/surface fill for selected filters and toggles"),
        ("filter_triggers", "max_exposed_filters", "exposed filter/sort dropdowns in one view",
         "consolidate secondary filters/sorting into a single Filters menu and show active filter chips only when set"),
    ]
    for field, limit_key, desc, fix in count_specs:
        items = stats[field]
        eff = stats["counts"][field]
        limit = cfg[limit_key]
        if eff > limit and any(look(n) for n in items):
            out.append(core.Finding(rel, items[0], "design", f"{name}: {eff} {desc} (limit {limit})", fix))
    if stats["counts"]["filter_triggers"] >= 1 and stats["counts"]["filter_chip_strips"] >= 1:
        hit_lines = stats["filter_triggers"] + stats["filter_chip_strips"]
        if any(look(n) for n in hit_lines):
            out.append(core.Finding(
                rel, min(hit_lines), "design",
                f"{name}: stacked filter controls ({stats['counts']['filter_triggers']} trigger(s) + exposed filter-chip strip)",
                "consolidate secondary filters/sorting into a single Filters menu and show active filter chips only when set",
            ))
    for line_no, width in stats["fixed_width_triggers"]:
        if look(line_no):
            out.append(core.Finding(
                rel, line_no, "design",
                f"{name}: fixed-width filter/sort trigger ({width}px) crowds toolbar",
                "size trigger to content or consolidate secondary filters into a single Filters menu",
            ))
    if stats["gap_calls"] > 0 and len(stats["spacers"]) >= 2 and any(look(n) for n in stats["spacers"]):
        out.append(core.Finding(
            rel, stats["spacers"][0], "design",
            f"{name}: {len(stats['spacers'])} explicit spacer boxes inside gap-spaced layout",
            "adjust container gap/padding tokens instead of inserting blank spacer blocks",
        ))
    tip_hint = "or move secondary notes into a tooltip" if uses_tooltips else "or shorten to one line"
    for run in stats["header_runs"]:
        if any(look(n) for n in run):
            out.append(core.Finding(
                rel, run[0], "design",
                f"{name}: {len(run)} stacked text blocks competing in sequence (lines {', '.join(map(str, run[:4]))})",
                f"keep 1 title + essential context {tip_hint}",
            ))
    for sig, locs in stats["inline_chains"].items():
        if len(locs) >= 3 and any(look(n) for n in locs):
            out.append(core.Finding(
                rel, locs[1], "design",
                f"{name}: repeated inline style pattern ({'.'.join(sig)}) {len(locs)}x on screen",
                "extract to a shared theme-backed helper or simplify repeated cards",
            ))


def _file_symbols(source):
    impls = set(re.findall(r"\bimpl(?:<[^>{]+>)?\s+([A-Z]\w*)", source))
    imports = set(re.findall(r"(?:\b(?:use|mod)\s+[\w:]+::(\w+)|\bmod\s+(\w+)\s*;|import\s+\{([^}]+)\}|import\s+(\w+)\s+from)", source))
    names = set()
    for grp in imports:
        for part in grp:
            for item in part.split(","):
                tok = item.strip().split(" as ")[0].strip()
                if tok:
                    names.add(tok)
    return impls, names


def _peer_record(ppath, file_cache):
    """Reads and caches UI metadata for peer discovery without re-reading across siblings."""
    key = str(ppath.resolve())
    if key in file_cache:
        return file_cache[key]
    if not ppath.is_file() or ppath.suffix.lower() not in core.CODE:
        file_cache[key] = None
        return None
    rel = core.relative(ppath)
    if _own_debloat_case(ppath, rel) or core.C.skip_dirs & set(Path(rel).parts) or core.is_test(rel) or is_theme_file(rel):
        file_cache[key] = None
        return None
    if ppath.stat().st_size > core.MAX_BYTES:
        file_cache[key] = None
        return None
    psource = ppath.read_text(encoding="utf-8", errors="replace")
    if not core.is_ui(rel, psource):
        file_cache[key] = None
        return None
    psuf = ppath.suffix.lower()
    praw = psource.splitlines()
    plines = copy_rules.jsx_lines(praw) if psuf in core.JSX else praw
    ptest = core.rust_test_start(praw) if psuf == ".rs" else None
    pskip = core.ignored_lines(praw)
    fn_names = {name for _, name, _ in _function_spans(plines, psuf, ptest)}
    rec = (rel, ppath, plines, psource, psuf, ptest, pskip, fn_names)
    file_cache[key] = rec
    return rec


def _screen_peer_paths(path, file_cache):
    """UI sibling files belonging to the same screen module folder (up/down 1 UI subdir level)."""
    parent = path.parent
    dirs = [parent]
    if parent.name.lower() in SCREEN_SUBDIRS and parent.parent.is_dir():
        dirs.append(parent.parent)
        dirs += [d for d in sorted(parent.parent.iterdir()) if d.is_dir() and d.name.lower() in SCREEN_SUBDIRS]
    elif parent.name.lower() not in GENERIC_UI_DIRS:
        dirs += [d for d in sorted(parent.iterdir()) if d.is_dir() and d.name.lower() in SCREEN_SUBDIRS]
    seen, out = {path.resolve()}, []
    for folder in dict.fromkeys(dirs):
        if not folder.is_dir():
            continue
        for candidate in sorted(folder.iterdir()):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rec = _peer_record(candidate, file_cache)
            if rec is not None:
                out.append(rec)
            if len(out) >= 20:
                return out
    return out


def _resolve_callee(cur_rel, cur_meta, qual, target, metas_by_rel):
    """Resolves a callee within current file or linked screen-module peers."""
    if target in cur_meta["by_name"]:
        return (cur_rel, target)
    if qual:
        for rel, meta in metas_by_rel.items():
            if rel != cur_rel and (Path(rel).stem == qual or Path(rel).parent.name == qual) and target in meta["by_name"]:
                return (rel, target)
    candidates = [(rel, meta) for rel, meta in metas_by_rel.items() if rel != cur_rel and target in meta["by_name"]]
    if not candidates:
        return None
    shared_impl = [(rel, m) for rel, m in candidates if cur_meta["impls"] & m["impls"]]
    if len(shared_impl) == 1:
        return (shared_impl[0][0], target)
    imported = [(rel, m) for rel, m in candidates if target in cur_meta["imports"] or Path(rel).stem in cur_meta["imports"]]
    if len(imported) == 1:
        return (imported[0][0], target)
    if len(candidates) == 1 and Path(cur_rel).parent.name.lower() not in GENERIC_UI_DIRS:
        return (candidates[0][0], target)
    return None


def _analyze_unit(rel, path, lines, source, suffix, test_from, look, skip, peer_metas=None):
    """Parses spans and analyses every function in one UI file."""
    spans = _function_spans(lines, suffix, test_from)
    local_names = {name for _, name, _ in spans}
    known_callees = set(local_names)
    if peer_metas:
        for meta in peer_metas.values():
            known_callees |= set(meta["fn_names"])
    by_name = {}
    for start, name, end in spans:
        by_name[name] = _analyze_fn(lines, suffix, start, name, end, look, skip, known_callees)
    impls, imports = _file_symbols(source)
    return {"rel": rel, "path": path, "by_name": by_name, "fn_names": local_names, "impls": impls, "imports": imports}


def _audit_file_design(rel, path, lines, source, suffix, test_from, look, skip, uses_tooltips, out, cache=None):
    cfg = core.C.design
    store = {} if cache is None else cache
    metas = store.setdefault("units", {})
    file_cache = store.setdefault("files", {})
    peers = _screen_peer_paths(path, file_cache)
    all_fn_names = {name for _, name, _ in _function_spans(lines, suffix, test_from)}
    for _, _, _, _, _, _, _, p_names in peers:
        all_fn_names |= p_names

    peer_metas = {}
    for prel, ppath, plines, psource, psuf, ptest, pskip, _ in peers:
        pkey = str(ppath.resolve())
        unit = metas.get(pkey)
        if unit is None or not (all_fn_names <= unit["known_callees"]):
            unit = _analyze_unit(
                prel, ppath, plines, psource, psuf, ptest, lambda n: False, pskip,
                peer_metas={"_": {"fn_names": all_fn_names}},
            )
            unit["known_callees"] = set(all_fn_names)
            metas[pkey] = unit
        peer_metas[prel] = unit

    cur_meta = _analyze_unit(
        rel, path, lines, source, suffix, test_from, look, skip,
        peer_metas={"_": {"fn_names": all_fn_names}},
    )
    metas_by_rel = {rel: cur_meta, **peer_metas}
    for stats in cur_meta["by_name"].values():
        _emit_fn_findings(rel, stats, uses_tooltips, look, out)

    called_anywhere = set()
    for m_rel, m_data in metas_by_rel.items():
        for s in m_data["by_name"].values():
            for qual, target, *_ in s["calls"]:
                resolved = _resolve_callee(m_rel, m_data, qual, target, metas_by_rel)
                if resolved:
                    called_anywhere.add(resolved)

    count_keys = (
        "cards", "dividers", "badges", "icons", "info_hints",
        "primary_accents", "filter_triggers", "filter_chip_strips",
    )
    hint_idx = count_keys.index("info_hints")
    primary_idx = count_keys.index("primary_accents")
    trigger_idx = count_keys.index("filter_triggers")
    strip_idx = count_keys.index("filter_chip_strips")
    memo = {}

    def compose(node_key, stack_path=()):
        if not stack_path and node_key in memo:
            return memo[node_key]
        node_rel, fn_name = node_key
        m_data = metas_by_rel.get(node_rel)
        s = m_data["by_name"].get(fn_name) if m_data else None
        if not s or node_key in stack_path or len(stack_path) >= 4:
            return 0, [fn_name], [0] * len(count_keys), False, False
        max_d = s["max_depth"]
        best_chain = [fn_name]
        base_totals = [s["counts"][k] for k in count_keys]
        call_entries = []
        any_touched = s["touched"]
        local_deep = s["max_depth"] > cfg["max_container_depth"]
        child_deep = False
        for qual, target, depth_at_call, _, bp, none_hint in s["calls"]:
            callee_key = _resolve_callee(node_rel, m_data, qual, target, metas_by_rel)
            if not callee_key:
                continue
            cd, cchain, ctotals, ctouched, _ = compose(callee_key, stack_path + (node_key,))
            any_touched = any_touched or ctouched
            c_stats = metas_by_rel[callee_key[0]]["by_name"].get(callee_key[1], {})
            if c_stats.get("max_depth", 0) > cfg["max_container_depth"]:
                child_deep = True
            cand = depth_at_call + cd
            if cd > 0 and cand > max_d:
                label = callee_key[1] if callee_key[0] == node_rel else f"{Path(callee_key[0]).stem}::{callee_key[1]}"
                max_d, best_chain = cand, [fn_name] + ([label] + cchain[1:] if cchain else [])
            adj = list(ctotals)
            if none_hint:
                adj[hint_idx] = 0
            if MODAL_CALLEE_RE.search(callee_key[1]):
                adj[primary_idx] = 0
                adj[trigger_idx] = 0
                adj[strip_idx] = 0
            call_entries.append((bp, adj))
        extra = _aggregate_branches(call_entries, widths=len(count_keys))
        seq_totals = [base_totals[i] + extra[i] for i in range(len(count_keys))]
        res = (max_d, best_chain, seq_totals, any_touched, (any_touched and not local_deep and not child_deep))
        if not stack_path:
            memo[node_key] = res
        return res

    composed_specs = [
        ("cards", "max_cards", 0, "boxed cards/panels across composed view",
         "group sections with whitespace and typography instead of wrapping every block in a card"),
        ("info_hints", "max_info_hints", 0, "info_hint badges across composed view",
         "make labels self-explanatory and cut redundant help icons"),
        ("primary_accents", "max_primary_accents", 1, "competing primary accents across composed view",
         "keep solid primary accent for the main CTA only; use neutral/surface fill for selected filters and toggles"),
        ("filter_triggers", "max_exposed_filters", 0, "exposed filter/sort dropdowns across composed view",
         "consolidate secondary filters/sorting into a single Filters menu and show active filter chips only when set"),
    ]
    for root_name, root in cur_meta["by_name"].items():
        if not root["calls"]:
            continue
        is_entry = (rel, root_name) not in called_anywhere or re.match(
            r"^(render\w*|build\w*|view\w*|[A-Z]\w*)$", root_name
        )
        if not is_entry:
            continue
        max_d, best_chain, totals, any_touched, depth_clean = compose((rel, root_name))
        if not any_touched and not root["touched"]:
            continue
        if len(best_chain) >= 2 and depth_clean and max_d > cfg["max_container_depth"]:
            out.append(core.Finding(
                rel, root["start"], "design",
                f"{' -> '.join(best_chain)}: composed hierarchy of {max_d} nested styled boxes (limit {cfg['max_container_depth']})",
                "flatten helper wrappers or remove inner card borders",
            ))
        for key, limit_key, extra_allowance, desc, fix in composed_specs:
            pos = count_keys.index(key)
            limit = cfg[limit_key] + extra_allowance
            if totals[pos] > limit and root["counts"][key] <= limit:
                out.append(core.Finding(
                    rel, root["start"], "design",
                    f"{root_name} (composed): {totals[pos]} {desc} (limit {limit})",
                    fix,
                ))
        local_stacked = root["counts"]["filter_triggers"] >= 1 and root["counts"]["filter_chip_strips"] >= 1
        if totals[trigger_idx] >= 1 and totals[strip_idx] >= 1 and not local_stacked:
            out.append(core.Finding(
                rel, root["start"], "design",
                f"{root_name} (composed): stacked filter controls across composed view ({totals[trigger_idx]} trigger(s) + exposed filter-chip strip)",
                "consolidate secondary filters/sorting into a single Filters menu and show active filter chips only when set",
            ))


def _check_css_line(rel, number, raw, tokens, out):
    """Checks CSS/QSS rule declarations for literal colors, font-size, and border-radius."""
    for decl in raw.split(";"):
        if "var(--" in decl:
            continue
        cm = CSS_COLOR_RE.search(URL_HASH_RE.sub("", decl))
        if cm:
            val = cm.group(1).lower()
            is_clear = val in ("#0000", "#00000000") or re.fullmatch(
                r"rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0(?:\.0+)?\s*\)", val
            ) is not None
            if not is_clear:
                out.append(core.Finding(rel, number, "style", f'hardcoded CSS color literal "{cm.group(1)[:32]}"', CSS_FIX))
        if tokens["has_font_tokens"]:
            fm = CSS_FONT_PX_RE.search(decl)
            if fm and float(fm.group(1)) >= 10:
                out.append(core.Finding(rel, number, "style", f"hardcoded CSS font-size {fm.group(1)}px", CSS_FIX))
        if tokens["has_radius_tokens"]:
            rm = CSS_RADIUS_PX_RE.search(decl)
            if rm and 3. < float(rm.group(1)) < 999.:
                out.append(core.Finding(rel, number, "style", f"hardcoded CSS border-radius {rm.group(1)}px", CSS_FIX))


def _style_checks(rel, lines, suffix, look, skip, test_from, tokens, out):
    if is_theme_file(rel):
        return
    limit = (test_from - 1) if (suffix == ".rs" and test_from) else len(lines)
    in_block_comment = False
    theme_scope_depth = 0
    for number in range(1, limit + 1):
        raw = lines[number - 1]
        if suffix in STYLE_EXT:
            stripped = raw.strip()
            if in_block_comment:
                if "*/" in stripped:
                    in_block_comment = False
                continue
            if stripped.startswith("/*"):
                if "*/" not in stripped[2:]:
                    in_block_comment = True
                continue
            if CSS_SCOPE_RE.match(stripped) and "{" in stripped:
                theme_scope_depth += stripped.count("{") - stripped.count("}")
                continue
            if theme_scope_depth > 0:
                theme_scope_depth = max(0, theme_scope_depth + stripped.count("{") - stripped.count("}"))
                continue
        if number in skip or not look(number):
            continue
        if core.comment_of(raw, suffix) is not None or core.LOG_CONTEXT.search(raw):
            continue

        if suffix == ".rs":
            m = RS_HEX_COLOR_RE.search(raw)
            if m:
                val = m.group(2).strip().replace("_", "")
                is_clear = bool(re.fullmatch(r"0x(?:[0-9a-fA-F]{6}00|0{6,8})", val))
                if not is_clear:
                    out.append(core.Finding(
                        rel, number, "style",
                        f'hardcoded color literal "{m.group(0)[:36]}"',
                        "use project Palette / theme color variable",
                    ))
            elif RS_RGBA_LIT_RE.search(raw):
                out.append(core.Finding(
                    rel, number, "style",
                    "hardcoded Rgba struct literal without theme palette spread",
                    "derive from theme Palette (e.g. Rgba { a: ..., ..p.surface })",
                ))
            if tokens["has_font_tokens"]:
                fm = RS_FONT_SIZE_RE.search(raw)
                if fm and float(fm.group(1)) >= 10:
                    out.append(core.Finding(
                        rel, number, "style",
                        f"hardcoded text_size px({fm.group(1)})",
                        "use project typography token (e.g. visual::BODY / FOOTNOTE / HEADLINE)",
                    ))
            if tokens["has_radius_tokens"]:
                rm = RS_RADIUS_RE.search(raw)
                if rm:
                    radius = float(rm.group(1))
                    nearby = "\n".join(lines[max(0, number - 3):min(limit, number + 2)])
                    circular = any(abs(float(sz) - 2. * radius) <= 1. for sz in RS_CIRCLE_SIZE_RE.findall(nearby))
                    if radius > 3. and not circular:
                        out.append(core.Finding(
                            rel, number, "style",
                            f"hardcoded corner radius px({rm.group(1)})",
                            "use project radius token (e.g. p.control_radius / p.card_radius)",
                        ))
        elif suffix in core.JS:
            tw_c = TW_COLOR_RE.search(raw)
            if tw_c and "var(--" not in tw_c.group(0):
                out.append(core.Finding(
                    rel, number, "style",
                    f'hardcoded Tailwind arbitrary color "{tw_c.group(0)[:36]}"',
                    CSS_FIX,
                ))
            tw_raw = TW_RAW_COLOR_RE.search(raw) if tokens["has_palette"] and not tw_c else None
            if tw_raw:  # the project names its colours; a stock palette shade bypasses them
                out.append(core.Finding(
                    rel, number, "style",
                    f'stock Tailwind color "{tw_raw.group(0)}" in a themed project',
                    CSS_FIX,
                ))
            tw_f = TW_FONT_RE.search(raw) if tokens["has_font_tokens"] else None
            if tw_f and (tw_f.group(2) != "px" or float(tw_f.group(1)) >= 10):
                out.append(core.Finding(
                    rel, number, "style",
                    f'hardcoded Tailwind font-size "{tw_f.group(0)}"',
                    CSS_FIX,
                ))
            tw_r = TW_RADIUS_RE.search(raw) if tokens["has_radius_tokens"] else None
            if tw_r and (tw_r.group(2) != "px" or 3. < float(tw_r.group(1)) < 999.):
                out.append(core.Finding(
                    rel, number, "style",
                    f'hardcoded Tailwind radius "{tw_r.group(0)}"',
                    CSS_FIX,
                ))
            sm = JSX_INLINE_STYLE_RE.search(raw)
            if sm:
                body = sm.group(1)
                cm = CSS_COLOR_RE.search(URL_HASH_RE.sub("", body))
                if cm and "var(--" not in body:
                    out.append(core.Finding(
                        rel, number, "style",
                        f'hardcoded inline CSS color "{cm.group(1)[:32]}"',
                        CSS_FIX,
                    ))
                if tokens["has_font_tokens"] and JSX_FONT_PX_RE.search(body) and "var(--" not in body:
                    out.append(core.Finding(
                        rel, number, "style",
                        "hardcoded inline fontSize in JSX style",
                        CSS_FIX,
                    ))
                if tokens["has_radius_tokens"] and JSX_RADIUS_PX_RE.search(body) and "var(--" not in body:
                    out.append(core.Finding(
                        rel, number, "style",
                        "hardcoded inline borderRadius in JSX style",
                        CSS_FIX,
                    ))
            elif not THEME_STYLE_HINT_RE.search(raw) and "var(--" not in raw:
                cm = CSS_COLOR_RE.search(URL_HASH_RE.sub("", raw))
                if cm and ("style" in raw or "color" in raw.lower() or "background" in raw.lower()):
                    out.append(core.Finding(
                        rel, number, "style",
                        f'hardcoded color literal "{cm.group(1)[:32]}"',
                        CSS_FIX,
                    ))
        elif suffix in STYLE_EXT:
            if not CSS_VAR_DECL_RE.match(raw):
                _check_css_line(rel, number, raw, tokens, out)
        elif suffix == ".py":
            qm = QSS_INLINE_RE.search(raw)
            if qm:
                body = qm.group(1)
                cm = CSS_COLOR_RE.search(body)
                if cm:
                    out.append(core.Finding(
                        rel, number, "style",
                        f'hardcoded inline QSS color "{cm.group(1)[:32]}"',
                        QSS_FIX,
                    ))
                if CSS_FONT_PX_RE.search(body) or CSS_RADIUS_PX_RE.search(body):
                    out.append(core.Finding(
                        rel, number, "style",
                        "hardcoded inline QSS size/radius",
                        QSS_FIX,
                    ))


def scan_file(path, only=None, tokens=None, checks=None, cache=None):
    return [f for f in raw_scan_file(path, only=only, tokens=tokens, checks=checks, cache=cache) if not core.accepted(f)]


def raw_scan_file(path, only=None, tokens=None, checks=None, cache=None):
    rel = core.relative(path)
    suffix = path.suffix.lower()
    if suffix not in SCAN_EXT or core.C.skip_dirs & set(Path(rel).parts) or not path.is_file():
        return []
    if _own_debloat_case(path, rel) or path.stat().st_size > core.MAX_BYTES:
        return []
    source = path.read_text(encoding="utf-8", errors="replace")
    if core.is_test(rel) or not _is_target_file(rel, suffix, source):
        return []

    wanted = checks or CHECKS
    tokens = tokens or discover_project_tokens()
    lines = copy_rules.jsx_lines(source.splitlines()) if suffix in core.JSX else source.splitlines()
    raw_lines = source.splitlines()
    skip = core.ignored_lines(raw_lines)
    look = (lambda n: n not in skip) if only is None else (lambda n: n in only and n not in skip)
    test_from = core.rust_test_start(raw_lines) if suffix == ".rs" else None

    out = []
    if suffix in core.CODE and "design" in wanted and not is_theme_file(rel):
        _audit_file_design(rel, path, lines, source, suffix, test_from, look, skip, tokens["uses_tooltips"], out, cache=cache)
    if "style" in wanted:
        _style_checks(rel, raw_lines, suffix, look, skip, test_from, tokens, out)
    if suffix in core.CODE and "bloat" in wanted:
        out += [f for f in hunt.scan_file(path, only) if f.kind == "copy"]
    return out


def stop_report(repo, session, state, gate, files=None, lean=False, style_only=False):
    """Hook report for newly edited UI files. lean (every session): only layout clutter (dividers,
    boxes, chips, stacked texts); a tautology-debloat-design session also checks hardcoded styles, and
    so does a file where an AI visual tic was reported. style_only: the pass right after a block, on
    the files that had a tic: did the fix bring a hardcoded color, size or radius?"""
    edited = state / f"{session}.files"
    shown = state / f"{session}.design.shown"
    if files is None:
        if not edited.is_file():
            return None
        file_paths = [Path(raw) for raw in dict.fromkeys(edited.read_text(encoding="utf-8").splitlines()) if raw]
    else:
        file_paths = [Path(raw) for raw in files if raw]
    if not file_paths:
        return None
    os.chdir(repo)
    root = repo.resolve()
    core.use_repo(root)
    tics = core.tic_files(state, session)
    if style_only:
        file_paths = [p for p in file_paths if core.relative(p) in tics]
        if not file_paths:
            return None
    styled = {p for p in file_paths if style_only or not lean or core.relative(p) in tics}
    # Lean mode only words a hint with these; the repository walk (~90 ms) is for style checks.
    tokens = discover_project_tokens(root) if styled else {"uses_tooltips": bool(core.C.raw.get("tooltip_calls"))}
    cache = {}
    findings = []
    for path in file_paths:
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        snap = gate.snapshot_of(state, session, path)
        only = gate.new_lines(snap, path) if snap.is_file() else core.added_lines(path)
        if only is None or only:
            checks = {"style"} if style_only else {"design", "style"} if path in styled else {"design"}
            findings += scan_file(path, only=only, tokens=tokens, checks=checks, cache=cache)
    if not lean:  # pruning rescans every check, style included; a full run does it
        core.prune_paths(
            file_paths,
            lambda p: raw_scan_file(p, only=None, tokens=tokens, checks=CHECKS, cache=cache),
            "design_audit.py",
            repo=root,
        )
    before = set(shown.read_text(encoding="utf-8").splitlines()) if shown.is_file() else set()
    keyed = {hashlib.sha1(f.key().encode()).hexdigest()[:16]: f for f in findings}
    fresh = [k for k in keyed if k not in before]
    new = [keyed[k] for k in fresh]
    core.note_tics(new, state, session)
    if not new:
        return None
    limit = 4 if lean else 8
    with shown.open("a", encoding="utf-8") as handle:  # also the cut ones: never back in a later turn
        handle.write("\n".join(fresh) + "\n")
    script_py = (HERE / "design_audit.py").as_posix()
    if style_only:
        lines = ["The tic fix hardcodes styles; use the project's tokens instead:", *core.report_lines(new[:limit])]
        return "\n".join(lines)
    lines = ["UI layout clutter:" if lean else
             f"Design debloat check before delivering (skill tautology-debloat-design, {(HERE / 'SKILL.md').as_posix()})."]
    lines += core.report_lines(new[:limit])
    if len(new) > limit:
        lines.append(f"(+{len(new) - limit} more in CLI)")
    accept = f'"{Path(sys.executable).as_posix()}" "{script_py}" --accept "<finding>" --reason "<why>"'
    lines.append(f"Not clutter? Accept silently: {accept}." if lean else
                 "Fix overloaded layout hierarchy and replace hardcoded styles with project theme variables/tokens. "
                 f"False positive? Accept silently: {accept}.")
    return "\n".join(lines)


def selftest():
    """Checks design/style findings, clean theme compliance, --find, and session hooks end to end."""
    import json
    import shutil
    import subprocess
    import tempfile

    problems = []
    repo = Path(tempfile.mkdtemp(prefix="debloat-"))
    try:
        shutil.copytree(CASES, repo, dirs_exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        core.SELFTEST = True
        os.chdir(repo)
        core.use_repo(repo)

        found_screens = find_screens("dirty", root=repo)
        if not found_screens or "dirty_modal.rs" not in found_screens[0][1]:
            problems.append(f"--find failed to locate dirty_modal.rs: {found_screens!r}")

        tokens = discover_project_tokens(repo)
        dirty = scan_file(repo / "src" / "screens" / "dirty_modal.rs", only=None, tokens=tokens, checks=CHECKS)
        dirty_text = "\n".join(str(f) for f in dirty)
        for needed in (
            "overloaded hierarchy",
            "dividers/separators",
            "explicit spacer boxes",
            "hardcoded color",
            "hardcoded text_size",
            "hardcoded corner radius",
        ):
            if needed not in dirty_text:
                problems.append(f"dirty_modal.rs missing expected finding {needed!r}:\n{dirty_text}")

        clean = scan_file(repo / "src" / "screens" / "clean_modal.rs", only=None, tokens=tokens, checks=CHECKS)
        if clean:
            problems.append(f"clean_modal.rs reported false positive(s): {[str(f) for f in clean]!r}")

        theme_file = scan_file(repo / "src" / "theme" / "mod.rs", only=None, tokens=tokens, checks=CHECKS)
        if theme_file:
            problems.append(f"theme/mod.rs reported false positive(s): {[str(f) for f in theme_file]!r}")

        modular = scan_file(repo / "src" / "screens" / "modular_dirty.rs", only=None, tokens=tokens, checks={"design"})
        modular_text = "\n".join(str(f) for f in modular)
        for needed in ("composed hierarchy", "boxed cards/panels across composed view", "info_hint badges across composed view"):
            if needed not in modular_text:
                problems.append(f"modular_dirty.rs missing expected finding {needed!r}:\n{modular_text}")

        # Stacked/exposed filter toolbar + competing solid primary accents + fixed-width triggers.
        dirty_bar = scan_file(repo / "src" / "screens" / "toolbar_clutter.rs", only=None, tokens=tokens, checks={"design"})
        dirty_bar_text = "\n".join(str(f) for f in dirty_bar)
        for needed in (
            "competing primary accents",
            "stacked filter controls across composed view",
            "fixed-width filter/sort trigger (175px)",
            "fixed-width filter/sort trigger (199px)",
        ):
            if needed not in dirty_bar_text:
                problems.append(f"toolbar_clutter.rs missing expected finding {needed!r}:\n{dirty_bar_text}")

        clean_bar = scan_file(repo / "src" / "screens" / "toolbar_clean.rs", only=None, tokens=tokens, checks=CHECKS)
        if clean_bar:
            problems.append(f"toolbar_clean.rs reported false positive(s): {[str(f) for f in clean_bar]!r}")

        # W1 (multi-line None) + W2 (if / else if / else + early return) must not false-positive.
        branched = scan_file(repo / "src" / "screens" / "branched_clean.rs", only=None, tokens=tokens, checks={"design"})
        if branched:
            problems.append(f"branched_clean.rs reported false positive(s): {[str(f) for f in branched]!r}")

        # W3 cross-file screen composition (split_screen/view.rs -> panels.rs).
        split_found = scan_file(repo / "src" / "screens" / "split_screen" / "view.rs", only=None, tokens=tokens, checks={"design"})
        split_text = "\n".join(str(f) for f in split_found)
        if "boxed cards/panels across composed view" not in split_text:
            problems.append(f"split_screen/view.rs missing cross-file composed cards finding:\n{split_text}")

        # W5 single-line chained nesting & sibling separation.
        inline_nested = repo / "src" / "screens" / "inline_chain.rs"
        inline_nested.write_text(
            "use gpui::{div, prelude::*};\nuse crate::theme::Palette;\n"
            "pub fn stacked(p: Palette) -> impl IntoElement {\n"
            "    div().bg(p.bg).child(div().bg(p.surface).child(div().border_1().child(div().bg(p.card).child(\"x\"))))\n"
            "}\n"
            "pub fn siblings(p: Palette) -> impl IntoElement {\n"
            "    div().bg(p.bg).child(div().bg(p.card).child(\"a\")).child(div().bg(p.card).child(\"b\"))\n"
            "}\n",
            encoding="utf-8",
        )
        inline_res = "\n".join(str(f) for f in scan_file(inline_nested, only=None, tokens=tokens, checks={"design"}))
        if "stacked: overloaded hierarchy with 4 nested" not in inline_res or "siblings:" in inline_res:
            problems.append(f"inline_chain.rs nesting mismatch:\n{inline_res}")

        # End-to-end Antigravity hook check with /tautology-debloat-design prompt + W6 persistence
        mod_gate = MODULAR_DIR / "hooks" / "modular_gate.py"
        edit_gate = TAUTOLOGY_DIR / "hooks" / "edit_gate.py"
        stop_gate = TAUTOLOGY_DIR / "hooks" / "stop_gate.py"

        def run_prompt(text):
            return subprocess.run(
                [sys.executable, str(mod_gate)],
                input=json.dumps({
                    "hookEventName": "PreInvocation",
                    "conversationId": "debloat-selftest",
                    "cwd": str(repo),
                    "lastUserInput": text,
                }),
                capture_output=True, text=True, encoding="utf-8", cwd=repo,
            )

        r = run_prompt("/tautology-debloat-design clean_modal")
        design_flag = repo / ".git/tautology/debloat-selftest.design"
        if r.returncode != 0 or not design_flag.is_file():
            problems.append(f"modular_gate did not set .design flag: rc={r.returncode} stderr={r.stderr}")

        # W6: iterative UI follow-ups must preserve .design flag; explicit task switch must clear it.
        run_prompt("pode remover a borda do card e ajustar o espacamento do header")
        if not design_flag.is_file():
            problems.append("modular_gate dropped .design flag on UI follow-up prompt")
        run_prompt("pode aplicar")
        if not design_flag.is_file():
            problems.append("modular_gate dropped .design flag on short continuation prompt")
        run_prompt("qual o status do pedido 123?")
        if design_flag.is_file():
            problems.append("modular_gate kept .design flag on unrelated short prompt")
        run_prompt("/tautology-debloat-design clean_modal")
        run_prompt("agora vamos auditar os testes unitarios do worker e depois fazer git commit")
        if design_flag.is_file():
            problems.append("modular_gate kept .design flag after switching away to tests/commit")
        run_prompt("/tautology-debloat-design clean_modal")

        target = repo / "src" / "screens" / "edited_modal.rs"
        subprocess.run(
            [sys.executable, str(edit_gate)],
            input=json.dumps({
                "hookEventName": "PreToolUse",
                "conversationId": "debloat-selftest",
                "cwd": str(repo),
                "toolCall": {"name": "edit_file", "args": {"Path": str(target)}},
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        target.write_text((repo / "src" / "screens" / "dirty_modal.rs").read_text(encoding="utf-8"), encoding="utf-8")
        r1 = subprocess.run(
            [sys.executable, str(stop_gate)],
            input=json.dumps({
                "hookEventName": "Stop",
                "conversationId": "debloat-selftest",
                "cwd": str(repo),
                "terminationReason": "NO_TOOL_CALL",
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        out1 = json.loads(r1.stdout or "{}")
        reason = out1.get("reason", "")
        if out1.get("decision") != "continue" or "Design debloat check" not in reason or "hardcoded color" not in reason:
            problems.append(f"stop_gate did not enforce design_audit during .design session: {out1!r}")

        r2 = subprocess.run(
            [sys.executable, str(stop_gate)],
            input=json.dumps({
                "hookEventName": "Stop",
                "conversationId": "debloat-selftest",
                "cwd": str(repo),
                "terminationReason": "NO_TOOL_CALL",
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        if json.loads(r2.stdout or "{}") != {}:
            problems.append(f"repeated stop_gate was not suppressed: {r2.stdout!r}")
    finally:
        shutil.rmtree(repo, ignore_errors=True)

    # W4: Tailwind arbitrary values, .css outside theme, and late-discovered z_theme CSS tokens after >50 UI files.
    web_repo = Path(tempfile.mkdtemp(prefix="debloat-web-"))
    try:
        for idx in range(55):
            f = web_repo / "src" / f"a_{idx:02d}_widget.tsx"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("export function W() { return <div>ok</div>; }\n", encoding="utf-8")
        tok_css = web_repo / "src" / "z_theme" / "tokens.css"
        tok_css.parent.mkdir(parents=True, exist_ok=True)
        tok_css.write_text(":root {\n  --bg: #111111;\n  --font-size-body: 14px;\n  --radius-card: 12px;\n}\n", encoding="utf-8")
        card_tsx = web_repo / "src" / "components" / "TailwindCard.tsx"
        card_tsx.parent.mkdir(parents=True, exist_ok=True)
        card_tsx.write_text(
            "export function TailwindCard() {\n"
            "  return (\n"
            "    <div className=\"bg-[#ff00aa] text-[15px] rounded-[14px]\">\n"
            "      <div className=\"bg-[var(--bg)] text-sm rounded-lg\">Clean</div>\n"
            "      <div className=\"bg-card\"><div className=\"bg-card\"><div className=\"border\"><div className=\"bg-muted\">deep</div></div></div></div>\n"
            "    </div>\n"
            "  );\n"
            "}\n",
            encoding="utf-8",
        )
        card_css = web_repo / "src" / "components" / "CardExtras.css"
        card_css.write_text(
            ":root { --local-surface: #222222; }\n"
            ".card-extra { color: #ff00aa; font-size: 15px; border-radius: 14px; background: var(--bg); }\n",
            encoding="utf-8",
        )
        mod_css = web_repo / "src" / "components" / "styles.css"
        mod_css.write_text(".mod { color: #ff00aa; font-size: 15px; border-radius: 14px; }\n", encoding="utf-8")
        hook_ts = web_repo / "src" / "hooks" / "useCard.ts"
        hook_ts.parent.mkdir(parents=True, exist_ok=True)
        hook_ts.write_text("import { useState } from 'react'\nexport function useCard() { return useState(false) }\n", encoding="utf-8")
        os.chdir(web_repo)
        core.use_repo(web_repo)
        web_tokens = discover_project_tokens(web_repo)
        if not (web_tokens["has_palette"] and web_tokens["has_font_tokens"] and web_tokens["has_radius_tokens"]):
            problems.append(f"discover_project_tokens missed late CSS tokens after >50 files: {web_tokens!r}")
        tsx_out = "\n".join(str(f) for f in scan_file(card_tsx, only=None, tokens=web_tokens, checks=CHECKS))
        for needed in ("Tailwind arbitrary color", "Tailwind font-size", "Tailwind radius", "overloaded hierarchy"):
            if needed not in tsx_out:
                problems.append(f"TailwindCard.tsx missing expected finding {needed!r}:\n{tsx_out}")
        css_out = "\n".join(str(f) for f in scan_file(card_css, only=None, tokens=web_tokens, checks=CHECKS))
        for needed in ("CSS color literal", "CSS font-size", "CSS border-radius"):
            if needed not in css_out:
                problems.append(f"CardExtras.css missing expected finding {needed!r}:\n{css_out}")
        if "#222222" in css_out:
            problems.append(f"CardExtras.css falsely flagged :root custom property:\n{css_out}")
        tok_out = scan_file(tok_css, only=None, tokens=web_tokens, checks=CHECKS)
        if tok_out:
            problems.append(f"z_theme/tokens.css reported false positive(s): {[str(f) for f in tok_out]!r}")
        mod_out = "\n".join(str(f) for f in scan_file(mod_css, only=None, tokens=web_tokens, checks=CHECKS))
        if "CSS color literal" not in mod_out:
            problems.append(f"components/styles.css was skipped as theme file:\n{mod_out}")
        find_hits = [rel for _, rel, _ in find_screens("card", root=web_repo)]
        if not find_hits or find_hits[0] != "src/components/TailwindCard.tsx":
            problems.append(f"find_screens('card') did not prioritize component JSX over hooks: {find_hits!r}")

        # State discovery + generic manifest + pure-stdlib visual PNG checks
        states_manifest = web_repo / "states.json"
        states_manifest.write_text(json.dumps({
            "states": [
                {"id": "tailwind-empty", "label": "Empty state", "screen": "TailwindCard"},
                {"id": "other-screen", "label": "Other", "screen": "unrelated"},
            ]
        }), encoding="utf-8")
        cfg_file = web_repo / ".claude" / "tautology" / "config.json"
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(json.dumps({"design": {"states_file": "states.json"}}), encoding="utf-8")
        core.use_repo(web_repo)
        card_tsx.write_text(
            card_tsx.read_text(encoding="utf-8")
            + "\nexport type CardMode = 'compact' | 'expanded';\n"
            + "// std::env::var(\"APP_MODAL_PREVIEW\") or process.env.PREVIEW_STATE\n",
            encoding="utf-8",
        )
        discovered = discover_states([card_tsx], root=web_repo)
        if "CardMode" not in discovered["enums"] or "tailwind-empty" not in [s["id"] for s in discovered["manifest"]]:
            problems.append(f"discover_states missed expected enum or manifest state: {discovered!r}")

        blank_png = web_repo / "blank.png"
        before_png = web_repo / "before.png"
        after_png = web_repo / "after.png"
        pw, ph = 240, 220
        visual_check.write_png(blank_png, pw, ph, [bytes([18, 18, 22] * pw) for _ in range(ph)])
        before_rows, after_rows = [], []
        for y in range(ph):
            row_a, row_b = bytearray(), bytearray()
            for x in range(pw):
                c_a = (18, 18, 22)
                c_b = (18, 18, 22)
                # Dense boxed grid in "before" vs fewer elements in "after" across 10+ shaded shades
                if 20 <= x < 220 and 20 <= y < 190:
                    if x in (20, 219) or y in (20, 189) or (y % 40 == 0):
                        c_a = (120 + (y % 10), 130, 150)
                    elif 30 <= x < 180 and 30 <= y < 170:
                        c_a = (52 + (x % 9), 56, 66)
                if 20 <= x < 200 and 30 <= y < 110:
                    c_b = (110 + (x % 13), 125 + ((y // 16) * 10), 150)
                row_a.extend(c_a)
                row_b.extend(c_b)
            before_rows.append(bytes(row_a))
            after_rows.append(bytes(row_b))
        visual_check.write_png(before_png, pw, ph, before_rows)
        visual_check.write_png(after_png, pw, ph, after_rows)

        if visual_check.inspect_image(blank_png)["ok"]:
            problems.append("inspect_image accepted a uniform blank PNG")
        before_info = visual_check.inspect_image(before_png)
        if not before_info["ok"]:
            problems.append(f"inspect_image rejected valid test PNG: {before_info!r}")
        cmp_res = visual_check.compare_images(before_png, after_png)
        if not cmp_res["ok"] or cmp_res["density_delta"] >= 0 or not cmp_res["regions"]:
            problems.append(f"compare_images failed on before->after reduction: {cmp_res!r}")
    finally:
        core.SELFTEST = False
        shutil.rmtree(web_repo, ignore_errors=True)
    for p in problems:
        print(f"FAIL    {p}")
    print(f"design_audit selftest: {'ok' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


STATE_ENUM_RE = re.compile(
    r"\benum\s+(\w*(?:Tab|Modal|Dialog|State|Mode|Step|Category|View|Section|Filter|Page)\w*)\s*\{([^}]+)\}",
    re.S,
)
TS_UNION_RE = re.compile(
    r"\btype\s+(\w*(?:Tab|Modal|Dialog|State|Mode|Step|Category|View|Section|Filter|Page)\w*)\s*=\s*([^;\n]+)"
)
ROUTE_KEY_RE = re.compile(
    r'"([a-z][a-z0-9_-]{1,32})"\s*=>\s*(?:Self|[\w:]*(?:Screen|Tab|Modal|Dialog|State|Mode|Page|Step|Section|Route))::'
    r'|<Route\b[^>]*path=["\']([^"\']+)["\']'
)
ENV_HOOK_RE = re.compile(
    r'(?:std::env::var|os\.environ(?:\.get)?)\(\s*["\']([A-Z][A-Z0-9_]{2,})["\']|process\.env\.([A-Z][A-Z0-9_]{2,})'
)
COND_STATE_RE = re.compile(
    r"\b(?:match|if)\b[^\n{]*\b(loading|empty|error|offline|editing|deleting|confirm\w*|collapsed|expanded|modal|dialog)\b",
    re.I,
)


def discover_states(targets, root=None):
    """Inventories UI states (enums, routes, modal/empty branches, env hooks, and optional state manifest)."""
    base = Path(root or core.REPO).resolve()
    paths = _expand_targets(targets) if targets else [p for _, p, _ in repo_ui_files(base)]
    seen = set()
    file_cache = {}
    scan_list = []
    for p in paths:
        rp = p.resolve()
        if rp not in seen and rp.is_file():
            seen.add(rp)
            scan_list.append(rp)
        for _, pp, *_ in _screen_peer_paths(rp, file_cache):
            prp = pp.resolve()
            if prp not in seen:
                seen.add(prp)
                scan_list.append(prp)

    enums, routes, env_hooks, branch_states = {}, [], set(), set()
    screen_hints = {p.stem.lower() for p in scan_list} | {p.parent.name.lower() for p in scan_list}
    for p in scan_list:
        rel = core.relative(p)
        if core.is_test(rel) or p.suffix.lower() in STYLE_EXT or p.stat().st_size > core.MAX_BYTES:
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        for ename, body in STATE_ENUM_RE.findall(src):
            variants = [v.strip().split("(")[0].split("{")[0].split("=")[0].strip() for v in body.split(",")]
            clean = [v for v in variants if v and re.match(r"^[A-Za-z_]\w*$", v)]
            if clean:
                enums[ename] = clean[:12]
        for tname, rhs in TS_UNION_RE.findall(src):
            opts = re.findall(r"""['"]([A-Za-z0-9_-]+)['"]""", rhs)
            if opts:
                enums[tname] = opts[:12]
        for m in ROUTE_KEY_RE.finditer(src):
            routes.append(m.group(1) or m.group(2))
        for m in ENV_HOOK_RE.finditer(src):
            env_hooks.add(m.group(1) or m.group(2))
        for m in COND_STATE_RE.finditer(src):
            branch_states.add(m.group(1).lower())

    manifest = []
    states_rel = core.C.design.get("states_file")
    if states_rel:
        sfile = (base / states_rel).resolve()
        if sfile.is_file():
            try:
                raw = json.loads(sfile.read_text(encoding="utf-8", errors="replace"))
                for entry in raw.get("states") or []:
                    if not isinstance(entry, dict) or not entry.get("id"):
                        continue
                    scr = str(entry.get("screen") or "").lower()
                    if not scr or scr in screen_hints or not targets:
                        manifest.append({"id": entry["id"], "label": entry.get("label") or entry["id"], "screen": entry.get("screen") or ""})
            except (OSError, ValueError):
                pass
    return {
        "enums": enums,
        "routes": list(dict.fromkeys(routes))[:20],
        "branches": sorted(branch_states),
        "env_hooks": sorted(env_hooks),
        "manifest": manifest[:20],
        "capture_command": core.C.design.get("capture_command") or "",
    }


def _format_states(info):
    lines = []
    for name, variants in sorted(info["enums"].items()):
        lines.append(f"state enum {name}: {', '.join(variants)}")
    if info["routes"]:
        lines.append("routes/keys: " + ", ".join(info["routes"]))
    if info["branches"]:
        lines.append("conditional states: " + ", ".join(info["branches"]))
    if info["env_hooks"]:
        lines.append("env preview hooks: " + ", ".join(info["env_hooks"]))
    if info["manifest"]:
        lines.append("declared states: " + ", ".join(f"{s['id']} ({s['label']})" for s in info["manifest"]))
    if info["capture_command"]:
        lines.append("configured capture_command (run only when authorized): " + info["capture_command"])
    return "\n".join(lines) if lines else "No explicit UI states or modals detected."


def _expand_targets(targets):
    out = []
    for raw in targets:
        path = Path(raw).resolve()
        if path.is_dir():
            out += sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SCAN_EXT)
        else:
            out.append(path)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*")
    parser.add_argument("--find", help="locate UI files matching screen name, symbol or title")
    parser.add_argument("--states", action="store_true", help="list interactive states, enums, modals, and hooks")
    parser.add_argument("--check-image", help="verify PNG screenshot is non-blank and measure visual density")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE_PNG", "AFTER_PNG"),
                        help="compare before/after UI PNG captures by region and visual density")
    parser.add_argument("--diff", action="store_true", help="changed UI lines against HEAD")
    parser.add_argument("--all", action="store_true", help="scan the whole file, not only changed lines")
    parser.add_argument("--only", help="comma-separated checks: " + ", ".join(sorted(CHECKS)))
    parser.add_argument("--accept", help="line of a finding that is not a defect")
    parser.add_argument("--reason", default="", help="why the accepted finding is kept")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.accept:
        return core.accept(args.accept, args.reason, tool="design_audit.py")
    if args.selftest:
        return selftest()
    if args.check_image:
        info = visual_check.inspect_image(args.check_image)
        print(visual_check.format_inspect(args.check_image, info))
        return 0 if info["ok"] else 1
    if args.compare:
        res = visual_check.compare_images(args.compare[0], args.compare[1])
        print(visual_check.format_compare(res))
        return 0 if res["ok"] else 1
    if args.states:
        print(_format_states(discover_states(args.files)))
        return 0
    if args.find:
        hits = find_screens(args.find)
        if not hits:
            print(f"No UI screen matched {args.find!r}.")
            return 1
        for score, rel, siblings in hits:
            extra = f" (siblings: {', '.join(siblings)})" if siblings else ""
            print(f"[{score}] {rel}{extra}")
        return 0

    wanted = CHECKS
    if args.only:
        wanted = {c.strip().lower() for c in args.only.split(",")} & CHECKS
        if not wanted:
            print(f"--only takes: {', '.join(sorted(CHECKS))}")
            return 2

    tokens = discover_project_tokens()
    targets = core.changed_files() if args.diff else args.files
    paths = _expand_targets(targets) if targets else [p for _, p, _ in repo_ui_files()]
    cache = {}
    found = []
    for path in paths:
        only = None if args.all else core.added_lines(path)
        if only is not None and not only:
            continue
        found += scan_file(path, only=only, tokens=tokens, checks=wanted, cache=cache)
    if args.all and not args.diff and wanted == CHECKS:
        core.prune_paths(
            paths,
            lambda p: raw_scan_file(p, only=None, tokens=tokens, checks=CHECKS, cache=cache),
            "design_audit.py",
        )
    for f in found:
        print(f)
    print(f"{len(found)} design finding(s).")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
