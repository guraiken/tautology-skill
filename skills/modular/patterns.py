"""Hunts code that should be one piece: the same call chain in several files and the same copied
block. Says where to extract it, by the codebase's architecture.

    python patterns.py a.rs b.rs                         # patterns that touch these files
    python patterns.py --all                             # the whole repository
    python patterns.py --accept "<finding>" --reason "<why>"
    python patterns.py --selftest                        # cases/ vs cases/expected.txt, and the hook

Calling the same function in many places is reuse, not a defect. The defect is the same sequence
copied: it becomes one function in the right layer, and each place calls it.
Project settings: .claude/modular/architecture.md (the destination map) and .claude/modular/accepted.txt.
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"
sys.path.insert(0, str(HERE.parent / "tautology"))
import core  # noqa: E402  (the tautology skill: findings, accepted findings, test-file detection)
from core import Finding  # noqa: E402

ACCEPTED_FILE = core.project_subdir(core.REPO, "modular") / "accepted.txt"
MAP_FILE = Path(".claude") / "modular" / "architecture.md"

CODE = {".rs", ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".ps1", ".sh", ".bat", ".cmd"}
SKIP = {"target", "node_modules", "dist", "build", ".venv", "__pycache__", "fixtures", "vendor", ".git"}
MIN_CALLS = 4      # chain: calls in a row
MIN_COUNT = 4      # chain: times in total
MIN_FILES = 3      # chain: different files
MIN_BLOCK = 6      # block: equal non-blank lines
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
UI = re.compile(
    r"\bpx\(|\b(div|h_flex|v_flex)\(\)|\.child\(|\.text_\w+\(|\.bg\(|\.border\w*\(|\.rounded\w*\(|"
    r"\b(?:gpui::|AnyElement|IntoElement|BoundsSlot|Window)\b|\bself\.palette\b|"
    r"\b(?:kit|visual)::\w+\(|\bcx\.(?:entity|listener|notify|emit)\(|"
    r"\.(?:on_click|on_change|on_hover|on_mouse_\w+|hint|cursor_hint|tooltip|accessibility_label|into_any_element)\(|"
    r"\b(?:useState|useEffect|useCallback|useMemo|QWidget|QPushButton|QLabel|QVBoxLayout|QHBoxLayout)\b|"
    r"className=|<\w+[\s>]|\bstyle="
)
# Style methods (GPUI, Tailwind-like): a chain of only these is a visual piece, even without px() or a colour.
STYLE = re.compile(r"\.(absolute|relative|top_\w+|left_\w+|right_\w+|bottom_\w+|inset_\w+|size_\w+|rounded\w*|"
                   r"border\w*|bg|shadow\w*|opacity|cursor_\w+|text_\w+|font_\w+|line_height|flex\w*|items_\w+|"
                   r"justify_\w+|gap\w*|p[xytblr]?|m[xytblr]?|w_\w+|h_\w+|overflow_\w+)\(")
COMPONENT = re.compile(r"(^|/)(components?|screens?|views?|pages?|widgets?|ui)/|\.(tsx|jsx|vue|svelte)$")


# ----------------------------------------------------------------------------- architecture
class Architecture:
    """Destination map: .claude/modular/architecture.md of the repository. Without it, looks for a description."""

    def __init__(self, repo):
        self.repo, self.layers, self.destinations, self.outside = Path(repo), [], {}, []
        self.components, self.common, self.verify, self.source = [], set(COMMON), "", ""
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
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=repo, capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout
    return [l for l in out.splitlines() if l]


# ------------------------------------------------------------------------------- index
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


def production(text, suffix):
    """Production code: Rust's #[cfg(test)] modules and TS type/interface blocks stay out (preserving line numbers)."""
    if suffix in (".ts", ".tsx"):
        lines = text.splitlines()
        out, in_type, depth = [], False, 0
        for line in lines:
            if not in_type and re.match(
                r"^\s*(?:export\s+)?(?:declare\s+)?(?:type\s+[A-Za-z_]\w*(?:<[^>]*>)?\s*=\s*\{|interface\s+[A-Za-z_]\w*\b)",
                line,
            ):
                in_type, depth = True, line.count("{") - line.count("}")
                out.append("")
                if depth <= 0 and "{" in line:
                    in_type = False
                continue
            if in_type:
                depth += line.count("{") - line.count("}")
                out.append("")
                if depth <= 0 and ("{" in line or "}" in line or line.rstrip().endswith(";")):
                    in_type = False
            else:
                out.append(line)
        return "\n".join(out)
    if suffix != ".rs":
        return text
    lines = text.splitlines()
    out, in_test, depth = [], False, 0
    for line in lines:
        if not in_test and re.match(r"^\s*#\[cfg\(test\)\]", line):
            in_test, depth = True, 0
            out.append("")
            continue
        if in_test:
            depth += line.count("{") - line.count("}")
            out.append("")
            if depth <= 0 and ("{" in line or "}" in line or line.rstrip().endswith(";")):
                in_test = False
        else:
            out.append(line)
    return "\n".join(out)


