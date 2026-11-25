"""Hunts code that should be one piece: copied call chains, copied blocks, duplicate UI helpers,
and structurally near-duplicate parameterizable components. Says where to extract or unify it,
by the codebase's architecture.

    python patterns.py a.rs b.rs                         # patterns that touch these files
    python patterns.py src/components                    # patterns touching files in a folder
    python patterns.py --all                             # the whole repository
    python patterns.py --accept "<finding>" --reason "<why>"
    python patterns.py --selftest                        # cases/ vs cases/expected.txt, and the hook

Calling the same function in many places is reuse, not a defect. The defect is the same sequence
copied or a near-duplicate component differing only by 1-3 callbacks/props/slots: it becomes one
function or composed shell in the right layer, and each place calls it.
Project settings: .claude/modular/architecture.md (the destination map) and .claude/modular/accepted.txt.
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"
sys.path.insert(0, str(HERE.parent / "tautology"))
import core  # noqa: E402  (the tautology skill: findings, accepted findings, test-file detection)
from core import Finding  # noqa: E402

ACCEPTED_FILE = core.project_subdir(core.REPO, "modular") / "accepted.txt"
MAP_FILE = Path(".claude") / "modular" / "architecture.md"

CODE = {".rs", ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".ps1", ".sh", ".bat", ".cmd"}
JS_FAMILY = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"}
MARKUP_SUFFIXES = {".jsx", ".tsx", ".vue", ".svelte"}
SKIP = {"target", "node_modules", "dist", "build", ".venv", "__pycache__", "fixtures", "vendor", ".git"}
MIN_CALLS = 4      # chain: calls in a row
MIN_COUNT = 4      # chain: times in total
MIN_FILES = 3      # chain: different files
MIN_BLOCK = 6      # block: equal non-blank lines
MIN_UNIT_LINES = 15  # near-duplicate component: full-size unit (normal score threshold)
MIN_SMALL_UNIT_LINES = 8  # small units (8-14 lines) qualify with a stricter score and shared structure
MIN_COMP_SCORE = 0.72
MIN_SMALL_COMP_SCORE = 0.88
MIN_SMALL_SHARED_CLASSES = 3
MIN_CLASS_SIM = 0.45
MIN_CREATED_SCORE = 0.65
MIN_CREATED_LINES = 10
COMPOSITION_DISCOUNT = 0.25
BEHAV_PROP_HINT = {
    "setTimeout": "debounceMs?", "setInterval": "pollMs?", "Enter": "onEnter?", "Escape": "closeOnEscape?",
    "keydown": "onKeyDown?", "focus": "autoFocus?", "stopPropagation": "closeOnBackdrop?", "preventDefault": "onSubmit?",
    "useEffect": "an effect prop/hook", "addEventListener": "a shared hook",
}
MIN_FAMILY_SCORE = 0.90  # pairs at or above this chain into one "N near-identical components" finding
MIN_PLAIN_SHINGLE = 0.70  # a plain near-duplicate needs this much code-shape agreement on top of the score
MIN_ROLE_SCORE = 0.50    # same role cue (search input) + same markup, different handlers
MIN_CHROME_SCORE = 0.62  # identical classes + markup, small presentational unit (empty states)
MIN_CONTAINMENT = 0.85   # subset variant: share of the small unit's shingles found in the large one
SHELL_CUES = {"overlay_backdrop", "escape_keydown", "modal_chrome"}
MIN_SHELL_SCORE = 0.40   # 2+ shared raw shell cues report a shell to extract from this score up
PER_ROUND = 3      # patterns per hook round

# Framework idiom: chains of only these methods, with trivial arguments, are the normal way to write.
COMMON = {"flex", "flex_1", "flex_col", "flex_row", "flex_none", "flex_shrink_0", "flex_grow", "min_w_0", "min_h_0",
          "w_full", "h_full", "size_full", "items_center", "items_start", "items_end", "justify_center",
          "justify_between", "justify_end", "justify_start", "gap", "gap_1", "gap_2", "gap_3", "gap_4", "id",
          "overflow_hidden", "overflow_y_scroll", "overflow_x_scroll", "relative", "absolute", "w", "h",
          "into_iter", "iter", "iter_mut", "map", "collect", "cloned", "copied", "flatten", "filter", "rev",
          "unwrap", "unwrap_or_default", "clone", "to_owned", "to_string", "into", "as_ref", "trim", "len"}
# Calls that carry content, not style: they cut the chain, and the argument is scanned on its own.
BREAKERS = {"child", "children", "on_click", "on_mouse_down", "on_mouse_up", "on_hover", "when", "when_some",
            "with_animation", "on_action", "tooltip", "hint", "map", "and_then_some"}
TRIVIAL_ARG = re.compile(r'^$|^"S"$|^-?[\d_.]+$|^px\(-?[\d_.]+\)$|^\w+\.\w+$|^\w+$')
UI_GENERAL = re.compile(
    r"\bpx\(|\b(div|h_flex|v_flex)\(\)|\.child\(|\.text_\w+\(|\.bg\(|\.border\w*\(|\.rounded\w*\(|"
    r"\b(?:gpui::|AnyElement|IntoElement|BoundsSlot|Window)\b|\bself\.palette\b|"
    r"\b(?:kit|visual)::\w+\(|\bcx\.(?:entity|listener|notify|emit)\(|"
    r"\.(?:on_click|on_change|on_hover|on_mouse_\w+|hint|cursor_hint|tooltip|accessibility_label|into_any_element)\(|"
    r"\b(?:useState|useEffect|useCallback|useMemo|useRef|QWidget|QPushButton|QLabel|QVBoxLayout|QHBoxLayout|QDialog|QLineEdit)\b"
)
# Suffix-scoped markup signals: never applied to Rust or non-markup files, and negative lookbehind excludes generics (Array<Item>).
UI_MARKUP = re.compile(
    r"className=|\bstyle=|(?<![A-Za-z0-9_$\]])<[A-Za-z][\w.-]*(?:\s+[a-zA-Z_:][\w.:-]*(?:=|\s|/>|>)|\s*/?>)"
)
UI_JS_STRICT = re.compile(
    r"className=|\bstyle=|(?<![A-Za-z0-9_$\]])<[A-Za-z][\w.-]*(?:\s+[a-zA-Z_:][\w.:-]*=|\s*/>)"
)
# Style methods (GPUI, Tailwind-like): a chain of only these is a visual piece, even without px() or a colour.
STYLE = re.compile(r"\.(absolute|relative|top_\w+|left_\w+|right_\w+|bottom_\w+|inset_\w+|size_\w+|rounded\w*|"
                   r"border\w*|bg|shadow\w*|opacity|cursor_\w+|text_\w+|font_\w+|line_height|flex\w*|items_\w+|"
                   r"justify_\w+|gap\w*|p[xytblr]?|m[xytblr]?|w_\w+|h_\w+|overflow_\w+)\(")
COMPONENT = re.compile(r"(^|/)(components?|screens?|views?|pages?|widgets?|ui)/|\.(tsx|jsx|vue|svelte)$")
UI_DIR_HINT = re.compile(r"(^|/)(components?|widgets?|ui|design|kit)/")


def is_ui_code(code, files=()):
    """True when code uses UI primitives; JSX tag checks are strictly scoped to web/markup suffixes."""
    if UI_GENERAL.search(code) or STYLE.search(code):
        return True
    suffixes = {Path(rel).suffix.lower() for rel in files}
    if suffixes & MARKUP_SUFFIXES:
        return bool(UI_MARKUP.search(code))
    if suffixes & JS_FAMILY:
        return bool(UI_JS_STRICT.search(code))
    return False


# ----------------------------------------------------------------------------- architecture
class Architecture:
    """Destination map: .claude/modular/architecture.md of the repository. Without it, looks for a description."""

    def __init__(self, repo):
        self.repo, self.layers, self.destinations, self.outside = Path(repo), [], {}, []
        self.components, self.common, self.verify, self.source = [], set(COMMON), "", ""
        self.ui_layer = None
        agents_map = self.repo / ".agents" / "modular" / "architecture.md"
        self.file = agents_map if agents_map.is_file() else self.repo / MAP_FILE
        self.docs = []
        if self.file.is_file():
            self._read(self.file.read_text(encoding="utf-8"))
        else:
            self.docs = described_architecture(self.repo)

    def _read(self, text):
        for raw in text.splitlines():
            m = re.match(r"^\s*([\w ]+?)\s*:\s*(.*)$", raw)
            if not m or raw.lstrip().startswith("#"):
                continue
            key, value = m.group(1).strip().lower(), m.group(2).strip()
            layer = re.fullmatch(r"layer (\d+)", key)
            target = re.fullmatch(r"target (\d+)", key)
            if layer:
                self.layers += [(prefix, int(layer.group(1))) for prefix in value.split()]
            elif target:
                self.destinations[int(target.group(1))] = value
            elif key in ("ui_layer", "ui layer") and value.isdigit():
                self.ui_layer = int(value)
            elif key == "exclude":
                self.outside += value.split()
            elif key == "components":
                self.components += value.split()
            elif key == "idioms":
                self.common |= set(value.split())
            elif key == "verify":
                self.verify = value
            elif key == "source":
                self.source = value
        self.layers.sort(key=lambda item: -len(item[0]))  # the longest prefix decides

    @property
    def defined(self):
        return bool(self.destinations)

    def visual_layer(self):
        """Lowest destination layer for UI pieces: explicit ui_layer, else inferred from components/target paths, else >=3."""
        if self.ui_layer is not None and self.ui_layer in self.destinations:
            return self.ui_layer
        for n in sorted(self.destinations):
            dest = self.destinations[n]
            layer_prefixes = [p for p, ln in self.layers if ln == n]
            if any(dest.startswith(c) or c.startswith(dest) for c in self.components):
                return n
            if any(p.startswith(c) or c.startswith(p) for p in layer_prefixes for c in self.components):
                return n
            if UI_DIR_HINT.search(dest) or any(UI_DIR_HINT.search(p) for p in layer_prefixes):
                return n
        return min((n for n in self.destinations if n >= 3), default=None)

    def layer(self, rel):
        return next((n for prefix, n in self.layers if rel == prefix or rel.startswith(prefix)), None)

    def excluded(self, rel):
        return any(rel.startswith(prefix) for prefix in self.outside)

    def is_component(self, rel):
        return any(rel.startswith(p) for p in self.components) or bool(COMPONENT.search(rel))


def _own_modular_case(repo, rel):
    return not core.SELFTEST and (
        (Path(repo) / rel).resolve().is_relative_to(CASES) or core.own_cases(Path(repo) / rel, rel)
    )


def described_architecture(repo):
    """Documents that describe the architecture: ARCHITECTURE.md in any folder, or a section in AGENTS/CLAUDE/README."""
    found = []
    for rel in tracked(repo):
        if SKIP & set(Path(rel).parts) or _own_modular_case(repo, rel):
            continue
        name = Path(rel).name.lower()
        if name in ("architecture.md", "arquitetura.md"):
            found.append(rel)
        elif name in ("agents.md", "claude.md", "gemini.md", "readme.md") and "/" not in rel:
            text = (Path(repo) / rel).read_text(encoding="utf-8", errors="replace")
            if re.search(r"(?im)^#+ .*(architecture|arquitetura|layers?|camadas?)\b", text):
                found.append(rel)
    return found


def tracked(repo):
    """The repository's files (git, without the ignored ones); outside a git root, every file in the folder."""
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo, capture_output=True, text=True).stdout.strip()
    if not top or Path(top).resolve() != Path(repo).resolve():
        return [p.relative_to(repo).as_posix() for p in Path(repo).rglob("*") if p.is_file()]
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-co", "--exclude-standard"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    return [l for l in out.splitlines() if l]


# ------------------------------------------------------------------------------- tokenizer & production
_JS_TOKENS_BLANK = re.compile(
    r"(?P<jxc>\{\s*/\*[\s\S]*?\*/\s*\})"
    r"|(?P<bc>/\*[\s\S]*?\*/)"
    r"|(?P<hc><!--[\s\S]*?-->)"
    r"|(?P<lc>//[^\n]*)"
    r"|(?P<tpl>`(?:\$\{(?:[^{}]|\{[^{}]*\})*\}|\$(?!\{)|[^`\\$]|\\[\s\S])*`)"
    r'|(?P<dq>"(?:[^"\\\n]|\\.)*")'
    r"|(?P<sq>(?<![A-Za-z])'(?:[^'\\\n]|\\.)*')"
)
_JS_TOKENS_KEEP_CLASS = re.compile(
    r"(?P<jxc>\{\s*/\*[\s\S]*?\*/\s*\})"
    r"|(?P<bc>/\*[\s\S]*?\*/)"
    r"|(?P<hc><!--[\s\S]*?-->)"
    r"|(?P<lc>//[^\n]*)"
    r'|(?P<cls>\b(?:className|class)\s*=\s*(?:"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'))'
    r"|(?P<tpl>`(?:\$\{(?:[^{}]|\{[^{}]*\})*\}|\$(?!\{)|[^`\\$]|\\[\s\S])*`)"
    r'|(?P<dq>"(?:[^"\\\n]|\\.)*")'
    r"|(?P<sq>(?<![A-Za-z])'(?:[^'\\\n]|\\.)*')"
)
_RS_TOKENS = re.compile(
    r"(?P<bc>/\*[\s\S]*?\*/)"
    r"|(?P<lc>//[^\n]*)"
    r'|(?P<raw>r(?P<hashes>#*)"[\s\S]*?"(?P=hashes))'
    r"|(?P<ch>'(?:[^'\\\n]|\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F_]+\}|.))')"
    r'|(?P<dq>"(?:[^"\\\n]|\\[\s\S])*")'
)
_PY_TOKENS = re.compile(
    r"(?P<lc>#[^\n]*)"
    r'|(?P<tq>"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')'
    r'|(?P<dq>"(?:[^"\\\n]|\\.)*")'
    r"|(?P<sq>'(?:[^'\\\n]|\\.)*')"
)
_SH_TOKENS = re.compile(
    r"(?P<lc>#[^\n]*)"
    r'|(?P<dq>"(?:[^"\\\n]|\\.)*")'
    r"|(?P<sq>'(?:[^'\\\n]|\\.)*')"
)
_BAT_TOKENS = re.compile(
    r"(?im)(?P<lc>^\s*(?:rem\b|::)[^\n]*)"
    r'|(?P<dq>"(?:[^"\\\n]|\\.)*")'
)


def blank_strings(text, suffix="", preserve_class=False):
    """Comment-first, quote-safe single-pass blanking (always preserves exact newline count)."""
    suf = (suffix or "").lower()
    if suf == ".rs":
        def _rs_sub(m):
            nl = "\n" * m.group(0).count("\n")
            kind = m.lastgroup
            if kind in ("bc", "lc"):
                return nl
            if kind == "ch":
                return "'C'"
            return '"S"' + nl
        return _RS_TOKENS.sub(_rs_sub, text)
    if suf in JS_FAMILY or not suf:
        rx = _JS_TOKENS_KEEP_CLASS if preserve_class else _JS_TOKENS_BLANK
        def _js_sub(m):
            nl = "\n" * m.group(0).count("\n")
            kind = m.lastgroup
            if kind in ("jxc", "bc", "hc", "lc"):
                return nl
            if kind == "cls":
                attr, _, val = m.group(0).partition("=")
                inner = re.sub(r"\s+", " ", val.strip()[1:-1].strip())
                return f'{attr.strip()}="{inner}"'
            return '"S"' + nl
        return rx.sub(_js_sub, text)
    if suf == ".py":
        def _py_sub(m):
            nl = "\n" * m.group(0).count("\n")
            return nl if m.lastgroup == "lc" else '"S"' + nl
        return _PY_TOKENS.sub(_py_sub, text)
    if suf in (".sh", ".ps1"):
        def _sh_sub(m):
            nl = "\n" * m.group(0).count("\n")
            return nl if m.lastgroup == "lc" else '"S"' + nl
        return _SH_TOKENS.sub(_sh_sub, text)
    def _bat_sub(m):
        return "" if m.lastgroup == "lc" else '"S"'
    return _BAT_TOKENS.sub(_bat_sub, text)