ARGS = r"\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)"
CHAIN = re.compile(r"(?:\s*\.\s*[A-Za-z_]\w*\s*" + ARGS + r"){" + str(MIN_CALLS) + r",}")
CALL = re.compile(r"\.\s*([A-Za-z_]\w*)\s*(" + ARGS + r")")
STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\x60[^\x60]*\x60')


def blank_strings(text):
    return STRING.sub(lambda m: '"S"' + "\n" * m.group(0).count("\n"), text)


class Index:
    def __init__(self, repo, arch, suffixes=None):
        self.repo, self.arch = Path(repo), arch
        self.chains = []   # (rel, line, [(name, args)])
        self.end = {}      # (rel, line) -> last line of the piece
        self.lines = {}    # rel -> [(line, normalized)]
        self.fns = []      # (rel, line, name, is_public, is_free_ui_helper)
        for rel in code_files(repo, arch, suffixes):
            path = self.repo / rel
            try:
                if path.stat().st_size > 1_500_000:
                    continue
                text = production(path.read_text(encoding="utf-8", errors="replace"), path.suffix)
            except OSError:
                continue
            flat = blank_strings(text)
            self.collect(rel, flat, flat, 0)
            self.lines[rel] = normalized_lines(flat)
            self.collect_fns(rel, text)

    def collect_fns(self, rel, text):
        for number, line in enumerate(text.splitlines(), 1):
            m = re.match(r"^(\s*)(pub(?:\([^)]*\))?\s+)?fn\s+([A-Za-z_]\w*)\s*\(([^)]*)\)(?:\s*->\s*([^{;]+))?", line)
            if not m:
                continue
            indent, pub, name, params, ret = m.group(1), bool(m.group(2)), m.group(3), m.group(4), (m.group(5) or "").strip()
            free_ui = (not indent and "self" not in params
                       and bool(re.search(r"\b(Div|AnyElement|Button|Stateful|ToggleGroup|Svg)\b", ret)))
            self.fns.append((rel, number, name, pub, free_ui))

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