def mask_for_braces(text, suffix=""):
    """Replaces comments and string contents with spaces of identical length (1-to-1 char offsets with text)."""
    suf = (suffix or "").lower()
    rx = (_RS_TOKENS if suf == ".rs" else
          _PY_TOKENS if suf == ".py" else
          _SH_TOKENS if suf in (".sh", ".ps1") else
          _BAT_TOKENS if suf in (".bat", ".cmd") else
          _JS_TOKENS_BLANK)

    def _space_keep_nl(m):
        return "".join("\n" if ch == "\n" else " " for ch in m.group(0))

    return rx.sub(_space_keep_nl, text)


def code_files(repo, arch, suffixes=None):
    for rel in tracked(repo):
        path = Path(rel)
        if path.suffix not in (suffixes or CODE) or rel.endswith(".d.ts") or SKIP & set(path.parts) or arch.excluded(rel):
            continue
        if _own_modular_case(repo, rel):
            continue
        if core.is_test(rel):
            continue
        yield rel


TS_TYPE_START = re.compile(
    r"^\s*(?:export\s+)?(?:declare\s+)?"
    r"(?:type\s+[A-Za-z_]\w*(?:<[^>]*>)?\s*=(?:[^;{]*\{|\s*$)|interface\s+[A-Za-z_]\w*\b)"
)
RS_CFG_TEST = re.compile(r"^\s*#\[cfg\((?:test|all\(\s*test\b[^)]*\))\)\]")


def production(text, suffix):
    """Production code: Rust's #[cfg(test)] modules and TS type/interface blocks stay out (preserving line numbers)."""
    if suffix not in (".ts", ".tsx", ".rs"):
        return text
    lines = text.splitlines()
    clean_lines = blank_strings(text, suffix).splitlines()
    if len(clean_lines) < len(lines):
        clean_lines += [""] * (len(lines) - len(clean_lines))
    if suffix in (".ts", ".tsx"):
        out, in_type, depth = [], False, 0
        for line, cline in zip(lines, clean_lines):
            if not in_type and TS_TYPE_START.match(line):
                in_type, depth = True, cline.count("{") - cline.count("}")
                out.append("")
                if depth <= 0 and ("{" in cline or cline.rstrip().endswith(";")):
                    in_type = False
                continue
            if in_type:
                depth += cline.count("{") - cline.count("}")
                out.append("")
                if depth <= 0 and ("{" in cline or "}" in cline or cline.rstrip().endswith(";")):
                    in_type = False
            else:
                out.append(line)
        return "\n".join(out)
    out, in_test, depth = [], False, 0
    for line, cline in zip(lines, clean_lines):
        if not in_test and RS_CFG_TEST.match(line):
            in_test, depth = True, 0
            out.append("")
            continue
        if in_test:
            depth += cline.count("{") - cline.count("}")
            out.append("")
            if depth <= 0 and ("{" in cline or "}" in cline or cline.rstrip().endswith(";")):
                in_test = False
        else:
            out.append(line)
    return "\n".join(out)


ARGS = r"\((?:[^()]|\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))*\)"
GENERIC = r"(?:(?:::\s*)?<[^<>()\n;=]{1,80}>)?"
CHAIN = re.compile(r"(?:\s*\??\.\s*[A-Za-z_]\w*\s*" + GENERIC + r"\s*" + ARGS + r"){" + str(MIN_CALLS) + r",}")
CALL = re.compile(r"\??\.\s*([A-Za-z_]\w*)\s*" + GENERIC + r"\s*(" + ARGS + r")")