def normalized_lines(flat):
    out = []
    for number, raw in enumerate(flat.splitlines(), 1):
        line = re.sub(r"\s+", " ", raw.strip())
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
    for key, spots in windows.items():
        spots = [s for s in spots if s not in covered]
        distinct = []
        for rel, i in spots:  # in one file, overlapping blocks are the same block
            if all(rel != r or abs(i - j) >= MIN_BLOCK for r, j in distinct):
                distinct.append((rel, i))
        if len(distinct) < 2:
            continue
        size = MIN_BLOCK
        while all(i + size < len(index.lines[r]) for r, i in distinct) and len(
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


PARAM_DECL = re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:mut\s+)?[A-Za-z_]\w*\s*\??:\s*([^=]+?),?\s*$")
PARAM_TYPE = re.compile(
    r"^(?:&(?:'\w+\s+)?(?:mut\s+)?|\*\s*(?:const|mut)\s+)?"
    r"(?:(?:impl|dyn|fn)\b|[A-Z]\w*|[a-z_]\w*::[A-Za-z_]\w*|[\[(]|"
    r"(?:usize|isize|bool|str|char|f32|f64|[iu](?:8|16|32|64|128)|"
    r"string|number|boolean|void|any|unknown|never|int|float|bytes|dict|list|set|tuple)\b)"
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
    return bool(calls) and re.fullmatch(r"(\s*\.\s*\w+\s*\(.*\)\s*)+[,;)]*", text) is not None and all(
        n in arch.common and TRIVIAL_ARG.match(re.sub(r"\s+", "", a[1:-1])) for n, a in calls)


def destination(where, code, arch):
    """The layer the extracted piece can live in: every place imports it, none imports upward."""
    files = {rel for rel, _ in where}
    if len(files) == 1:
        return "a function in the same file (one place uses it)", None
    if not arch.defined:
        return None, None
    layers = {arch.layer(rel) for rel in files} - {None}
    ui = bool(UI.search(code) or STYLE.search(code))
    if not layers:
        return arch.destinations.get(max(arch.destinations), ""), None
    low = min(layers)
    if ui:
        visual = min((n for n, d in arch.destinations.items() if n >= 3), default=None)
        if visual is None or low < visual:
            return None, (f"uses UI and appears in layer {low}, which cannot import layer "
                          f"{visual}: split off the part without UI first")
        return arch.destinations[visual], None
    target = min(low, 2) if 2 in arch.destinations or low < 2 else low
    target = max((n for n in arch.destinations if n <= target), default=min(arch.destinations))
    return arch.destinations[target], None


def helper_patterns(index, existing):
    """Screen-local UI helper functions that duplicate a shared component function or another screen's helper."""
    already = {(r, n) for _, _, where, _ in existing for r, n in where}
    shared = {}
    local = defaultdict(list)
    for rel, line, name, pub, free_ui in index.fns:
        layer = index.arch.layer(rel)
        if pub and free_ui and layer is not None and layer <= 3:
            shared.setdefault(name, (rel, line))
        elif not pub and free_ui and (layer is None or layer >= 4) and index.arch.is_component(rel) and (rel, line) not in already:
            local[name].append((rel, line))
    out = []
    for name, spots in sorted(local.items()):
        where = tuple(sorted(spots))
        if name in shared:
            s_rel, s_line = shared[name]
            for rel, line in where:
                index.end[(rel, line)] = line
            out.append(("helper", f"fn {name} (already in {s_rel}:{s_line})", (*where, (s_rel, s_line)), "px(0.)"))
        elif len({r for r, _ in where}) >= 2:
            for rel, line in where:
                index.end[(rel, line)] = line
            out.append(("helper", f"fn {name}", where, "px(0.)"))
    return out


def findings(repo, arch, touched=None, suffixes=None):
    """Findings; touched {file: changed lines or None = all}: only patterns with a place in those lines."""
    index = Index(repo, arch, suffixes)
    out = []
    found_patterns = chain_patterns(index) + block_patterns(index)
    found_patterns += helper_patterns(index, found_patterns)
    for kind, label, where, code in found_patterns:
        mine = [w for w in where if touched is None or (w[0] in touched and (touched[w[0]] is None or any(
            n in touched[w[0]] for n in range(w[1], index.end.get(w, w[1]) + 1))))]
        if not mine:
            continue
        files = sorted({r for r, _ in where})
        target, conflict = destination(where, code, arch)
        places = ", ".join(f"{r}:{n}" for r, n in where[:6]) + (f" (+{len(where) - 6})" if len(where) > 6 else "")
        if kind == "chain":
            problem = f'chain "{label[:110]}" repeated {len(where)}x in {len(files)} file(s) ({places})'
        elif kind == "helper":
            problem = f'local UI helper "{label}" in {len(files)} file(s) ({places})'
        else:
            problem = (f'block of {label.split(":")[0]} repeated in {len(where)} places: '
                       f'"{label.split(": ", 1)[1]}" ({places})')
        fix = (f"layer conflict: {conflict}" if conflict else
               f"extract to {target}" if target else "extract to the destination the chosen architecture gives")
        f = Finding(mine[0][0], mine[0][1], "pattern", problem, fix)
        f.where, f.code, f.conflict, f.target = where, code, conflict, target
        out.append(f)
    store = core.project_subdir(repo, "modular") / "accepted.txt"
    if not core.SELFTEST:
        scanned_whole = list(index.lines) if touched is None else [
            rel for rel, lines in touched.items() if lines is None
        ]
        core.prune_accepted(scanned_whole, out, "patterns.py", store=store, repo=repo)
    taken = {(p, r, s) for p, r, s, *_ in core.load_accepted(store)} if not core.SELFTEST else set()
    return [f for f in out if (f.path, f.rule, f.subject) not in taken]


# ------------------------------------------------------------------------------- briefing
OPTIONS = [
    ("Shared layers", "components/ (visual pieces and components) + lib/ or utils/ (logic without UI); "
                      "screens only assemble pieces"),
    ("Feature modules", "each feature has its folder; what two features use moves up to shared/"),
    ("Local function", "extract inside the same file, without new modules (smallest change)"),
]


def briefing(found, arch, trigger, created=()):
    """The text the hook returns to the agent: what to extract, where to, and how without changing behaviour."""
    lines = [f"Modularization (skill modular, {(HERE / 'SKILL.md').as_posix()}). Trigger: {trigger}."]
    if created:
        where = ", ".join(arch.destinations.values()) if arch.defined else "components/, scripts/, bin/, lib/ or utils/"
        lines.append(
            f"New component/script started ({', '.join(created[:4])}): ask yourself whether an equivalent "
            f"component, helper or script already exists in the repository ({where}). If one exists, reuse or "
            "extend it and delete the duplicate."
        )
    if not arch.defined and found:
        if arch.docs:
            lines.append(f"Architecture docs exist ({', '.join(arch.docs)}), but {MAP_FILE.as_posix()} is missing; follow the existing structure.")
        elif any("same file" not in f.fix for f in found[:PER_ROUND]):
            lines.append("No architecture map: follow the existing folder structure (or extract locally inside the same file).")
    if found:
        lines.append("Repeated patterns that involve this session's files:")
        for n, f in enumerate(found[:PER_ROUND], 1):
            lines.append(f"{n}. {f}")
        if len(found) > PER_ROUND:
            lines.append(f"(+{len(found) - PER_ROUND} in the next round or in patterns.py --all)")
        lines.append(
            "Extract shared logic (parameterizing only what varies; skip files with unrelated git edits; "
            + (f"verify with {arch.verify}). " if arch.verify else "then build/test). ")
            + "False positive? Accept silently: "
            + core.accept_command(HERE / "patterns.py") + ".")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------- CLI
def selftest(record=False):
    """cases/repo is a fake repository with an architecture; expected.txt is the right output."""
    core.SELFTEST = True
    repo = CASES / "repo"
    got = [str(f) for f in findings(repo, Architecture(repo))]
    if record:
        return core.compare_expected(got, CASES / "expected.txt", record=True)
    differs = core.compare_expected(got, CASES / "expected.txt")
    problems = hook_selftest()
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


def hook_selftest():
    """The hook in a temporary repository: with a map it gives the destination; without, the options; no repeats."""
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
                    "export type AdminItem = {\n  id: string\n  title: string\n  comment: string\n  status: string\n  createdAt: string\n  updatedAt: string\n}\n"
                    "export type ShopperItem = {\n  id: string\n  title: string\n  comment: string\n  status: string\n  createdAt: string\n  updatedAt: string\n}\n",
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
    repo = core.REPO
    arch = Architecture(repo)
    touched = None if args.all else {Path(f).resolve().relative_to(repo).as_posix(): None for f in args.files}
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