# ------------------------------------------------------------------------------- unit & fn extraction
RS_FN_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<pub>pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+"
    r"(?P<name>[A-Za-z_]\w*)(?:\s*<[^>{;]*>)?\s*\((?P<params>[^)]*)\)\s*(?:->\s*(?P<ret>[^{;]+))?\{",
    re.M,
)
RS_FN_HEAD_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<pub>pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(?P<name>[A-Za-z_]\w*)(?:\s*<[^>{;(]*>)?\s*\(",
    re.M,
)
RS_RENDER_IMPL_RE = re.compile(r"\bimpl(?:\s*<[^>{]*>)?\s+(?:Render|RenderOnce|IntoElement|Element)\s+for\s+(?P<ty>[A-Z]\w*)[^{]*\{")
RS_UI_RET = re.compile(r"\b(Div|AnyElement|IntoElement|Element|Button|Stateful|ToggleGroup|Svg|View|Response|Widget)\b")
JS_FN_RE = re.compile(
    r"^(?P<pub>export\s+(?:default\s+)?)?(?:async\s+)?function\s+(?P<name>[A-Za-z_]\w*)(?:\s*<[^>{;]*>)?\s*\((?P<params>[^)]*)\)",
    re.M,
)
JS_ARROW_RE = re.compile(
    r"^(?P<pub>export\s+)?const\s+(?P<name>[A-Za-z_]\w*)\s*(?::[^=\n]+)?=\s*"
    r"(?:(?:React\.)?(?:memo|forwardRef)\s*(?:<[^>]*>)?\s*\(\s*)?(?:async\s+)?(?:\((?P<params>[^)]*)\)|(?P<param>[A-Za-z_]\w*))\s*(?::[^=\n]+)?=>",
    re.M,
)
JS_DECL_RE = re.compile(
    r"^(?P<pub>export\s+(?:default\s+)?)?(?:"
    r"(?:const|let)\s+(?P<name>[A-Za-z_]\w*)(?=\s*[:=])"
    r"|(?<=default )\s*[\w.]+\s*(?:<[^>\n]*>)?\s*\(\s*(?:[\w.]+\s*\(\s*)?function\s+(?P<dname>[A-Z]\w*)"
    r"|class\s+(?P<cname>[A-Z]\w*)\s+extends\s+(?:React\.)?(?:Pure)?Component\b)",
    re.M,
)


def _js_statement_end(clean, pos):
    """End of a `const X ... = ...` statement: skip the type annotation up to the top-level `=`, then follow
    balanced ()[]{} groups (and generic `<...>` arguments) until the expression stops continuing."""
    n = len(clean)
    depth, i = 0, pos
    while i < n:  # find the assignment `=` at depth 0 (not `=>`, `==`)
        ch = clean[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "=" and depth == 0 and clean[i + 1:i + 2] not in ("=", ">") and clean[i - 1:i] not in "=!<>":
            break
        elif ch in ";\n" and depth == 0 and ch == ";":
            return -1
        i += 1
    else:
        return -1
    i += 1
    depth, opened = 0, False
    while i < n:
        ch = clean[i]
        if clean.startswith("=>", i):
            i += 2
            continue
        if ch in "([{":
            depth += 1
            opened = True
        elif ch in ")]}":
            depth -= 1
            if depth == 0 and opened:
                j = i + 1
                while j < n and clean[j] in " \t\r\n":
                    j += 1
                nxt = clean[j:j + 2]
                if not (nxt[:1] in ("(", ".", "?", ":", "<", ">", "[", "{", "&", "|") or nxt == "=>"):
                    return i + 1
        elif ch == ";" and depth == 0:
            return i
        elif ch == "\n" and depth == 0 and not opened and clean[pos:i].strip().split("=", 1)[-1].strip():
            return i  # `const X = styled.div` ... no call/body on this line: not a component declaration
        i += 1
    return n


PY_UNIT_RE = re.compile(r"^(?P<kind>def|class)\s+(?P<name>[A-Za-z_]\w*)\s*(?:\((?P<params>[^)]*)\))?\s*(?:->[^:]+)?:", re.M)
PY_UI_HINT = re.compile(r"\b(QWidget|QPushButton|QLabel|QVBoxLayout|QHBoxLayout|QDialog|QFrame|QLineEdit|tk\.|ttk\.)\b")

KEEP_TOKENS = {
    "return", "if", "else", "for", "while", "switch", "case", "const", "let", "var", "function", "export",
    "default", "async", "await", "null", "undefined", "true", "false", "fn", "pub", "match", "def", "class",
    "useState", "useEffect", "useRef", "useMemo", "useCallback", "useContext", "useReducer", "useId",
    "setTimeout", "clearTimeout", "setInterval", "clearInterval", "addEventListener", "removeEventListener",
    "stopPropagation", "preventDefault", "focus", "blur", "click", "target", "value", "key", "current",
    "onClick", "onChange", "onKeyDown", "onKeyUp", "onSubmit", "onBlur", "onFocus", "aria", "role", "type",
    "placeholder", "ref", "disabled", "readOnly", "autoFocus", "className", "class", "style", "children",
    "Escape", "Enter", "keydown", "keyup", "search", "dialog", "button", "submit", "CLS", "STR", "N",
    "div", "span", "input", "textarea", "select", "option", "form", "section", "header", "footer", "main",
    "nav", "aside", "h1", "h2", "h3", "h4", "p", "label", "ul", "ol", "li", "table", "thead", "tbody",
    "tr", "td", "th", "svg", "img", "a",
    "h_flex", "v_flex", "child", "when", "when_some", "on_click", "on_change", "on_hover", "cx", "listener",
    "QWidget", "QDialog", "QLineEdit", "QPushButton", "QLabel", "QVBoxLayout", "QHBoxLayout", "QFrame",
}
SEMANTIC_STRINGS = {"Escape", "Enter", "keydown", "keyup", "search", "dialog", "button", "submit", "text", "checkbox", "radio"}
BEHAV_RE = re.compile(
    r"\b(use[A-Z]\w*|setTimeout|setInterval|addEventListener|stopPropagation|preventDefault|"
    r"keydown|keyup|Escape|Enter|on_click|on_change|on_key_down|keyPressEvent|textChanged|returnPressed|QTimer|"
    r"on_action|on_mouse_down|on_key_down|track_focus|focus_handle)\b"
)
FOCUS_CALL_RE = re.compile(r"\.(focus|blur)\s*\(|\b(?:on)(Focus|Blur)\s*=")
# Vue / Svelte behaviour vocabulary (event modifiers keep their key: @keydown.esc, on:keydown, v-model)
SFC_BEHAV_RE = re.compile(
    r"(?<=\s)(?:(@|v-on:|on:)(\w+)((?:\.\w+)*)(?=\s*=)|(v-model|bind:value|bind:this)|"
    r"\b(watch|watchEffect|computed|defineEmits|defineProps|emit|onMounted|onUnmounted|onMount|onDestroy|"
    r"createEventDispatcher|nextTick)\b\s*\()"
)


def _sfc_behav_token(m):
    if m.group(1):
        mods = m.group(3) or ""
        key = {"esc": "Escape", "escape": "Escape", "enter": "Enter"}.get(mods.lstrip(".").split(".")[0].lower(), "")
        return f"on:{m.group(2)}" + (f".{key}" if key else "")
    return m.group(4) or m.group(5)
CLASS_ATTR_RE = re.compile(r"""\b(?:className|class)\s*=\s*(?:"([^"\n]+)"|'([^'\n]+)'|\{([^}\n]+)\})""")
CLASS_STR_RE = re.compile(r"""["'`]([^"'`\n]+)["'`]""")
CLASS_FN_RE = re.compile(r"\b(?:cn|clsx|classnames|classNames|twMerge|twJoin|cva)\s*\(")
CSS_MODULE_RE = re.compile(r"""\b(?:styles|classes|css)\s*(?:\.\s*(\w+)|\[\s*["'](\w[\w-]*)["']\s*\])""")
JSX_TAG_RE = re.compile(r"(?<![A-Za-z0-9_$\]])<([A-Za-z]\w*)(?=[\s/>])")
JSX_RETURN_ROOT_RE = re.compile(r"(?:\breturn\s*\(?|=>\s*\()\s*<([A-Z]\w*)(?=[\s/>])")
UI_ROLE_SUFFIX_RE = re.compile(
    r"(Modal|Dialog|Input|SearchInput|Card|Toolbar|Panel|Form|Table|List|Drawer|Dropdown|Select|Filter|Header|Sidebar|Popover|Banner|Sheet|Tabs?)$"
)
CALLBACK_IDENT_RE = re.compile(r"\b((?:on|handle)[A-Z]\w*|on_[a-z]\w*)\b")


@dataclass
class Unit:
    rel: str
    line: int
    end_line: int
    name: str
    lang: str
    is_exported: bool
    nontrivial_lines: int
    shingles: Counter
    classes: Counter
    tags: Counter
    behav: set
    props: set
    callbacks: set
    root_wrapper: str
    wrappers: set
    raw_shell_cues: set
    role_suffix: str
    body_sample: str = field(default="")


def _find_balanced_end(clean, open_pos, open_ch="{", close_ch="}"):
    depth = 0
    n = len(clean)
    for i in range(open_pos, n):
        ch = clean[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
    return n


def _count_nontrivial(body, suffix):
    clean = blank_strings(body, suffix)
    count = 0
    for raw in clean.splitlines():
        s = raw.strip()
        if not s or s.startswith(("import ", "from ", "use ")):
            continue
        if re.fullmatch(r"[\s{}()\[\];,<>/:.]+", s):
            continue
        count += 1
    return count


def _extract_props(params_text):
    if not params_text:
        return set()
    # Handle destructured React props `{ open, onClose, onConfirm = noop }: Props`
    inner = params_text.strip()
    m = re.search(r"\{([^}]*)\}", inner)
    if m:
        inner = m.group(1)
    props = set()
    for part in inner.split(","):
        p = part.strip().lstrip(".")
        if not p:
            continue
        p = p.split("=")[0].split(":")[0].strip()
        p = re.sub(r"^(?:mut|ref)\s+", "", p)
        if re.fullmatch(r"[A-Za-z]\w*", p) and p not in ("self", "cls", "props", "ctx", "cx", "window"):
            props.add(p)
    return props


def _fingerprint_unit(rel, line, end_line, name, lang, is_exported, body, params_text, suffix):
    nt_lines = _count_nontrivial(body, suffix)
    # Channel 2: preserved style/chrome token bag
    classes = Counter()
    if lang == "js":
        for m in CLASS_ATTR_RE.finditer(body):
            raw_cls = m.group(1) or m.group(2)
            if raw_cls:
                classes.update(t for t in raw_cls.split() if t)
            elif m.group(3) and not CLASS_FN_RE.search(m.group(3)):
                expr = m.group(3)
                for sm in CLASS_STR_RE.finditer(expr):
                    classes.update(t for t in sm.group(1).split() if t and not t.startswith("${"))
                # CSS modules: className={styles.card} -> a class token per module key
                classes.update("mod:" + (k1 or k2) for k1, k2 in CSS_MODULE_RE.findall(expr))
        # cn()/clsx()/cva()/twMerge() calls, also when they span several lines
        for m in CLASS_FN_RE.finditer(body):
            end = _find_balanced_end(body, m.end() - 1, "(", ")")
            for sm in CLASS_STR_RE.finditer(body[m.end():end]):
                classes.update(t for t in sm.group(1).split() if t and not t.startswith("${"))
        if suffix in (".vue", ".svelte"):
            classes = Counter()  # scoped SFC class names never match across files; compare template + script only
    elif lang == "rs":
        classes.update(STYLE.findall(body))
    elif lang == "py":
        classes.update(re.findall(r"\.(setStyleSheet|setContentsMargins|setSpacing|setFixedHeight|setFixedWidth|setPlaceholderText|addWidget|addLayout)\b", body))

    # Channel 3: UI element / tag skeleton
    tags = Counter()
    if lang == "js":
        tags.update(JSX_TAG_RE.findall(body))
    elif lang == "rs":
        tags.update(re.findall(r"\b(div|h_flex|v_flex|svg|img|(?:kit|visual)::\w+)\s*\(", body))
    elif lang == "py":
        tags.update(re.findall(r"\b(Q[A-Z]\w*|ttk\.\w+)\b", body))

    # Channel 1: role-preserving normalized 3-token shingles
    # Keep semantic string literals (Escape, Enter, keydown, search, dialog) while blanking arbitrary text and classes.
    no_comments = blank_strings(body, suffix, preserve_class=False)
    def _clean_tok(m):
        g = m.lastgroup
        if g in ("jxc", "bc", "hc", "lc"):
            return " "
        if g == "cls":
            return "className=CLS"
        if g in ("dq", "sq", "tpl", "raw", "tq"):
            s = m.group(0)
            val = s[3:-3] if g == "tq" else (s[s.find('"') + 1:s.rfind('"')] if g == "raw" else s[1:-1])
            return val if val in SEMANTIC_STRINGS else "STR"
        return "STR"
    tok_re = _JS_TOKENS_KEEP_CLASS if lang == "js" else (_RS_TOKENS if lang == "rs" else _PY_TOKENS)
    cleaned = tok_re.sub(_clean_tok, body)

    # Channel 4: behavioral / hook / event primitives, read from the string/class-blanked body so Tailwind
    # text (`focus-visible:`, `backdrop-blur`) never counts; focus/blur only as calls or handlers.
    behav = set(BEHAV_RE.findall(cleaned)) | {m.group(1) or m.group(2).lower() for m in FOCUS_CALL_RE.finditer(cleaned)}
    if lang == "js" and suffix in (".vue", ".svelte"):
        behav |= {_sfc_behav_token(m) for m in SFC_BEHAV_RE.finditer(body)}
    behav.discard(name)  # a hook's own name is not shared behaviour
    raw_toks =re.findall(r"[A-Za-z_]\w*|\d+|[{}()<>=\[\].,;?!&|]+", cleaned)
    own_props_types = {name, f"{name}Props"}
    norm_toks = []
    for t in raw_toks:
        if t in KEEP_TOKENS or not t[0].isalpha():
            norm_toks.append("N" if t.isdigit() else t)
        elif t in own_props_types or t.endswith("Props"):
            norm_toks.append("SELF" if t == name else "PROPS")
        elif re.match(r"^(?:on|handle)[A-Z]|^on_[a-z]", t):
            norm_toks.append("CB")
        elif t[0].isupper():
            norm_toks.append(t)  # preserve shared child components/icons like Search, X, ModalShell
        else:
            norm_toks.append("ID")
    shingles = Counter(tuple(norm_toks[i:i + 3]) for i in range(max(0, len(norm_toks) - 2)))

    # Composition metadata: root wrapper & non-self-closing PascalCase wrappers
    root_wrapper = ""
    wrappers = set()
    if lang == "js":
        rm = JSX_RETURN_ROOT_RE.search(no_comments)
        if rm and f"</{rm.group(1)}>" in no_comments:
            root_wrapper = rm.group(1)
        for tag in set(tags):
            if tag[0].isupper() and f"</{tag}>" in no_comments:
                wrappers.add(tag)

    # Raw shell cues (for smart `created` reuse checks)
    raw_shell_cues = set()
    if "fixed" in classes and "inset-0" in classes:
        raw_shell_cues.add("overlay_backdrop")
    if ("keydown" in behav and "Escape" in behav) or "on:keydown.Escape" in behav or "on:keyup.Escape" in behav:
        raw_shell_cues.add("escape_keydown")
    if "input" in tags and ("search" in cleaned or "setTimeout" in behav):
        raw_shell_cues.add("search_input")
    if "stopPropagation" in behav and ("border-b" in classes or "border-t" in classes):
        raw_shell_cues.add("modal_chrome")

    props = _extract_props(params_text)
    callbacks = set(CALLBACK_IDENT_RE.findall(body)) - {"onClick", "onChange", "onKeyDown", "onKeyUp", "onSubmit", "onBlur", "onFocus", "on_click", "on_change"}
    role_m = UI_ROLE_SUFFIX_RE.search(name)
    role_suffix = role_m.group(1).lower() if role_m else ""
    return Unit(
        rel=rel, line=line, end_line=end_line, name=name, lang=lang, is_exported=is_exported,
        nontrivial_lines=nt_lines, shingles=shingles, classes=classes, tags=tags, behav=behav,
        props=props, callbacks=callbacks, root_wrapper=root_wrapper, wrappers=wrappers,
        raw_shell_cues=raw_shell_cues, role_suffix=role_suffix, body_sample=no_comments[:400],
    )


def _py_block_body(text, lines, match_start):
    start_line = text.count("\n", 0, match_start) + 1
    end_line = start_line
    for idx in range(start_line, len(lines)):
        raw = lines[idx]
        if raw.strip() and not raw.lstrip().startswith("#") and not raw[0].isspace():
            break
        end_line = idx + 1
    return start_line, end_line, "\n".join(lines[start_line - 1:end_line])


def _extract_units(rel, text, clean, suffix):
    units = []
    suf = suffix.lower()
    if suf in (".vue", ".svelte"):
        lines_n = text.count("\n") + 1
        stem = Path(rel).stem
        units.append(_fingerprint_unit(rel, 1, lines_n, stem, "js", True, text, "", suf))
        return units
    if suf in JS_FAMILY:
        seen_starts = set()
        for rx in (JS_FN_RE, JS_ARROW_RE):
            for m in rx.finditer(text):
                name = m.group("name")
                if not (name[0].isupper() or re.match(r"^use[A-Z]", name)):
                    continue
                if m.start() in seen_starts:
                    continue
                seen_starts.add(m.start())
                # Find opening brace or paren after match
                pos = m.end()
                while pos < len(clean) and clean[pos] not in "{(\n;":
                    pos += 1
                if pos >= len(clean) or clean[pos] not in "{(":
                    # Scan slightly further for multi-line return type annotations before `{`
                    brace = clean.find("{", m.end(), min(len(clean), m.end() + 200))
                    if brace == -1:
                        continue
                    pos = brace
                open_ch = clean[pos]
                close_ch = "}" if open_ch == "{" else ")"
                end_pos = _find_balanced_end(clean, pos, open_ch, close_ch)
                body = text[m.start():end_pos]
                if not name.startswith("use") and not UI_MARKUP.search(body):
                    continue
                line = text.count("\n", 0, m.start()) + 1
                end_line = text.count("\n", 0, end_pos) + 1
                params = m.groupdict().get("params") or m.groupdict().get("param") or ""
                units.append(_fingerprint_unit(rel, line, end_line, name, "js", bool(m.group("pub")), body, params, suf))
        # Wrapped / typed declarations the two regexes miss: forwardRef<A, B & {...}>(...), memo(forwardRef(...)),
        # observer(...), generic arrows <T,>(...), React.FC<{ cb: () => void }>, export default memo(function X(...)),
        # and class components. The whole declaration statement is the unit.
        for m in JS_DECL_RE.finditer(clean):
            if m.start() in seen_starts:
                continue
            name = m.group("name") or m.group("dname") or m.group("cname")
            if not (name[0].isupper() or re.match(r"^use[A-Z]", name)):
                continue
            if m.group("cname"):
                brace = clean.find("{", m.end())
                end_pos = _find_balanced_end(clean, brace, "{", "}") if brace != -1 else -1
            elif m.group("dname"):
                paren = clean.find("(", m.start() + len(m.group("pub") or ""))
                end_pos = _find_balanced_end(clean, paren, "(", ")")
            else:
                end_pos = _js_statement_end(clean, m.end())
            if end_pos <= m.end():
                continue
            seen_starts.add(m.start())
            body = text[m.start():end_pos]
            if not name.startswith("use") and not UI_MARKUP.search(body):
                continue
            pm = re.search(r"\(\s*(\{[^{}]*\})", clean[m.end():end_pos]) or re.search(
                r"\{([^{}]*)\}\s*=\s*this\.props", clean[m.end():end_pos])
            params = text[m.end() + pm.start(1):m.end() + pm.end(1)] if pm else ""
            line = text.count("\n", 0, m.start()) + 1
            end_line = text.count("\n", 0, end_pos) + 1
            units.append(_fingerprint_unit(rel, line, end_line, name, "js", bool(m.group("pub")), body, params, suf))
        return units
    if suf == ".rs":
        # GPUI views: `impl Render/RenderOnce for X { fn render(...) }` -> one unit named after X
        render_impls = []
        for im in RS_RENDER_IMPL_RE.finditer(clean):
            brace = clean.find("{", im.end() - 1)
            render_impls.append((brace, _find_balanced_end(clean, brace, "{", "}"), im.group("ty")))
        for m in RS_FN_HEAD_RE.finditer(clean):
            p_open = m.end() - 1
            p_close = _find_balanced_end(clean, p_open, "(", ")")  # closure params: impl Fn(&mut Window) + 'static
            brace = p_close
            while brace < len(clean) and clean[brace] not in "{;":
                brace += 1
            if brace >= len(clean) or clean[brace] != "{":
                continue
            ret = clean[p_close:brace]
            name, is_pub = m.group("name"), bool(m.group("pub"))
            if m.group("indent"):
                owner = next((ty for s0, e0, ty in render_impls if s0 < m.start() < e0), None)
                if owner and name == "render":
                    name, is_pub = owner, True
                elif not RS_UI_RET.search(ret):
                    continue  # methods and nested fns that build no UI
            end_pos = _find_balanced_end(clean, brace, "{", "}")
            body = text[m.start():end_pos]
            if not (RS_UI_RET.search(ret) or UI_GENERAL.search(body)):
                continue
            line = text.count("\n", 0, m.start()) + 1
            end_line = text.count("\n", 0, end_pos) + 1
            units.append(_fingerprint_unit(rel, line, end_line, name, "rs", is_pub, body, text[p_open + 1:p_close - 1], suf))
        return units
    if suf == ".py":
        lines = text.splitlines()
        for m in PY_UNIT_RE.finditer(text):
            start_line, end_line, body = _py_block_body(text, lines, m.start())
            if not PY_UI_HINT.search(body):
                continue
            name = m.group("name")
            units.append(_fingerprint_unit(rel, start_line, end_line, name, "py", not name.startswith("_"), body, m.group("params") or "", suf))
    return units


class Index:
    def __init__(self, repo, arch, suffixes=None):
        self.repo, self.arch = Path(repo), arch
        self.chains = []   # (rel, line, [(name, args)])
        self.end = {}      # (rel, line) -> last line of the piece
        self.lines = {}    # rel -> [(line, normalized)]
        self.fns = []      # (rel, line, name, is_public, is_free_ui_helper)
        self.units = []    # [Unit]
        for rel in code_files(repo, arch, suffixes):
            path = self.repo / rel
            try:
                if path.stat().st_size > 1_500_000:
                    continue
                text = production(path.read_text(encoding="utf-8", errors="replace"), path.suffix)
            except OSError:
                continue
            flat_chains = blank_strings(text, path.suffix, preserve_class=False)
            flat_blocks = blank_strings(text, path.suffix, preserve_class=True)
            masked = mask_for_braces(text, path.suffix)
            self.collect(rel, flat_chains, flat_chains, 0)
            self.lines[rel] = normalized_lines(flat_blocks)
            self.collect_fns(rel, text, masked, path.suffix)
            self.units.extend(_extract_units(rel, text, masked, path.suffix))

    def collect_fns(self, rel, text, clean, suffix):
        suf = suffix.lower()
        if suf == ".rs":
            for m in RS_FN_RE.finditer(text):
                indent, pub, name = m.group("indent"), bool(m.group("pub")), m.group("name")
                params, ret = m.group("params") or "", (m.group("ret") or "").strip()
                free_ui = (not indent and "self" not in params and bool(RS_UI_RET.search(ret)))
                line = text.count("\n", 0, m.start()) + 1
                self.fns.append((rel, line, name, pub, free_ui))
        elif suf in JS_FAMILY:
            for rx in (JS_FN_RE, JS_ARROW_RE):
                for m in rx.finditer(text):
                    name, pub = m.group("name"), bool(m.group("pub"))
                    pos = clean.find("{", m.end(), min(len(clean), m.end() + 200))
                    if pos == -1:
                        pos = clean.find("(", m.end(), min(len(clean), m.end() + 80))
                    if pos == -1:
                        continue
                    end_pos = _find_balanced_end(clean, pos, clean[pos], "}" if clean[pos] == "{" else ")")
                    body = text[m.start():end_pos]
                    free_ui = bool(UI_MARKUP.search(body))
                    line = text.count("\n", 0, m.start()) + 1
                    self.fns.append((rel, line, name, pub, free_ui))
        elif suf == ".py":
            lines = text.splitlines()
            for m in PY_UNIT_RE.finditer(text):
                if m.group("kind") != "def":
                    continue
                name = m.group("name")
                start_line, _, body = _py_block_body(text, lines, m.start())
                free_ui = bool(PY_UI_HINT.search(body))
                self.fns.append((rel, start_line, name, not name.startswith("_"), free_ui))

    def collect(self, rel, flat, text, offset, depth=0):
        """Chains of the text; content (.child, .on_click...) cuts the chain and is scanned inside."""
        if depth > 6:
            return
        for m in CHAIN.finditer(text):
            segment = []
            for call in CALL.finditer(m.group(0)):
                name, args = call.group(1), call.group(2)
                start = offset + m.start() + call.start()
                if name in BREAKERS:
                    self.keep(rel, flat, segment)
                    segment = []
                    self.collect(rel, flat, args[1:-1], offset + m.start() + call.start(2) + 1, depth + 1)
                else:
                    segment.append((name, re.sub(r"\s+", "", args[1:-1]), start))
            self.keep(rel, flat, segment)

    def keep(self, rel, flat, segment):
        trimmed = segment  # routine layout at the edges is not part of the piece, if a chain remains
        while trimmed and common_chain([trimmed[0][:2]], self.arch):
            trimmed = trimmed[1:]
        while trimmed and common_chain([trimmed[-1][:2]], self.arch):
            trimmed = trimmed[:-1]
        segment = trimmed if len(trimmed) >= MIN_CALLS else segment
        if len(segment) >= MIN_CALLS:
            line = flat.count("\n", 0, segment[0][2]) + 1
            self.end[(rel, line)] = max(self.end.get((rel, line), line), flat.count("\n", 0, segment[-1][2]) + 1)
            self.chains.append((rel, line, [(n, a) for n, a, _ in segment]))


IMPORT_LINE_RE = re.compile(r"(?:import|export)\b.*\bfrom\b|import\s+[\w\"'*{]|use\s+[\w:]+.*;$|from\s+\S+\s+import\b")
IMPORT_OPEN_RE = re.compile(r"(?:import|export)\s+(?:type\s+)?\{[^}]*$|use\s+[\w:]+::\{[^}]*$|from\s+\S+\s+import\s+\($")


def normalized_lines(flat):
    out = []
    in_import = False
    for number, raw in enumerate(flat.splitlines(), 1):
        line = re.sub(r"\s+", " ", raw.strip())
        # import/use lists are not code to extract: skip single-line and multi-line `import { ... } from`
        if in_import:
            if line.startswith("}") or line.endswith((";", ")")) or " from " in f" {line} ":
                in_import = False
            continue
        if IMPORT_OPEN_RE.match(line):
            in_import = True
            continue
        if IMPORT_LINE_RE.match(line):
            continue
        if not line or line.startswith(("//", "#", "/*", "*", "--")) or len(line) < 12:
            continue
        if re.fullmatch(r"[\s{}()\[\];,]*|(\} )?else \{|return;?|Ok\(\(\)\)|None|\.into\(\),?|\.into_any_element\(\),?", line):
            continue
        norm = re.sub(r"\b\d+(\.\d+)?\b", "N", line)
        if re.fullmatch(r'[\s()\[\]{},"SN:;]+', norm):
            continue  # pure literal tuple/table row: ("S", "S", "S"),
        out.append((number, norm))
    return out


# ------------------------------------------------------------------------------ patterns
def show(calls):
    return "".join(f".{n}({a})" for n, a in calls)


def common_chain(calls, arch):
    return all(n in arch.common and TRIVIAL_ARG.match(a) for n, a in calls)


def chain_patterns(index):
    """Chains of MIN_CALLS+ equal calls in MIN_FILES+ files; extended as far as they all agree."""
    windows = defaultdict(list)
    for chain_id, (_, _, calls) in enumerate(index.chains):
        for start in range(len(calls) - MIN_CALLS + 1):
            windows[tuple(calls[start:start + MIN_CALLS])].append((chain_id, start))
    patterns, seen = [], set()
    for key, spots in sorted(windows.items(), key=lambda kv: -len(kv[1])):
        files = {index.chains[c][0] for c, _ in spots}
        if len(spots) < MIN_COUNT or len(files) < MIN_FILES or common_chain(key, index.arch):
            continue
        begin, end = 0, MIN_CALLS
        while all(s - begin - 1 >= 0 for _, s in spots) and len({index.chains[c][2][s - begin - 1] for c, s in spots}) == 1:
            begin += 1
        while all(s + end < len(index.chains[c][2]) for c, s in spots) and len({index.chains[c][2][s + end] for c, s in spots}) == 1:
            end += 1
        full = tuple(index.chains[spots[0][0]][2][spots[0][1] - begin: spots[0][1] + end])
        where = tuple(sorted((index.chains[c][0], index.chains[c][1]) for c, _ in spots))
        if (full, where) in seen or any(set(where) <= set(w) and show(full) in show(f) for f, w in seen):
            continue
        seen.add((full, where))
        patterns.append(("chain", show(full), where, show(full)))
    return patterns


def block_patterns(index):
    """Blocks of MIN_BLOCK+ equal lines (texts and numbers aside) in two or more places."""
    windows = defaultdict(list)
    for rel, lines in index.lines.items():
        for i in range(len(lines) - MIN_BLOCK + 1):
            key = hashlib.sha1("\n".join(t for _, t in lines[i:i + MIN_BLOCK]).encode()).digest()
            windows[key].append((rel, i))
    covered, patterns = set(), []
    # Process windows appearing in MORE files/places first so a 2-file prefix never shadows a 3+-file sub-block.
    ordered = sorted(windows.items(), key=lambda kv: (-len({r for r, _ in kv[1]}), -len(kv[1])))
    for _, spots in ordered:
        spots = [(r, i) for r, i in spots if all((r, i + k) not in covered for k in range(MIN_BLOCK))]
        distinct = []
        for rel, i in spots:  # in one file, overlapping blocks are the same block
            if all(rel != r or abs(i - j) >= MIN_BLOCK for r, j in distinct):
                distinct.append((rel, i))
        if len(distinct) < 2:
            continue
        size = MIN_BLOCK
        while all(i + size < len(index.lines[r]) and (r, i + size) not in covered for r, i in distinct) and len(
                {index.lines[r][i + size][1] for r, i in distinct}) == 1:
            size += 1
        sample = [t for _, t in index.lines[distinct[0][0]][distinct[0][1]: distinct[0][1] + size]]
        if sum(common_line(t, index.arch) for t in sample) >= 0.8 * size:
            continue  # only framework idiom (layout chains)
        if sum(signature_line(t) for t in sample) >= 0.7 * size:
            continue  # typed parameter list or struct field declarations
        if single_call_block(sample):
            continue  # a single function/method call wrapped over multiple lines
        for r, i in distinct:
            covered.update((r, i + k) for k in range(size))
            index.end[(r, index.lines[r][i][0])] = index.lines[r][min(i + size, len(index.lines[r])) - 1][0]
        where = tuple(sorted((r, index.lines[r][i][0]) for r, i in distinct))
        patterns.append(("block", f"{size} lines: {sample[0][:60]}", where, "\n".join(sample)))
    return patterns


PARAM_DECL = re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:readonly\s+)?(?:mut\s+)?[A-Za-z_]\w*\s*\??:\s*([^=]+?),?\s*;?$")
PARAM_TYPE = re.compile(
    r"^(?:&(?:'\w+\s+)?(?:mut\s+)?|\*\s*(?:const|mut)\s+|readonly\s+)?"
    r"(?:(?:impl|dyn|fn|keyof|typeof)\b|[A-Z]\w*|[a-z_]\w*::[A-Za-z_]\w*|[\[(]|"
    r'"S"|\'[^\']*\''
    r"|(?:usize|isize|bool|str|char|f32|f64|[iu](?:8|16|32|64|128)|"
    r"string|number|boolean|void|any|unknown|never|null|undefined|int|float|bytes|dict|list|set|tuple)\b)"
)
CALL_HEADER = re.compile(r"^(?:(?:let(?:\s+mut)?|const|var)\s+[^;{}]*=\s*|return\s+)?[\w.:?<>]+\s*\($")
SIMPLE_ARG = re.compile(
    r"""^(?:(?:&\s*(?:mut\s+)?)?[\w."']+(?:\(\))?|[\w.]+\s*[:=]\s*(?:&\s*(?:mut\s+)?)?[\w."']+(?:\(\))?)[,)]*;?$"""
)


def signature_line(text):
    m = PARAM_DECL.match(text)
    return bool(m and PARAM_TYPE.match(m.group(1).strip()))


def single_call_block(sample):
    """A single call wrapped over several lines is reuse, not a copied sequence."""
    if not sample or not CALL_HEADER.match(sample[0]):
        return False
    args = sample[1:]
    return bool(args) and sum(bool(SIMPLE_ARG.match(t)) for t in args) >= 0.8 * len(args)


def common_line(text, arch):
    calls = CALL.findall(text)
    return bool(calls) and re.fullmatch(r"(\s*\??\.\s*\w+\s*(?:(?:::\s*)?<[^<>()\n;=]{1,80}>)?\s*\(.*\)\s*)+[,;)]*", text) is not None and all(
        n in arch.common and TRIVIAL_ARG.match(re.sub(r"\s+", "", a[1:-1])) for n, a in calls)


def destination(where, code, arch, force_ui=False):
    """The layer the extracted piece can live in: every place imports it, none imports upward."""
    files = {rel for rel, _ in where}
    if len(files) == 1:
        return "a function in the same file (one place uses it)", None
    if not arch.defined:
        return None, None
    layers = {arch.layer(rel) for rel in files} - {None}
    ui = force_ui or is_ui_code(code, files)
    if not layers:
        return arch.destinations.get(max(arch.destinations), ""), None
    low = min(layers)
    visual = arch.visual_layer()
    if ui:
        if visual is None or low < visual:
            return None, (f"uses UI and appears in layer {low}, which cannot import layer "
                          f"{visual}: split off the part without UI first")
        return arch.destinations[visual], None
    non_ui = [n for n in arch.destinations if n <= low and (visual is None or n < visual)]
    if non_ui:
        return arch.destinations[max(non_ui)], None
    target = max((n for n in arch.destinations if n <= low), default=min(arch.destinations))
    return arch.destinations[target], None


def helper_patterns(index, existing):
    """Screen-local UI helper functions that duplicate a shared component function or another screen's helper."""
    already = {(r, n) for _, _, where, _ in existing for r, n in where}
    visual = index.arch.visual_layer()
    shared_max = visual if visual is not None else 3
    shared = {}
    local = defaultdict(list)
    for rel, line, name, pub, free_ui in index.fns:
        layer = index.arch.layer(rel)
        if pub and free_ui and layer is not None and layer <= shared_max:
            shared.setdefault(name, (rel, line))
        elif not pub and free_ui and (layer is None or layer > shared_max) and index.arch.is_component(rel) and (rel, line) not in already:
            local[name].append((rel, line))
    out = []
    for name, spots in sorted(local.items()):
        where = tuple(sorted(spots))
        if name in shared:
            s_rel, s_line = shared[name]
            for rel, line in where:
                index.end[(rel, line)] = line
            out.append(("helper", f"fn {name} (already in {s_rel}:{s_line})", (*where, (s_rel, s_line)), "div()"))
        elif len({r for r, _ in where}) >= 2:
            for rel, line in where:
                index.end[(rel, line)] = line
            out.append(("helper", f"fn {name}", where, "div()"))
    return out


# ----------------------------------------------------------------- near-duplicate component detection
def _dice(c1, c2):
    if not c1 and not c2:
        return 0.0
    inter = sum((c1 & c2).values())
    return 2.0 * inter / (sum(c1.values()) + sum(c2.values()))


def _jaccard(s1, s2):
    if not s1 and not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def _score_pair(u1, u2):
    """4-channel structural similarity with Critic anti-FP & composition-discount guardrails."""
    if u1.lang != u2.lang:
        return 0.0, {}
    lo, hi = min(u1.nontrivial_lines, u2.nontrivial_lines), max(u1.nontrivial_lines, u2.nontrivial_lines)
    if hi == 0:
        return 0.0, {}
    if (lo / hi) < 0.6 and lo >= MIN_SMALL_UNIT_LINES:
        # Subset variant: the small unit is the large one minus an optional feature (the classic "add a prop").
        small, large = (u1, u2) if u1.nontrivial_lines <= u2.nontrivial_lines else (u2, u1)
        total = sum(small.shingles.values())
        contain = sum((small.shingles & large.shingles).values()) / total if total else 0.0
        cls_ok = not small.classes or sum((small.classes & large.classes).values()) >= 0.8 * sum(small.classes.values())
        if contain >= MIN_CONTAINMENT and small.behav <= large.behav and cls_ok and (small.behav or small.classes):
            return contain, {"shingle": contain, "subset": small.name}
    if (lo / hi) < 0.45:
        return 0.0, {}
    s_shingle = _dice(u1.shingles, u2.shingles)
    s_class = _dice(u1.classes, u2.classes)
    s_tag = _dice(u1.tags, u2.tags)
    s_behav = _jaccard(u1.behav, u2.behav)
    detail = {"shingle": s_shingle, "class": s_class, "tag": s_tag, "behav": s_behav}
    has_tags = bool(u1.tags or u2.tags)

    if not has_tags and not (u1.classes or u2.classes):
        # Non-UI hooks: no markup channels, so the score is shape + behaviour only.
        score = 0.75 * s_shingle + 0.25 * s_behav
    elif u1.classes and u2.classes:
        if s_class < MIN_CLASS_SIM:
            # Soft class gate: a restyled copy of the same component (different Tailwind/BEM/CSS-module classes)
            # is still one component with a `variant`/`className` prop; only report it when the rest is near-identical.
            # Coincidental shape with no shared behaviour (PricingCard vs ErrorBanner) stays separate.
            score = 0.55 * s_shingle + 0.25 * s_tag + 0.20 * s_behav
            detail["behaviour_score"] = score
            if s_shingle < 0.75 or s_behav < 0.6 or not (u1.behav & u2.behav - {"useState"}) or score < MIN_COMP_SCORE:
                return 0.0, detail
            detail["style_variant"] = True
            return score, detail
        score = 0.45 * s_shingle + 0.25 * s_class + 0.15 * s_tag + 0.15 * s_behav
    elif u1.classes or u2.classes:
        score = (0.45 * s_shingle + 0.15 * s_tag + 0.15 * s_behav) / 0.75
    elif u1.behav or u2.behav:
        score = 0.55 * s_shingle + 0.25 * s_tag + 0.20 * s_behav
    else:
        score = 0.70 * s_shingle + 0.30 * s_tag

    # Composition discount: if both units already wrap the same shared shell component (e.g., <ModalShell>),
    # discount similarity unless both re-implement raw shell chrome primitives, or the bodies inside the shared
    # wrappers are themselves near-identical copies (copy-pasted shadcn AlertDialog confirm dialogs).
    shared_root = bool(u1.root_wrapper and u1.root_wrapper == u2.root_wrapper)
    shared_shell_wrapper = bool((u1.wrappers & u2.wrappers) and not (u1.raw_shell_cues & u2.raw_shell_cues))
    # 3+ shared composite parts (AlertDialog, AlertDialogContent, AlertDialogFooter...) copied verbatim are one
    # composite re-implemented; a single shared <ModalShell> wrapper means reuse already happened.
    copied_composite = s_shingle >= 0.85 and u1.wrappers == u2.wrappers and len(u1.wrappers) >= 3
    if not copied_composite and (shared_root or (shared_shell_wrapper and (u1.root_wrapper in u2.wrappers or u2.root_wrapper in u1.wrappers))):
        score = max(0.0, score - COMPOSITION_DISCOUNT)

    return score, detail


def _param_diff(u1, u2):
    """Summarizes what chrome/hooks are shared vs what callbacks/props/elements vary."""
    shared_parts = []
    shared_behav = sorted(u1.behav & u2.behav)
    if shared_behav:
        shared_parts.append("hooks/events: " + ", ".join(shared_behav[:4]))
    shared_cues = sorted(u1.raw_shell_cues & u2.raw_shell_cues)
    if shared_cues:
        shared_parts.append("shell: " + ", ".join(shared_cues[:3]))
    if not shared_parts:
        shared_tags = [t for t, _ in (u1.tags & u2.tags).most_common(4)]
        if shared_tags:
            shared_parts.append("elements: " + ", ".join(shared_tags))

    varies_parts = []
    # Behaviour difference first, mapped to the optional prop that would absorb it.
    b_only = [(t, u1.name) for t in sorted(u1.behav - u2.behav)] + [(t, u2.name) for t in sorted(u2.behav - u1.behav)]
    b_only = [(t, n) for t, n in b_only if t not in ("useState", "useRef", "useMemo", "useCallback")]
    if b_only:
        varies_parts.append("behaviour: " + ", ".join(
            f"{t} only in {n}" + (f" -> {BEHAV_PROP_HINT[t]}" if t in BEHAV_PROP_HINT else "") for t, n in b_only[:3]))
    # Callbacks: declared props only (local handlers are implementation), entity names normalized out
    # (handleUserClear vs handleOrderClear are the same handler).
    def _cbs(u):
        cbs = {c for c in u.callbacks if c in u.props} if u.props else set(u.callbacks)
        ent = UI_ROLE_SUFFIX_RE.sub("", u.name)
        return {c.replace(ent, "") if ent and len(ent) > 2 else c for c in cbs}
    c1, c2 = _cbs(u1), _cbs(u2)
    cb_diff = sorted(c1 ^ c2)
    if cb_diff:
        varies_parts.append("callbacks: " + ", ".join(f"{c}?" for c in cb_diff[:4]))
    prop_diff = sorted(((u1.props - u2.props) | (u2.props - u1.props)) - set(u1.callbacks | u2.callbacks))
    if prop_diff:
        varies_parts.append("props: " + ", ".join(prop_diff[:4]))
    tag_diff = sorted((set(u1.tags) - set(u2.tags)) | (set(u2.tags) - set(u1.tags)))
    if tag_diff:
        varies_parts.append("slots: " + ", ".join(tag_diff[:3]))
    return ("; ".join(shared_parts) or "structure/styles"), ("; ".join(varies_parts) or "identifiers/copy")


def _unit_touches(unit, touched):
    if touched is None:
        return True
    if unit.rel not in touched:
        return False
    lines = touched[unit.rel]
    return lines is None or any(n in lines for n in range(unit.line, unit.end_line + 1))


def _candidate_pairs(units, touched):
    """O(K*N) when touched is set (Stop hook); pre-bucketed inverted index for --all."""
    by_lang = defaultdict(list)
    for u in units:
        if u.nontrivial_lines >= MIN_SMALL_UNIT_LINES:
            by_lang[u.lang].append(u)

    pairs = []
    for lang_units in by_lang.values():
        n = len(lang_units)
        if n < 2:
            continue
        if touched is not None:
            touched_idx = [i for i, u in enumerate(lang_units) if _unit_touches(u, touched)]
            seen = set()
            for i in touched_idx:
                for j in range(n):
                    if i == j:
                        continue
                    pair = (min(i, j), max(i, j))
                    if pair in seen:
                        continue
                    seen.add(pair)
                    pairs.append((lang_units[pair[0]], lang_units[pair[1]]))
        else:
            # Pre-bucket by structural keys to avoid blind O(N^2) on huge repos.
            buckets = defaultdict(list)
            # Rare shingles (IDF-style) as extra keys: two near-copies share their rarest shingles, so they still
            # collide when every chrome bucket (`div`, `flex`, useState) is too generic and gets dropped.
            df = Counter(sh for u in lang_units for sh in u.shingles)
            for idx, u in enumerate(lang_units):
                keys = {("sh", sh) for sh in sorted((sh for sh in u.shingles if df[sh] >= 2), key=lambda sh: (df[sh], sh))[:6]}
                if u.role_suffix:
                    keys.add(("role", u.role_suffix))
                for b in u.behav:
                    keys.add(("behav", b))
                for t, _ in u.tags.most_common(4):
                    keys.add(("tag", t))
                for c, _ in u.classes.most_common(4):
                    keys.add(("cls", c))
                for k in keys:
                    buckets[k].append(idx)
            overlap = Counter()
            for idxs in buckets.values():
                if len(idxs) > 80:  # ultra-generic bucket (e.g. `div`, `flex`)
                    continue
                for a_pos in range(len(idxs)):
                    ia = idxs[a_pos]
                    for b_pos in range(a_pos + 1, len(idxs)):
                        overlap[(ia, idxs[b_pos])] += 1
            for (i, j), hits in overlap.items():
                if hits >= 2 or (lang_units[i].role_suffix and lang_units[i].role_suffix == lang_units[j].role_suffix):
                    pairs.append((lang_units[i], lang_units[j]))
    return pairs


def _pair_threshold(a, b):
    """Score a pair must reach: full-size units use MIN_COMP_SCORE; a pair with a small unit (8-14 lines)
    needs MIN_SMALL_COMP_SCORE plus shared behaviour, shell chrome or classes, so tiny wrappers
    (icons, one-tag helpers) never match on shape alone. None: the pair is not eligible."""
    if min(a.nontrivial_lines, b.nontrivial_lines) >= MIN_UNIT_LINES:
        return MIN_COMP_SCORE
    shared_structure = (
        bool(a.behav & b.behav)
        or bool(a.raw_shell_cues & b.raw_shell_cues)
        or len(set(a.classes) & set(b.classes)) >= MIN_SMALL_SHARED_CLASSES
    )
    return MIN_SMALL_COMP_SCORE if shared_structure else None


def component_patterns(index, touched=None):
    """Detects structurally near-duplicate UI components/hooks that can be unified via props or shell composition."""
    out = []
    seen_pairs = set()
    shell_groups, plain = [], []
    for u1, u2 in _candidate_pairs(index.units, touched):
        if u1.rel == u2.rel and not (u1.end_line < u2.line or u2.end_line < u1.line):
            continue  # nested inside the same span
        a, b = sorted([u1, u2], key=lambda u: (u.rel, u.line))
        key = (a.rel, a.line, b.rel, b.line)
        if key in seen_pairs:
            continue
        threshold = _pair_threshold(a, b)
        if threshold is None:
            continue
        score, detail = _score_pair(a, b)
        shell_cues = a.raw_shell_cues & b.raw_shell_cues & SHELL_CUES
        shell_hit = len(shell_cues) >= 2 and max(score, detail.get("behaviour_score", 0.0)) >= MIN_SHELL_SCORE
        # Same role, different behaviour: two search inputs (debounce vs Enter) with the same markup are one
        # component with optional props even when their handler code differs.
        role_hit = (bool(a.raw_shell_cues & b.raw_shell_cues & {"search_input"}) and detail.get("tag", 0) >= 0.75
                    and (not (a.classes and b.classes) or detail.get("class", 0) >= 0.6) and score >= MIN_ROLE_SCORE)
        # Small presentational copies (empty states) are defined by their chrome: identical classes and markup.
        chrome_hit = (detail.get("class", 0) >= 0.9 and detail.get("tag", 0) >= 0.7 and score >= MIN_CHROME_SCORE)
        if score < threshold and (role_hit or chrome_hit) and not shell_hit:
            threshold = score
        # Shared design-system classes alone (same input styles in a login form and a newsletter form) do not make
        # two components one: the code shape must agree too, unless a role/chrome/shell cue explains the match.
        if detail.get("shingle", 1.0) < MIN_PLAIN_SHINGLE and not (role_hit or chrome_hit or shell_hit or detail.get("subset")):
            continue
        if score < threshold and not shell_hit:
            continue
        seen_pairs.add(key)
        for u in (a, b):
            index.end[(u.rel, u.line)] = max(index.end.get((u.rel, u.line), u.line), u.end_line)
        if score < threshold:  # reported only for the shell it re-implements: cluster all such units
            shell_groups.append((a, b, score))
            continue
        shared_desc, varies_desc = _param_diff(a, b)
        pct = int(round(score * 100))
        if detail.get("subset"):
            big = b if detail["subset"] == a.name else a
            label = (f'{detail["subset"]} / {big.name}" (subset variant: {pct}% of {detail["subset"]} is inside '
                     f'{big.name}; varies: {varies_desc}; add the extras as optional props)')
        elif detail.get("style_variant"):
            label = (f'{a.name} / {b.name}" ({pct}% structural match, restyled copy; shared: {shared_desc}; '
                     f'varies: styles -> one component with a `variant`/`className` prop)')
        else:
            label = f'{a.name} / {b.name}" ({pct}% structural match; shared: {shared_desc}; varies: {varies_desc})'
        where = ((a.rel, a.line), (b.rel, b.line))
        sig = f"{a.name}|{b.name}|{shared_desc}|{varies_desc}"  # stable content signature for shown deduplication
        kind = "component_shell" if shell_cues else "component"
        if detail.get("subset") or detail.get("style_variant"):
            out.append((kind, label, where, sig))
        else:
            plain.append((a, b, score, (kind, label, where, sig)))

    # Families: 3+ components that are all copies of one another become ONE finding instead of N*(N-1)/2 pairs.
    # Only near-identical pairs (>= MIN_FAMILY_SCORE) chain into a family, so 75% look-alikes never snowball.
    family_pairs = [(a, b) for a, b, sc, _ in plain if sc >= MIN_FAMILY_SCORE]
    in_family = {k for members in _clusters(family_pairs) if len(members) >= 3 for k in ((u.rel, u.line) for u in members)}
    out.extend(f for a, b, _, f in plain if not ((a.rel, a.line) in in_family and (b.rel, b.line) in in_family))
    for members in _clusters(family_pairs):
        if len(members) < 3:
            continue
        keys = {(u.rel, u.line) for u in members}
        scores = [sc for a, b, sc, _ in plain if (a.rel, a.line) in keys and (b.rel, b.line) in keys and sc >= MIN_FAMILY_SCORE]
        kind = "component_shell" if any(f[0] == "component_shell" for a, _, _, f in plain if (a.rel, a.line) in keys) else "component"
        shared_desc, varies_desc = _param_diff(members[0], members[1])
        names = " / ".join(u.name for u in members[:4]) + (f" (+{len(members) - 4})" if len(members) > 4 else "")
        label = (f'{names}" ({len(members)} near-identical components, {int(round(min(scores) * 100))}%+ structural match; '
                 f'shared: {shared_desc}; varies: {varies_desc})')
        out.append((kind, label, tuple((u.rel, u.line) for u in members), "family|" + "|".join(u.name for u in members)))

    # Shell clusters: N units that each re-implement the same overlay/Escape/chrome become ONE finding.
    for members in _clusters([(a, b) for a, b, _ in shell_groups]):
        cues = (set.intersection(*(u.raw_shell_cues for u in members)) & SHELL_CUES
                or set.union(*(u.raw_shell_cues for u in members)) & SHELL_CUES)
        hooks = sorted(set.intersection(*(u.behav for u in members)) - {"useState"})
        names = " / ".join(u.name for u in members[:4]) + (f" (+{len(members) - 4})" if len(members) > 4 else "")
        shared = "shell: " + ", ".join(sorted(cues)) + (("; hooks/events: " + ", ".join(hooks[:4])) if hooks else "")
        label = (f'{names}" ({len(members)} components re-implement the same shell; shared: {shared}; '
                 f'varies: body -> extract <{_shell_name(members)} title footer onClose>{{children}}</...> '
                 f'+ useEscapeKey, keep each body as children)')
        where = tuple((u.rel, u.line) for u in members)
        out.append(("component_shell", label, where, "shell|" + "|".join(u.name for u in members)))
    return out


def _clusters(pairs):
    """Connected components (union-find) over unit pairs, each sorted by (rel, line)."""
    parent, units = {}, {}
    def find(k):
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    for a, b in pairs:
        ka, kb = (a.rel, a.line), (b.rel, b.line)
        units[ka], units[kb] = a, b
        parent[find(ka)] = find(kb)
    groups = defaultdict(list)
    for k, u in units.items():
        groups[find(k)].append(u)
    return [sorted(g, key=lambda u: (u.rel, u.line)) for g in sorted(groups.values(), key=lambda g: min((u.rel, u.line) for u in g))]


def _shell_name(members):
    roles = {u.role_suffix for u in members if u.role_suffix}
    for role in ("dialog", "modal", "drawer", "sheet", "popover", "panel"):
        if role in roles:
            return role.capitalize() + "Shell"
    return "ModalShell"


def created_reuse_candidates(index, created_rels, existing_findings):
    """Smart reuse check for newly created components: fires ONLY on structural score >= 0.65 OR shared raw shell cues."""
    if not created_rels:
        return []
    created_set = set(created_rels)
    already_flagged = {r for f in existing_findings for r, _ in getattr(f, "where", ())}
    visual = index.arch.visual_layer()
    shared_max = visual if visual is not None else 3

    new_units = [u for u in index.units if u.rel in created_set and u.rel not in already_flagged and u.nontrivial_lines >= MIN_CREATED_LINES]
    old_units = [u for u in index.units if u.rel not in created_set and u.nontrivial_lines >= MIN_CREATED_LINES]
    candidates = []
    for nu in new_units:
        best = None
        for ou in old_units:
            if nu.lang != ou.lang:
                continue
            score, _ = _score_pair(nu, ou)
            ou_layer = index.arch.layer(ou.rel)
            ou_in_shared = (ou_layer is not None and ou_layer <= shared_max) or bool(UI_DIR_HINT.search(ou.rel))
            shared_raw_shell = nu.raw_shell_cues & ou.raw_shell_cues
            hit_score = score >= MIN_CREATED_SCORE
            hit_shell = len(shared_raw_shell & SHELL_CUES) >= 2 or (bool(shared_raw_shell) and ou_in_shared)
            if hit_score or hit_shell:
                rank = (score, len(shared_raw_shell))
                if best is None or rank > best[0]:
                    best = (rank, score, shared_raw_shell, ou)
        if best is not None:
            _, score, shared_raw_shell, ou = best
            shared_desc, varies_desc = _param_diff(nu, ou)
            reason = (f"{int(round(score * 100))}% structural match" if score >= MIN_CREATED_SCORE
                      else f"duplicates shared shell ({', '.join(sorted(shared_raw_shell))})")
            problem = (
                f'new component "{nu.name} -> {ou.name}" closely matches {ou.rel}:{ou.line} '
                f'({reason}; shared: {shared_desc}; varies: {varies_desc})'
            )
            if score >= MIN_CREATED_SCORE:
                fix = f"reuse or extend {ou.rel}:{ou.line} (<=3 optional props/slots) instead of duplicating, or --accept if distinct"
            else:  # only the shell is shared: compose, do not grow one component with mode flags
                fix = (f"extract the shared shell of {ou.rel}:{ou.line} (<{_shell_name([nu, ou])} title footer onClose>"
                       f"{{children}} + useEscapeKey) and keep each body as children, or --accept if distinct")
            f = Finding(nu.rel, nu.line, "pattern", problem, fix)
            f.where = ((nu.rel, nu.line), (ou.rel, ou.line))
            f.code = f"reuse|{nu.name}|{ou.name}|{ou.rel}"
            f.conflict, f.target = None, ou.rel
            candidates.append(f)
    return candidates


def _build_findings(index, repo, arch, touched=None):
    out = []
    found_patterns = chain_patterns(index) + block_patterns(index)
    found_patterns += helper_patterns(index, found_patterns)
    found_patterns += component_patterns(index, touched=touched)
    for kind, label, where, code in found_patterns:
        mine = [w for w in where if touched is None or (w[0] in touched and (touched[w[0]] is None or any(
            n in touched[w[0]] for n in range(w[1], index.end.get(w, w[1]) + 1))))]
        if not mine:
            continue
        files = sorted({r for r, _ in where})
        is_hook = kind == "component" and label.startswith("use") and all(
            n.strip().startswith("use") for n in label.split('"')[0].split(" (")[0].split(" / "))
        force_ui = kind in ("helper", "component", "component_shell") and not is_hook
        target, conflict = destination(where, code, arch, force_ui=force_ui)
        places = ", ".join(f"{r}:{n}" for r, n in where[:6]) + (f" (+{len(where) - 6})" if len(where) > 6 else "")
        if kind == "chain":
            problem = f'chain "{label[:110]}" repeated {len(where)}x in {len(files)} file(s) ({places})'
        elif kind == "helper":
            problem = f'local UI helper "{label}" in {len(files)} file(s) ({places})'
        elif kind in ("component", "component_shell"):
            problem = f'near-duplicate component "{label} ({places})'
        else:
            problem = (f'block of {label.split(":")[0]} repeated in {len(where)} places: '
                       f'"{label.split(": ", 1)[1]}" ({places})')
        if conflict:
            fix = f"layer conflict: {conflict}"
        elif kind == "component_shell":
            dest = target or "the shared UI layer"
            fix = f"extract shared shell/hook to {dest} (composition, <=3 props) or unify if same role"
        elif is_hook:
            dest = target or "the shared hooks/lib layer"
            fix = f"unify into one shared hook in {dest} with the varying parts as parameters (<=3)"
        elif kind == "component":
            dest = target or "the shared UI layer"
            fix = f"unify into shared component in {dest} via optional prop/callback (<=3 props)"
        else:
            fix = f"extract to {target}" if target else "extract to the destination the chosen architecture gives"
        # Canonical path/line (`where[0]`) ensures cross-file --accept and prune_accepted stay stable
        # regardless of which file in `where` triggered the scan.
        canon_path, canon_line = where[0][0], where[0][1]
        f = Finding(canon_path, canon_line, "pattern", problem, fix)
        f.where, f.code, f.conflict, f.target = where, code, conflict, target
        out.append(f)
    return out


def findings_and_reuse(repo, arch, touched=None, suffixes=None, created=()):
    """Returns (findings, reuse_candidates) in one index pass for Stop hooks."""
    index = Index(repo, arch, suffixes)
    raw = _build_findings(index, repo, arch, touched=touched)
    reuse = created_reuse_candidates(index, created, raw)
    all_raw = raw + reuse
    store = core.project_subdir(repo, "modular") / "accepted.txt"
    if not core.SELFTEST:
        scanned_whole = list(index.lines) if touched is None else [
            rel for rel, lines in touched.items() if lines is None
        ]
        core.prune_accepted(scanned_whole, all_raw, "patterns.py", store=store, repo=repo)
    taken = {(p, r, s) for p, r, s, *_ in core.load_accepted(store)} if not core.SELFTEST else set()
    kept_raw = [f for f in raw if (f.path, f.rule, f.subject) not in taken]
    kept_reuse = [f for f in reuse if (f.path, f.rule, f.subject) not in taken]
    return kept_raw, kept_reuse


def findings(repo, arch, touched=None, suffixes=None):
    """Findings; touched {file: changed lines or None = all}: only patterns with a place in those lines."""
    found, _ = findings_and_reuse(repo, arch, touched=touched, suffixes=suffixes)
    return found


# ------------------------------------------------------------------------------- briefing
def briefing(found, arch, trigger, created=()):
    """The text the hook returns to the agent: what to extract, where to, and how without changing behaviour."""
    lines = [f"Modularization (skill modular, {(HERE / 'SKILL.md').as_posix()}). Trigger: {trigger}."]
    if created:
        where = ", ".join(arch.destinations.values()) if arch.defined else "components/, scripts/, bin/, lib/ or utils/"
        lines.append(
            f"New component/script started ({', '.join(created[:4])}): check whether an equivalent "
            f"component, shell, helper or script already exists in ({where}); reuse, extend (<=3 optional props/slots), "
            "or extract a shared shell instead of duplicating."
        )
    if not arch.defined and found:
        if arch.docs:
            lines.append(f"Architecture docs exist ({', '.join(arch.docs)}), but {MAP_FILE.as_posix()} is missing; follow the existing structure.")
        elif any("same file" not in f.fix for f in found[:PER_ROUND]):
            lines.append("No architecture map: follow the existing folder structure (or extract locally inside the same file).")
    if found:
        lines.append("Repeated or near-duplicate patterns involving this session's files:")
        for n, f in enumerate(found[:PER_ROUND], 1):
            lines.append(f"{n}. {f}")
        if len(found) > PER_ROUND:
            lines.append(f"(+{len(found) - PER_ROUND} in the next round or in patterns.py --all)")
        lines.append(
            "Extract or unify shared logic (parameterizing only what varies, max <=3 props/slots; prefer shell composition over boolean mode flags; "
            "skip files with unrelated git edits; "
            + (f"verify with {arch.verify}). " if arch.verify else "then build/test). ")
            + "False positive or intentionally distinct? Accept silently: "
            + core.accept_command(HERE / "patterns.py") + ".")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------- CLI & selftest
def _wave1_wave2_selftest():
    """Targeted regression tests for Wave 1 bug fixes & Wave 2 near-duplicate component detection."""
    import json
    import shutil
    import tempfile

    problems = []
    mod_gate = HERE / "hooks" / "modular_gate.py"
    edit_gate = HERE.parent / "tautology" / "hooks" / "edit_gate.py"
    stop_gate = HERE.parent / "tautology" / "hooks" / "stop_gate.py"

    def make_repo(files, arch_md=None):
        d = Path(tempfile.mkdtemp(prefix="modular-w12-"))
        for rel, content in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        if arch_md is not None:
            ap = d / MAP_FILE
            ap.parent.mkdir(parents=True, exist_ok=True)
            ap.write_text(arch_md, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        return d

    def check_target(files, arch_md, expected, label):
        repo_dir = make_repo(files, arch_md=arch_md)
        try:
            res = findings(repo_dir, Architecture(repo_dir))
            if not res or res[0].conflict or res[0].target != expected:
                problems.append(f"{label}: {[str(f) for f in res]!r}")
        finally:
            shutil.rmtree(repo_dir, ignore_errors=True)

    # 1. 3-layer architecture (1=lib, 2=components, 3=pages) routes UI to target 2
    check_target(
        {
            "src/pages/A.tsx": "export function A() {\n  const state = useState(false);\n  return <div className=\"modal-shell\">\n    <header className=\"modal-header\">Header</header>\n    <main className=\"modal-body\">Body</main>\n    <button onClick={onSave}>Save</button>\n    <button onClick={onCancel}>Cancel</button>\n    <footer className=\"modal-footer\">Foot</footer>\n  </div>;\n}\n",
            "src/pages/B.tsx": "export function B() {\n  const state = useState(false);\n  return <div className=\"modal-shell\">\n    <header className=\"modal-header\">Header</header>\n    <main className=\"modal-body\">Body</main>\n    <button onClick={onSave}>Save</button>\n    <button onClick={onCancel}>Cancel</button>\n    <footer className=\"modal-footer\">Foot</footer>\n  </div>;\n}\n",
        },
        "layer 1: src/lib/\nlayer 2: src/components/\nlayer 3: src/pages/\ntarget 1: src/lib/utils.ts\ntarget 2: src/components/\n",
        "src/components/",
        "3-layer UI routing failed",
    )

    # 2. Rust generics Vec<String> in layer 1 NOT misclassified as UI
    check_target(
        {
            "src/data/a.rs": "pub fn load_a(items: Vec<String>) -> Vec<String> {\n    let mut out: Vec<String> = Vec::new();\n    for item in items.into_iter() {\n        let trimmed = item.trim().to_owned();\n        if !trimmed.is_empty() {\n            out.push(trimmed);\n        }\n    }\n    out.sort_unstable();\n    out.dedup_by(|a, b| a == b);\n    out\n}\n",
            "src/data/b.rs": "pub fn load_b(items: Vec<String>) -> Vec<String> {\n    let mut out: Vec<String> = Vec::new();\n    for item in items.into_iter() {\n        let trimmed = item.trim().to_owned();\n        if !trimmed.is_empty() {\n            out.push(trimmed);\n        }\n    }\n    out.sort_unstable();\n    out.dedup_by(|a, b| a == b);\n    out\n}\n",
        },
        "layer 1: src/data/\nlayer 3: src/components/\nlayer 4: src/screens/\ntarget 1: src/data/shared.rs\ntarget 3: src/components/ui.rs\n",
        "src/data/shared.rs",
        "Rust Vec<String> misclassified as UI",
    )

    # 3. Single quotes, Rust char '"', Python comment with ", Tailwind unrelated FP rejected
    r = make_repo({
        "src/components/A.tsx": "export function A() {\n  const label = 'Users';\n  const emptyMsg = 'No users found';\n  const count = items.filter(x => x.active).length;\n  const title = label + ' (' + count + ')';\n  if (count === 0) return <p className='text-muted'>{emptyMsg}</p>;\n  return <section aria-label='Users list'>{title}</section>;\n}\n",
        "src/components/B.tsx": "export function B() {\n  const label = 'Orders';\n  const emptyMsg = 'No orders found';\n  const count = items.filter(x => x.active).length;\n  const title = label + ' (' + count + ')';\n  if (count === 0) return <p className='text-muted'>{emptyMsg}</p>;\n  return <section aria-label='Orders list'>{title}</section>;\n}\n",
        "src/a.rs": "fn parse_a(c: char) {\n    let is_q = c == '\"';\n    let mut out = Vec::new();\n    for item in items.iter().filter(|x| x.ok) {\n        let name = format!(\"{} - {}\", item.a, item.b);\n        out.push(Row { name, id: item.id });\n    }\n    out.sort_by(|a, b| a.name.cmp(&b.name));\n    out.dedup_by(|a, b| a.id == b.id);\n    let end = \"done\";\n}\n",
        "src/b.rs": "fn parse_b(c: char) {\n    let mut out = Vec::new();\n    for item in items.iter().filter(|x| x.ok) {\n        let name = format!(\"{} - {}\", item.a, item.b);\n        out.push(Row { name, id: item.id });\n    }\n    out.sort_by(|a, b| a.name.cmp(&b.name));\n    out.dedup_by(|a, b| a.id == b.id);\n    let end = \"done\";\n}\n",
        "src/a.py": "def helper_a(items):\n    # strip leading \" quote\n    out_rows = []\n    for item in items:\n        if item.active:\n            label = f\"{item.store} - {item.price}\"\n            out_rows.append((label, item.state, item.id))\n    out_rows.sort(key=lambda x: x[0])\n    return out_rows\n",
        "src/b.py": "def helper_b(items):\n    out_rows = []\n    for item in items:\n        if item.active:\n            label = f\"{item.store} - {item.price}\"\n            out_rows.append((label, item.state, item.id))\n    out_rows.sort(key=lambda x: x[0])\n    return out_rows\n",
    })
    try:
        res = findings(r, Architecture(r))
        paths = {f.path for f in res}
        for need in ("src/components/A.tsx", "src/a.rs", "src/a.py"):
            if need not in paths:
                problems.append(f"tokenizer regression missed {need}: {[str(f) for f in res]!r}")
    finally:
        shutil.rmtree(r, ignore_errors=True)

    # 4. PT-BR accented filename in git tracked()
    r = make_repo({
        "src/screens/configuração.tsx": "export function A() {\n  const a = computeThing(x);\n  const b = transformThing(a);\n  const c = validateThing(b);\n  const d = formatThing(c);\n  const e = serializeThing(d);\n  return finalizeThing(e);\n}\n",
        "src/screens/outro.tsx": "export function B() {\n  const a = computeThing(x);\n  const b = transformThing(a);\n  const c = validateThing(b);\n  const d = formatThing(c);\n  const e = serializeThing(d);\n  return finalizeThing(e);\n}\n",
    })
    try:
        res = findings(r, Architecture(r))
        if not res:
            problems.append("non-ASCII filename (configuração.tsx) skipped by tracked()")
    finally:
        shutil.rmtree(r, ignore_errors=True)

    # 5. Cross-file --accept from src/b.rs survives patterns.py --all without pruning or resurfacing
    r = make_repo({
        "src/a.rs": "fn fa() {\n  let a = step_one(x);\n  let b = step_two(a);\n  let c = step_three(b);\n  let d = step_four(c);\n  let e = step_five(d);\n  finish_all(e);\n}\n",
        "src/b.rs": "fn fb() {\n  let a = step_one(x);\n  let b = step_two(a);\n  let c = step_three(b);\n  let d = step_four(c);\n  let e = step_five(d);\n  finish_all(e);\n}\n",
    })
    try:
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(r)}
        out_b = subprocess.run([sys.executable, str(HERE / "patterns.py"), "src/b.rs"], cwd=r, env=env, capture_output=True, text=True, encoding="utf-8").stdout
        line_b = next((l for l in out_b.splitlines() if "[pattern]" in l), "")
        if not line_b:
            problems.append(f"cross-file accept setup found nothing: {out_b!r}")
        else:
            acc = subprocess.run([sys.executable, str(HERE / "patterns.py"), "--accept", "-", "--reason", "intentional"],
                                 input=line_b, cwd=r, env=env, capture_output=True, text=True, encoding="utf-8")
            out_all = subprocess.run([sys.executable, str(HERE / "patterns.py"), "--all"], cwd=r, env=env, capture_output=True, text=True, encoding="utf-8").stdout
            if acc.returncode != 0 or "[pattern]" in out_all or not (r / ".claude/modular/accepted.txt").is_file():
                problems.append(f"cross-file --accept broke on --all: rc={acc.returncode}, out_all={out_all!r}")
    finally:
        shutil.rmtree(r, ignore_errors=True)

    # 6. Wave 2: UserSearchInput vs OrderSearchInput (CAUGHT), DeleteModal vs RenameModal (CAUGHT),
    # PricingCard vs ErrorBanner (REJECTED), already-wrapped <ModalShell> pair (REJECTED via composition discount)
    r = make_repo({
        "src/components/UserSearchInput.tsx": (
            'import { useState, useEffect, useRef } from "react";\n'
            'import { Search, X } from "lucide-react";\n'
            'export function UserSearchInput({ value, onChange, onSelectUser }: Props) {\n'
            '  const [query, setQuery] = useState(value);\n'
            '  const inputRef = useRef<HTMLInputElement>(null);\n'
            '  useEffect(() => {\n'
            '    const id = setTimeout(() => onChange(query), 250);\n'
            '    return () => clearTimeout(id);\n'
            '  }, [query, onChange]);\n'
            '  const handleClear = () => {\n'
            '    setQuery("");\n'
            '    onChange("");\n'
            '    inputRef.current?.focus();\n'
            '  };\n'
            '  return (\n'
            '    <div className="relative flex items-center rounded-md border border-input bg-background px-3 py-2">\n'
            '      <Search className="mr-2 h-4 w-4 text-muted-foreground" />\n'
            '      <input ref={inputRef} type="search" value={query} onChange={(e) => setQuery(e.target.value)}\n'
            '        onKeyDown={(e) => e.key === "Enter" && onSelectUser(query)} placeholder="Search users..."\n'
            '        className="w-full bg-transparent text-sm outline-none" />\n'
            '      {query && (\n'
            '        <button type="button" onClick={handleClear} aria-label="Clear search">\n'
            '          <X className="h-4 w-4 text-muted-foreground" />\n'
            '        </button>\n'
            '      )}\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        ),
        "src/components/OrderSearchInput.tsx": (
            'import { useState, useEffect, useRef } from "react";\n'
            'import { Search, X } from "lucide-react";\n'
            'export function OrderSearchInput({ initialTerm, onSearch, onFilterReset }: OrderProps) {\n'
            '  const [term, setTerm] = useState(initialTerm);\n'
            '  const inputRef = useRef<HTMLInputElement>(null);\n'
            '  useEffect(() => {\n'
            '    const id = setTimeout(() => onSearch(term), 250);\n'
            '    return () => clearTimeout(id);\n'
            '  }, [term, onSearch]);\n'
            '  const clearTerm = () => {\n'
            '    setTerm("");\n'
            '    onSearch("");\n'
            '    onFilterReset?.();\n'
            '    inputRef.current?.focus();\n'
            '  };\n'
            '  return (\n'
            '    <div className="relative flex items-center rounded-md border border-input bg-background px-3 py-2">\n'
            '      <Search className="mr-2 h-4 w-4 text-muted-foreground" />\n'
            '      <input ref={inputRef} type="search" value={term} onChange={(e) => setTerm(e.target.value)}\n'
            '        placeholder="Search orders..." className="w-full bg-transparent text-sm outline-none" />\n'
            '      {term && (\n'
            '        <button type="button" onClick={clearTerm} aria-label="Clear orders">\n'
            '          <X className="h-4 w-4 text-muted-foreground" />\n'
            '        </button>\n'
            '      )}\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        ),
        "src/components/DeleteModal.tsx": (
            'import { useEffect } from "react";\n'
            'import { X } from "lucide-react";\n'
            'export function DeleteModal({ open, onClose, onConfirm, itemName }: DeleteProps) {\n'
            '  useEffect(() => {\n'
            '    if (!open) return;\n'
            '    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };\n'
            '    window.addEventListener("keydown", onKey);\n'
            '    return () => window.removeEventListener("keydown", onKey);\n'
            '  }, [open, onClose]);\n'
            '  if (!open) return null;\n'
            '  return (\n'
            '    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>\n'
            '      <div className="w-full max-w-md rounded-lg bg-surface p-6 shadow-lg" onClick={(e) => e.stopPropagation()}>\n'
            '        <div className="flex items-center justify-between border-b pb-3">\n'
            '          <h2 className="text-lg font-semibold">Delete {itemName}</h2>\n'
            '          <button onClick={onClose} aria-label="Close"><X className="h-4 w-4" /></button>\n'
            '        </div>\n'
            '        <div className="py-4 text-sm text-muted">Permanently delete this record?</div>\n'
            '        <div className="flex justify-end gap-2 border-t pt-3">\n'
            '          <button onClick={onClose} className="btn-secondary">Cancel</button>\n'
            '          <button onClick={onConfirm} className="btn-danger">Delete</button>\n'
            '        </div>\n'
            '      </div>\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        ),
        "src/components/RenameModal.tsx": (
            'import { useEffect, useState } from "react";\n'
            'import { X } from "lucide-react";\n'
            'export function RenameModal({ isOpen, onDismiss, onSave, currentName }: RenameProps) {\n'
            '  const [name, setName] = useState(currentName);\n'
            '  useEffect(() => {\n'
            '    if (!isOpen) return;\n'
            '    const handleEsc = (e: KeyboardEvent) => { if (e.key === "Escape") onDismiss(); };\n'
            '    window.addEventListener("keydown", handleEsc);\n'
            '    return () => window.removeEventListener("keydown", handleEsc);\n'
            '  }, [isOpen, onDismiss]);\n'
            '  if (!isOpen) return null;\n'
            '  return (\n'
            '    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onDismiss}>\n'
            '      <div className="w-full max-w-md rounded-lg bg-surface p-6 shadow-lg" onClick={(e) => e.stopPropagation()}>\n'
            '        <div className="flex items-center justify-between border-b pb-3">\n'
            '          <h2 className="text-lg font-semibold">Rename project</h2>\n'
            '          <button onClick={onDismiss} aria-label="Close"><X className="h-4 w-4" /></button>\n'
            '        </div>\n'
            '        <div className="py-4">\n'
            '          <input value={name} onChange={(e) => setName(e.target.value)} className="input w-full" />\n'
            '        </div>\n'
            '        <div className="flex justify-end gap-2 border-t pt-3">\n'
            '          <button onClick={onDismiss} className="btn-secondary">Cancel</button>\n'
            '          <button onClick={() => onSave(name)} className="btn-primary">Save</button>\n'
            '        </div>\n'
            '      </div>\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        ),
        "src/components/PricingCard.tsx": (
            'export function PricingCard({ price, tier, seats, support }: Props) {\n'
            '  const label = tier.toUpperCase();\n'
            '  const sub = `${seats} seats`;\n'
            '  return (\n'
            '    <div className="rounded-xl border border-slate-200 p-6 shadow-sm">\n'
            '      <div className="flex items-baseline justify-between">\n'
            '        <span className="text-sm font-medium text-slate-500">\n'
            '          {label}\n'
            '        </span>\n'
            '        <span className="text-2xl font-bold text-slate-900">\n'
            '          {price}\n'
            '        </span>\n'
            '      </div>\n'
            '      <div className="mt-4 flex flex-col gap-2">\n'
            '        <span className="text-xs text-slate-600">{sub}</span>\n'
            '        <span className="text-xs text-slate-600">{support}</span>\n'
            '      </div>\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        ),
        "src/components/ErrorBanner.tsx": (
            'export function ErrorBanner({ code, msg, retryLabel, dismissLabel }: ErrProps) {\n'
            '  const heading = code.toUpperCase();\n'
            '  const detail = `Code: ${msg}`;\n'
            '  return (\n'
            '    <div className="fixed bottom-4 right-4 bg-red-600 text-white px-4 py-3">\n'
            '      <div className="flex items-center gap-3">\n'
            '        <span className="font-semibold uppercase tracking-wide">\n'
            '          {heading}\n'
            '        </span>\n'
            '        <span className="text-sm opacity-90">\n'
            '          {detail}\n'
            '        </span>\n'
            '      </div>\n'
            '      <div className="mt-2 flex justify-end gap-2">\n'
            '        <span className="underline cursor-pointer">{retryLabel}</span>\n'
            '        <span className="opacity-75 cursor-pointer">{dismissLabel}</span>\n'
            '      </div>\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        ),
        "src/components/WrappedArchiveModal.tsx": (
            'export function WrappedArchiveModal({ open, onClose, onArchive, projectName }: Props) {\n'
            '  const [reason, setReason] = useState("");\n'
            '  const canSubmit = reason.trim().length > 2;\n'
            '  return (\n'
            '    <ModalShell\n'
            '      open={open}\n'
            '      onClose={onClose}\n'
            '      title={`Archive ${projectName}`}\n'
            '      footer={\n'
            '        <div className="flex justify-end gap-2">\n'
            '          <button onClick={onClose} className="btn-secondary">Cancel</button>\n'
            '          <button disabled={!canSubmit} onClick={() => onArchive(reason)} className="btn-danger">Archive</button>\n'
            '        </div>\n'
            '      }\n'
            '    >\n'
            '      <p className="text-sm text-muted">Enter a reason before archiving.</p>\n'
            '      <input value={reason} onChange={(e) => setReason(e.target.value)} className="input w-full" />\n'
            '    </ModalShell>\n'
            '  );\n'
            '}\n'
        ),
        "src/components/WrappedTransferModal.tsx": (
            'export function WrappedTransferModal({ isOpen, onDismiss, onTransfer, repoName }: TransferProps) {\n'
            '  const [owner, setOwner] = useState("");\n'
            '  const canSave = owner.trim().length > 2;\n'
            '  return (\n'
            '    <ModalShell\n'
            '      open={isOpen}\n'
            '      onClose={onDismiss}\n'
            '      title={`Transfer ${repoName}`}\n'
            '      footer={\n'
            '        <div className="flex justify-end gap-2">\n'
            '          <button onClick={onDismiss} className="btn-secondary">Cancel</button>\n'
            '          <button disabled={!canSave} onClick={() => onTransfer(owner)} className="btn-primary">Transfer</button>\n'
            '        </div>\n'
            '      }\n'
            '    >\n'
            '      <p className="text-sm text-muted">Choose the destination organization.</p>\n'
            '      <input value={owner} onChange={(e) => setOwner(e.target.value)} className="input w-full" />\n'
            '    </ModalShell>\n'
            '  );\n'
            '}\n'
        ),
    })
    try:
        res = findings(r, Architecture(r))
        joined = "\n".join(str(f) for f in res)
        if "OrderSearchInput" not in joined or "UserSearchInput" not in joined:
            problems.append(f"Wave 2 missed SearchInput near-duplicate: {joined!r}")
        if "DeleteModal" not in joined or "RenameModal" not in joined:
            problems.append(f"Wave 2 missed Modal near-duplicate: {joined!r}")
        if "PricingCard" in joined or "ErrorBanner" in joined:
            problems.append(f"Wave 2 false positive on PricingCard/ErrorBanner: {joined!r}")
        if "WrappedArchiveModal" in joined or "WrappedTransferModal" in joined:
            problems.append(f"Wave 2 failed composition discount on <ModalShell> pair: {joined!r}")
    finally:
        shutil.rmtree(r, ignore_errors=True)

    # 7. modular_gate: approval persistence, polite action question, expanded nouns, smart created reuse
    r = make_repo({
        "src/components/UserSearchInput.tsx": (
            'export function UserSearchInput({ value, onChange }: Props) {\n'
            '  const [query, setQuery] = useState(value);\n'
            '  useEffect(() => {\n'
            '    const id = setTimeout(() => onChange(query), 250);\n'
            '    return () => clearTimeout(id);\n'
            '  }, [query, onChange]);\n'
            '  return (\n'
            '    <div className="relative flex items-center rounded-md border px-3 py-2">\n'
            '      <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} className="w-full text-sm" />\n'
            '      {query && <button onClick={() => setQuery("")}>Clear</button>}\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        ),
    })
    try:
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(r)}

        def prompt_hook(text, sid):
            p = {"hook_event_name": "UserPromptSubmit", "session_id": sid, "cwd": str(r), "prompt": text}
            res = subprocess.run([sys.executable, str(mod_gate)], input=json.dumps(p), capture_output=True, text=True, encoding="utf-8", env=env)
            return res.returncode, bool(res.stdout.strip()), (r / f".git/tautology/{sid}.refactor").exists()

        _, rem_q, flg_q = prompt_hook("Can you create a new OrderSearchInput component for orders?", "q1")
        if not rem_q or not flg_q:
            problems.append("modular_gate suppressed polite action question ending with ?")
        _, rem_info, flg_info = prompt_hook("How does SearchInput work?", "q2")
        if rem_info or flg_info:
            problems.append("modular_gate triggered on pure informational question")
        prompt_hook("refactor search helpers in src/lib", "ref1")
        _, _, flg_keep = prompt_hook("yes, go ahead", "ref1")
        if not flg_keep:
            problems.append("modular_gate dropped .refactor on short approval turn")
        _, rem_noun, _ = prompt_hook("add a SearchInput and toolbar to the orders page", "n1")
        if not rem_noun:
            problems.append("modular_gate missed expanded BUILD_ARTIFACT nouns (SearchInput/toolbar)")
        _, _, flg_test = prompt_hook("/tautology-audit-tests", "t1")
        if flg_test:
            problems.append("modular_gate forced .refactor on /tautology-audit-tests")

        # Smart created reuse check: creating a unique component must NOT nag; creating a near-dup MUST nag
        uniq = r / "src/components/UniqueModal.tsx"
        subprocess.run([sys.executable, str(edit_gate)], input=json.dumps({
            "hook_event_name": "PreToolUse", "session_id": "c_uniq", "cwd": str(r), "tool_input": {"file_path": str(uniq)}
        }), env=env, capture_output=True, text=True)
        uniq.write_text('export function UniqueModal() { return <section className="p-4"><h1>Only heading</h1></section>; }\n', encoding="utf-8")
        stop_u = subprocess.run([sys.executable, str(stop_gate)], input=json.dumps({
            "hook_event_name": "Stop", "session_id": "c_uniq", "cwd": str(r)
        }), env=env, capture_output=True, text=True)
        if stop_u.returncode != 0:
            problems.append(f"smart created check nagged on unrelated *Modal: rc={stop_u.returncode}, {stop_u.stderr!r}")

        dup = r / "src/components/OrderSearchInput.tsx"
        subprocess.run([sys.executable, str(edit_gate)], input=json.dumps({
            "hook_event_name": "PreToolUse", "session_id": "c_dup", "cwd": str(r), "tool_input": {"file_path": str(dup)}
        }), env=env, capture_output=True, text=True)
        dup.write_text(
            'export function OrderSearchInput({ initialTerm, onSearch }: OrderProps) {\n'
            '  const [term, setTerm] = useState(initialTerm);\n'
            '  useEffect(() => {\n'
            '    const id = setTimeout(() => onSearch(term), 250);\n'
            '    return () => clearTimeout(id);\n'
            '  }, [term, onSearch]);\n'
            '  return (\n'
            '    <div className="relative flex items-center rounded-md border px-3 py-2">\n'
            '      <input type="search" value={term} onChange={(e) => setTerm(e.target.value)} className="w-full text-sm" />\n'
            '      {term && <button onClick={() => setTerm("")}>Reset</button>}\n'
            '    </div>\n'
            '  );\n'
            '}\n',
            encoding="utf-8",
        )
        stop_d = subprocess.run([sys.executable, str(stop_gate)], input=json.dumps({
            "hook_event_name": "Stop", "session_id": "c_dup", "cwd": str(r)
        }), env=env, capture_output=True, text=True)
        if stop_d.returncode != 2 or "UserSearchInput" not in stop_d.stderr:
            problems.append(f"smart created check missed near-duplicate OrderSearchInput: rc={stop_d.returncode}, {stop_d.stderr!r}")
    finally:
        shutil.rmtree(r, ignore_errors=True)

    # 8. Extra Wave 1 regressions: 3rd-file sub-block not dropped, Rust turbofish chain, multi-line/IntoElement helper,
    #    #[cfg(test)] brace in string, cross-suffix .jsx/.tsx stop_gate, directory CLI arg
    r = make_repo({
        "src/a.rs": (
            '#[cfg(test)]\nmod tests {\n    fn t() { let s = "{"; }\n}\n'
            'fn fa() {\n  let x0 = init_zero(u);\n  let x1 = init_one(x0);\n'
            '  let a = step_one(x1);\n  let b = step_two(a);\n  let c = step_three(b);\n'
            '  let d = step_four(c);\n  let e = step_five(d);\n  let f = step_six(e);\n  finish_all(f);\n}\n'
            'fn chain_a(v: Client) { let _ = v.get::<User>("u").header("x", "y").timeout(5).send::<Resp>(); }\n'
        ),
        "src/b.rs": (
            'fn fb() {\n  let x0 = init_zero(u);\n  let x1 = init_one(x0);\n'
            '  let a = step_one(x1);\n  let b = step_two(a);\n  let c = step_three(b);\n'
            '  let d = step_four(c);\n  let e = step_five(d);\n  let f = step_six(e);\n  finish_all(f);\n}\n'
            'fn chain_b(v: Client) { let _ = v.get::<User>("u").header("x", "y").timeout(5).send::<Resp>(); }\n'
        ),
        "src/c.rs": (
            'fn fc() {\n  let a = step_one(x1);\n  let b = step_two(a);\n  let c = step_three(b);\n'
            '  let d = step_four(c);\n  let e = step_five(d);\n  let f = step_six(e);\n  finish_all(f);\n}\n'
            'fn chain_c(v: Client) { let _ = v.get::<User>("u").header("x", "y").timeout(5).send::<Resp>(); }\n'
        ),
        "src/d.rs": 'fn chain_d(v: Client) { let _ = v.get::<User>("u").header("x", "y").timeout(5).send::<Resp>(); }\n',
        "src/screens/h1.rs": "fn form_card(\n    title: &str,\n    p: Palette,\n) -> impl IntoElement {\n    div()\n}\n",
        "src/screens/h2.rs": "fn form_card(\n    title: &str,\n    p: Palette,\n) -> impl IntoElement {\n    div()\n}\n",
        "src/components/old.jsx": (
            "export function OldWidget(x) {\n  const a = step_one(x);\n  const b = step_two(a);\n"
            "  const c = step_three(b);\n  const d = step_four(c);\n  const e = step_five(d);\n  return finish_all(e);\n}\n"
        ),
    })
    try:
        res = findings(r, Architecture(r))
        joined = "\n".join(str(f) for f in res)
        if "src/c.rs:" not in joined:
            problems.append(f"block_patterns dropped 3rd file src/c.rs: {joined!r}")
        if ".send(" not in joined:
            problems.append(f"Rust turbofish chain missed: {joined!r}")
        if 'local UI helper "fn form_card"' not in joined:
            problems.append(f"Rust multi-line impl IntoElement helper missed: {joined!r}")
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(r)}
        dir_run = subprocess.run([sys.executable, str(HERE / "patterns.py"), "src/screens"], cwd=r, env=env, capture_output=True, text=True, encoding="utf-8")
        if "form_card" not in dir_run.stdout:
            problems.append(f"CLI directory arg src/screens missed helper: {dir_run.stdout!r}")
        new_tsx = r / "src/components/new.tsx"
        subprocess.run([sys.executable, str(edit_gate)], input=json.dumps({
            "hook_event_name": "PreToolUse", "session_id": "xext", "cwd": str(r), "tool_input": {"file_path": str(new_tsx)}
        }), env=env, capture_output=True, text=True)
        new_tsx.write_text(
            "export function NewWidget(x) {\n  const a = step_one(x);\n  const b = step_two(a);\n"
            "  const c = step_three(b);\n  const d = step_four(c);\n  const e = step_five(d);\n  return finish_all(e);\n}\n",
            encoding="utf-8",
        )
        stop_x = subprocess.run([sys.executable, str(stop_gate)], input=json.dumps({
            "hook_event_name": "Stop", "session_id": "xext", "cwd": str(r)
        }), env=env, capture_output=True, text=True)
        if stop_x.returncode != 2 or "old.jsx" not in stop_x.stderr:
            problems.append(f"cross-suffix .jsx/.tsx missed in stop_gate: rc={stop_x.returncode}, {stop_x.stderr!r}")
    finally:
        shutil.rmtree(r, ignore_errors=True)

    return problems


def selftest(record=False):
    """cases/repo is a fake repository with an architecture; expected.txt is the right output."""
    core.SELFTEST = True
    repo = CASES / "repo"
    got = [str(f) for f in findings(repo, Architecture(repo))]
    if record:
        return core.compare_expected(got, CASES / "expected.txt", record=True)
    differs = core.compare_expected(got, CASES / "expected.txt")
    problems = hook_selftest() + _wave1_wave2_selftest()
    # Regression: blocks using GPUI context/widget helpers (cx.entity, kit::, on_change) route to UI layer 3, not layer 2.
    arch = Architecture(repo)
    ui_block = "let owner = cx.entity().downgrade();\nkit::switch(\"id\", enabled, p).hint(\"tip\", p).on_change(move |v, _, _, cx| {});"
    target, conflict = destination([("src/screens/a/view.rs", 10), ("src/screens/b/view.rs", 10)], ui_block, arch)
    if conflict or target != arch.destinations.get(3):
        problems.append(f"GPUI widget block routed to {target!r} (conflict={conflict!r}), expected layer 3 {arch.destinations.get(3)!r}")
    if problems:
        for p in problems:
            print(f"FAIL    {p}")
    return 1 if differs or problems else 0


def _semantic_selftest():
    """cases/semantic/<case>/ are fake repositories of conceptual duplication (modal shells, restyled or subset
    variants, hooks, wrapped components, GPUI views) and look-alike negatives; expected.txt says what must
    (+) or must not (-, 0) be reported."""
    return run_case_spec(CASES / "semantic", "semantic", lambda repo: [
        str(f) for f in findings(repo, Architecture(repo)) if "near-duplicate" in f.problem])


def run_case_spec(base, label, scan):
    """<base>/expected.txt lines: `<case> + <text>` (a finding of <base>/<case> contains it), `<case> - <text>`
    (none does), `<case> 0` (no finding at all). scan(repo) -> finding lines. Returns the problems."""
    spec = base / "expected.txt"
    if not spec.is_file():
        return []
    problems, cache = [], {}
    for raw in spec.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        case, op, text = (raw.split(" ", 2) + [""])[:3]
        if case not in cache:
            cache[case] = scan(base / case)
        got = cache[case]
        if op == "+" and not any(text in g for g in got):
            problems.append(f"{label} case {case}: expected a finding with {text!r}, got {got!r}")
        elif op == "-" and any(text in g for g in got):
            problems.append(f"{label} case {case}: unexpected finding with {text!r}")
        elif op == "0" and got:
            problems.append(f"{label} case {case}: expected no finding, got {got!r}")
    return problems


def hook_selftest():
    """The hook in a temporary repository: with a map it gives the destination; without, fallback guidance; no repeats."""
    import json
    import shutil
    import tempfile
    gates = HERE.parent / "tautology" / "hooks"
    problems = []
    for variant in ("with a map", "without a map"):
        repo = Path(tempfile.mkdtemp(prefix="modular-"))
        try:
            shutil.copytree(CASES / "repo", repo, dirs_exist_ok=True)
            if variant == "without a map":
                (repo / MAP_FILE).unlink()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            target = repo / "src" / "screens" / "new" / "view.rs"
            env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}

            def hook(script, event, **extra):
                payload = {"hook_event_name": event, "session_id": "selftest", "cwd": str(repo), **extra}
                run = subprocess.run([sys.executable, str(gates / script)], input=json.dumps(payload),
                                     capture_output=True, text=True, encoding="utf-8", env=env, cwd=repo)
                return run.returncode, run.stderr

            if variant == "with a map":  # an edit far from every pattern does not bring up the old repetition
                old = repo / "src" / "screens" / "d" / "view.rs"
                hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(old)})
                old.write_text(old.read_text(encoding="utf-8") + "\npub const HEIGHT: f32 = 2.0;\n", encoding="utf-8")
                code, _ = hook("stop_gate.py", "Stop")
                if code != 0:
                    problems.append(f"edit away from the patterns: Stop exited {code}, expected 0")
            hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(target)})
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((CASES / "new_screen.rs").read_text(encoding="utf-8"), encoding="utf-8")
            code, report = hook("stop_gate.py", "Stop")
            if code != 2:
                problems.append(f"{variant}: Stop exited {code}, expected 2")
            needed = ["new component", "New component/script started", "src/components/visual.rs"] if variant == "with a map" else [
                "No architecture map", "New component/script started"]
            problems += [f"{variant}: briefing without {n!r}" for n in needed if n not in report]
            if "flex_1().min_w_0()" in report:
                problems.append(f"{variant}: framework idiom became a finding")
            code, _ = hook("stop_gate.py", "Stop")
            if code != 0:
                problems.append(f"{variant}: repeated Stop exited {code}, expected 0")
            if variant == "with a map":
                mod_gate = HERE / "hooks" / "modular_gate.py"
                agy_in = {"hookEventName": "PreInvocation", "conversationId": "agy", "cwd": str(repo),
                          "lastUserInput": "please refactor this module"}
                run = subprocess.run([sys.executable, str(mod_gate)], input=json.dumps(agy_in),
                                     capture_output=True, text=True, encoding="utf-8", cwd=repo)
                out = json.loads(run.stdout or "{}")
                if run.returncode != 0 or "injectSteps" not in out or not (repo / ".git/tautology/agy.refactor").is_file():
                    problems.append("Antigravity PreInvocation did not set refactor flag or inject reminder")
                ts_types = repo / "src" / "typings" / "models.ts"
                ts_types.parent.mkdir(parents=True, exist_ok=True)
                ts_types.write_text(
                    "export type AdminItem = BaseItem & {\n  id: string\n  variant: 'a' | 'b'\n  onClose: () => void\n  title: string\n  comment: string\n  status: string\n}\n"
                    "export type ShopperItem = BaseItem & {\n  id: string\n  variant: 'a' | 'b'\n  onClose: () => void\n  title: string\n  comment: string\n  status: string\n}\n",
                    encoding="utf-8",
                )
                ts_patterns = findings(repo, Architecture(repo), {"src/typings/models.ts": None}, {".ts"})
                if ts_patterns:
                    problems.append(f"TypeScript type declarations triggered pattern: {[str(f) for f in ts_patterns]!r}")
                sig_file = repo / "src" / "screens" / "sigs.rs"
                sig_file.write_text(
                    "fn first_view(\n"
                    "    kind: &'static str,\n"
                    "    index: usize,\n"
                    "    offset: usize,\n"
                    "    shown: usize,\n"
                    "    total: usize,\n"
                    "    palette: Palette,\n"
                    ") -> Div { div() }\n"
                    "fn second_view(\n"
                    "    kind: &'static str,\n"
                    "    index: usize,\n"
                    "    offset: usize,\n"
                    "    shown: usize,\n"
                    "    total: usize,\n"
                    "    palette: Palette,\n"
                    ") -> Div { div() }\n",
                    encoding="utf-8",
                )
                sig_patterns = findings(repo, Architecture(repo), {"src/screens/sigs.rs": None}, {".rs"})
                if sig_patterns:
                    problems.append(f"Rust parameter list triggered pattern: {[str(f) for f in sig_patterns]!r}")
                for call_name in ("call_a.rs", "call_b.rs"):
                    (repo / "src" / "screens" / call_name).write_text(
                        "fn draw() {\n"
                        "    let pair = self.detail_transition.wrap_views(\n"
                        "        detail_visible,\n"
                        "        &self.host_bounds,\n"
                        "        self.palette,\n"
                        "        window,\n"
                        "        list_view,\n"
                        "        detail_view,\n"
                        "        leaving_view,\n"
                        "    );\n"
                        "}\n",
                        encoding="utf-8",
                    )
                call_patterns = findings(repo, Architecture(repo), {"src/screens/call_a.rs": None}, {".rs"})
                if call_patterns:
                    problems.append(f"Multi-line single call triggered pattern: {[str(f) for f in call_patterns]!r}")
                for ui_name in ("ui_ctx_a.rs", "ui_ctx_b.rs"):
                    (repo / "src" / "screens" / ui_name).write_text(
                        "fn sync_panel(self_ref: &Self, window: &mut Window) {\n"
                        "    let palette = self.palette;\n"
                        "    let frame_bounds = *self.host_bounds.borrow();\n"
                        "    let animated = animations_enabled(palette.animate);\n"
                        "    let surface = if palette.glass_on() { palette.box_fill() } else { palette.bg };\n"
                        "    let edge = palette.glass_edge(palette.box_border);\n"
                        "    self.transition.prepare(frame_bounds, surface, edge, animated, window);\n"
                        "}\n",
                        encoding="utf-8",
                    )
                ui_found = findings(repo, Architecture(repo), {"src/screens/ui_ctx_a.rs": None}, {".rs"})
                if not ui_found or "src/components/visual.rs" not in ui_found[0].fix:
                    problems.append(f"GPUI context block did not route to visual.rs: {[str(f) for f in ui_found]!r}")
                else:
                    brief_text = briefing(ui_found, Architecture(repo), "edited ui_ctx_a.rs")
                    brief_line = next((l for l in brief_text.splitlines() if l.startswith("1. ")), "")
                    if "ui_ctx_a.rs:" not in brief_line or "[pattern]" not in brief_line:
                        problems.append(f"briefing() line missing path/tag for --accept: {brief_line!r}")
                    stripped = re.sub(r'"[^"]*"', "", brief_line)
                    bad_accept = subprocess.run(
                        [sys.executable, str(HERE / "patterns.py"), "--accept", stripped, "--reason", "stripped test"],
                        capture_output=True, text=True, encoding="utf-8", env=env, cwd=repo,
                    )
                    if bad_accept.returncode == 0:
                        problems.append("quote-stripped --accept exited 0, expected failure")
                    good_accept = subprocess.run(
                        [sys.executable, str(HERE / "patterns.py"), "--accept", "-", "--reason", "stdin accept test"],
                        input=brief_line, capture_output=True, text=True, encoding="utf-8", env=env, cwd=repo,
                    )
                    if good_accept.returncode != 0:
                        problems.append(f"stdin --accept failed: {good_accept.stderr or good_accept.stdout}")
                    after_accept = subprocess.run(
                        [sys.executable, str(HERE / "patterns.py"), "src/screens/ui_ctx_a.rs"],
                        capture_output=True, text=True, encoding="utf-8", env=env, cwd=repo,
                    )
                    if "ui_ctx_a.rs:" in after_accept.stdout:
                        problems.append(f"finding still active after --accept -: {after_accept.stdout}")
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    for problem in problems:
        print(f"HOOK    {problem}")
    print(f"hook: {'ok' if not problems else f'{len(problems)} problem(s)'}")
    return problems


def _resolve_touched(repo, arch, raw_targets):
    touched = {}
    all_files = None
    for raw in raw_targets:
        path = Path(raw).resolve()
        if not path.is_relative_to(repo):
            print(f"Error: path outside repository: {raw}", file=sys.stderr)
            return None
        rel = path.relative_to(repo).as_posix()
        if path.is_dir():
            if all_files is None:
                all_files = list(code_files(repo, arch))
            prefix = "" if rel == "." else rel.rstrip("/") + "/"
            for candidate in all_files:
                if not prefix or candidate.startswith(prefix):
                    touched[candidate] = None
        else:
            touched[rel] = None
    return touched


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*")
    parser.add_argument("--all", action="store_true", help="the whole repository")
    parser.add_argument("--accept", help="finding line (or '-' to read from stdin) that is not a repetition to extract")
    parser.add_argument("--reason", default="")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--record", action="store_true", help="with --selftest: records the output as expected")
    args = parser.parse_args()
    if args.accept:
        return core.accept(args.accept, args.reason, store=core.project_subdir(core.REPO, "modular") / "accepted.txt", tool="patterns.py")
    if args.selftest:
        return selftest(record=args.record)
    if not args.all and not args.files:
        parser.print_help()
        return 2
    repo = core.REPO
    arch = Architecture(repo)
    touched = None if args.all else _resolve_touched(repo, arch, args.files)
    if not args.all and touched is None:
        return 2
    found = findings(repo, arch, touched)
    for f in found:
        print(f)
    if not arch.defined:
        print(f"no destination map ({MAP_FILE.as_posix()})" + (f"; architecture described in {', '.join(arch.docs)}"
                                                              if arch.docs else "; the repository defines no architecture"))
    print(f"{len(found)} pattern(s).")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
