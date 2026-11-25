"""Module boundaries: imports that erode a modular architecture.

    python boundaries.py --all              # the whole repository
    python boundaries.py src/features/x     # imports made by these files or folders
    python boundaries.py --selftest         # cases/boundaries vs cases/boundaries/expected.txt

Three rules, from the most to the least certain:
- layer: a file imports a higher layer of .claude/modular/architecture.md (`layer N:`), or breaks a
  `forbid: <from> -> <to>` line. Without a map, only shared code (lib/, utils/, shared/, core/, domain/,
  components/ui/) importing feature/page code (features/, pages/, screens/, views/, routes/, app/).
- deep import: code outside a feature reaches into that feature's files although the feature has an
  index (its public API).
- cycle: two or more modules (feature folders, or top folders under src/ app/ lib/) import each other.
  Type-only and lazy (in-function, dynamic) imports do not count for cycles.

Reads JS/TS (also Vue/Svelte scripts) and Python. Imports of packages are ignored; only files of the
repository count. Rust is left out (the compiler already enforces module privacy).
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import patterns  # noqa: E402  (Architecture, tracked files, the tautology core)
from patterns import core, Finding  # noqa: E402

JS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"}
PY = {".py"}
RESOLVE_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")
CONTAINERS = {"src", "app", "lib", "packages"}
SHARED_SEGS = {"lib", "libs", "utils", "util", "shared", "common", "core", "domain", "helpers"}
UPPER_SEGS = {"features", "feature", "modules", "pages", "screens", "views", "routes", "app"}

JS_STATIC = re.compile(
    r"^[ \t]*(import|export)\s+(type\s+)?"
    r"((?:[\w$]+\s*,?\s*)?(?:\{[^}]*\}|\*\s*as\s+[\w$]+|\*)?)\s*from\s*(['\"])([^'\"\n]+)\4", re.M)
JS_SIDE = re.compile(r"^[ \t]*import\s*(['\"])([^'\"\n]+)\1", re.M)
JS_REQUIRE = re.compile(r"\brequire\(\s*(['\"])([^'\"\n]+)\1\s*\)")
JS_DYNAMIC = re.compile(r"\bimport\(\s*(['\"])([^'\"\n]+)\1\s*\)")
PY_FROM = re.compile(r"^([ \t]*)from\s+(\.*)([\w.]*)\s+import\s+\(?([^\n#]*)", re.M)
PY_IMPORT = re.compile(r"^([ \t]*)import\s+([\w.]+(?:\s*,\s*[\w.]+)*)", re.M)
PY_TYPE_CHECKING = re.compile(r"^([ \t]*)if\s+(?:typing\.)?TYPE_CHECKING\s*:", re.M)


class Edge:
    __slots__ = ("src", "dst", "line", "spec", "type_only", "lazy")

    def __init__(self, src, dst, line, spec, type_only=False, lazy=False):
        self.src, self.dst, self.line, self.spec = src, dst, line, spec
        self.type_only, self.lazy = type_only, lazy


# ----------------------------------------------------------------------------- resolution
def _strip_jsonc(text):
    """tsconfig.json allows comments and trailing commas; strips both outside strings."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def js_aliases(repo):
    """[(prefix, [target dirs relative to the repo])] from tsconfig/jsconfig `paths`, plus baseUrl.
    Without a readable config: `@/` and `~/` -> `src/` when src/ exists."""
    aliases, base_url = [], None
    for name in ("tsconfig.json", "tsconfig.app.json", "jsconfig.json"):
        cfg = Path(repo) / name
        if not cfg.is_file():
            continue
        try:
            opts = json.loads(_strip_jsonc(cfg.read_text(encoding="utf-8", errors="replace"))).get("compilerOptions") or {}
        except (ValueError, AttributeError):
            continue
        raw_base = opts.get("baseUrl")
        base = (_norm(str(raw_base)) or "") if raw_base is not None else ""
        if raw_base is not None:
            base_url = base
        for key, targets in (opts.get("paths") or {}).items():
            prefix = key[:-1] if key.endswith("*") else key
            dirs = []
            for t in targets if isinstance(targets, list) else []:
                t = t[:-1] if t.endswith("*") else t
                t = re.sub(r"^\./", "", t)
                dirs.append("/".join(p for p in (base, t) if p))
            if prefix and dirs:
                aliases.append((prefix, dirs))
    if not aliases and (Path(repo) / "src").is_dir():
        aliases = [("@/", ["src/"]), ("~/", ["src/"])]
    aliases.sort(key=lambda a: -len(a[0]))
    return aliases, base_url


def _norm(path):
    parts = []
    for p in path.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(p)
    return "/".join(parts)


def _js_candidates(base):
    if base is None:
        return
    yield base
    for ext in RESOLVE_EXT:
        yield base + ext
    stem = re.sub(r"\.(m|c)?js$", "", base)
    if stem != base:  # ESM TypeScript imports "./x.js" for ./x.ts
        for ext in (".ts", ".tsx", ".mts", ".cts"):
            yield stem + ext
    for ext in RESOLVE_EXT:
        yield base + "/index" + ext


def resolve_js(spec, src, files, aliases, base_url):
    if spec.startswith("."):
        bases = [_norm(str(Path(src).parent.as_posix()) + "/" + spec)]
    else:
        bases = [_norm(d + spec[len(prefix):]) for prefix, dirs in aliases if spec.startswith(prefix) for d in dirs]
        if not bases and base_url is not None:
            bases = [_norm(f"{base_url}/{spec}" if base_url else spec)]
    for base in bases:
        for cand in _js_candidates(base):
            if cand in files:
                return cand
    return None


def _py_module(mod_path, files):
    for cand in (mod_path + ".py", mod_path + "/__init__.py"):
        if cand in files:
            return cand
    return None


def resolve_py(dots, module, names, src, files):
    """Files a `from <dots><module> import <names>` (or `import <module>`) reaches; [] for packages."""
    if dots:
        base = Path(src).parent.as_posix()
        for _ in range(len(dots) - 1):
            base = Path(base).parent.as_posix()
        roots = ["" if base == "." else base]
    else:
        roots = ["", "src"]
    out = []
    for root in roots:
        stem = "/".join(p for p in (root, module.replace(".", "/")) if p)
        if not stem:
            continue
        if module:
            hit = _py_module(stem, files)
            if hit:
                subs = [_py_module(f"{stem}/{n}", files) for n in names] if hit.endswith("__init__.py") else []
                out += [s for s in subs if s] or [hit]
                break
        else:  # from . import a, b
            out += [s for s in (_py_module(f"{stem}/{n}", files) for n in names) if s]
            if out:
                break
    return out


# ----------------------------------------------------------------------------- import graph
def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def _js_edges(rel, text, files, aliases, base_url):
    out, seen = [], set()

    def add(spec, pos, type_only=False, lazy=False):
        dst = resolve_js(spec, rel, files, aliases, base_url)
        if dst and dst != rel:
            line = _line_of(text, pos)
            if (dst, line) not in seen:
                seen.add((dst, line))
                out.append(Edge(rel, dst, line, spec, type_only, lazy))

    for m in JS_STATIC.finditer(text):
        clause = m.group(3) or ""
        names = re.findall(r"\{([^}]*)\}", clause)
        all_typed = bool(names) and not re.sub(r"\{[^}]*\}", "", clause).strip(" ,") and all(
            item.strip().startswith("type ") for item in names[0].split(",") if item.strip())
        add(m.group(5), m.start(1), type_only=bool(m.group(2)) or all_typed)
    for m in JS_SIDE.finditer(text):
        add(m.group(2), m.start())
    for m in JS_REQUIRE.finditer(text):
        prefix = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        add(m.group(2), m.start(), lazy=prefix[:1] in (" ", "\t"))  # indented = inside a function
    for m in JS_DYNAMIC.finditer(text):
        add(m.group(2), m.start(), lazy=True)
    return out


def _py_edges(rel, text, files):
    out = []
    typed_blocks = []  # (start, end) offsets of `if TYPE_CHECKING:` bodies
    for m in PY_TYPE_CHECKING.finditer(text):
        indent = len(m.group(1))
        end = m.end()
        for line in re.finditer(r"^([ \t]*)(\S.*)$", text[m.end():], re.M):
            if len(line.group(1)) <= indent:
                end = m.end() + line.start()
                break
        else:
            end = len(text)
        typed_blocks.append((m.end(), end))

    def flags(m):
        typed = any(a <= m.start() < b for a, b in typed_blocks)
        return typed, bool(m.group(1)) and not typed

    for m in PY_FROM.finditer(text):
        names = [n.strip().split(" as ")[0].strip(" ()") for n in m.group(4).split(",")]
        typed, lazy = flags(m)
        for dst in resolve_py(m.group(2), m.group(3), [n for n in names if n and n != "*"], rel, files):
            if dst != rel:
                out.append(Edge(rel, dst, _line_of(text, m.start(2)), m.group(2) + m.group(3), typed, lazy))
    for m in PY_IMPORT.finditer(text):
        typed, lazy = flags(m)
        for mod in m.group(2).split(","):
            for dst in resolve_py("", mod.strip(), [], rel, files):
                if dst != rel:
                    out.append(Edge(rel, dst, _line_of(text, m.start(2)), mod.strip(), typed, lazy))
    return out


def import_graph(repo, arch):
    repo = Path(repo)
    files = {rel for rel in patterns.code_files(repo, arch, JS | PY)}
    aliases, base_url = js_aliases(repo)
    edges = []
    for rel in sorted(files):
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        suffix = Path(rel).suffix
        edges += _js_edges(rel, text, files, aliases, base_url) if suffix in JS else _py_edges(rel, text, files)
    return files, edges


# ----------------------------------------------------------------------------- rules
def _area(rel):
    parts = rel.split("/")
    start = 1 if len(parts) > 2 and parts[0] in CONTAINERS else 0  # src/, or a Python package named app/
    for i, seg in enumerate(parts[:-1]):
        if i < start:
            continue
        if seg == "components" and i + 1 < len(parts) - 1 and parts[i + 1] == "ui":
            return "shared"
        if seg in UPPER_SEGS:
            return "upper"
        if seg in SHARED_SEGS:
            return "shared"
    return None


def module_of(rel, arch):
    feature = arch.feature_of(rel)
    if feature:
        return feature
    parts = rel.split("/")
    if len(parts) > 2 and parts[0] in CONTAINERS:
        return "/".join(parts[:2])
    if len(parts) > 2:
        return parts[0]
    return rel  # a loose file (src/index.ts, src/env.ts): an entry point or config, a node of its own


def _public_index(feature, files):
    return next((f"{feature}/index{ext}" for ext in RESOLVE_EXT if f"{feature}/index{ext}" in files), None)


def layer_findings(edges, arch):
    out = []
    for e in edges:
        a, b = arch.layer(e.src), arch.layer(e.dst)
        banned = next(((f, t) for f, t in arch.forbid if e.src.startswith(f) and e.dst.startswith(t)), None)
        if banned:
            out.append(Finding(e.src, e.line, "boundary",
                               f'imports "{e.dst}", which the map forbids ({banned[0]} -> {banned[1]})',
                               "invert the dependency: pass it in (parameter, callback, interface) or move the shared part below both"))
        elif a is not None and b is not None and b > a:
            out.append(Finding(e.src, e.line, "boundary",
                               f'layer {a} imports "{e.dst}" from layer {b} (a lower layer must not depend on a higher one)',
                               f"move what layer {a} needs down to layer {a} or lower, or receive it as a parameter/callback"))
        elif not arch.layers and _area(e.src) == "shared" and _area(e.dst) == "upper":
            out.append(Finding(e.src, e.line, "boundary",
                               f'shared code imports "{e.dst}" from feature/page code',
                               "shared code must not know its callers: move the piece into the shared layer or pass it in"))
    return out


def deep_import_findings(edges, files, arch):
    out = []
    for e in edges:
        if Path(e.src).suffix not in JS:
            continue
        feature = arch.feature_of(e.dst)
        if not feature or arch.feature_of(e.src) == feature:
            continue
        public = _public_index(feature, files)
        if public and e.dst != public:
            out.append(Finding(e.src, e.line, "boundary",
                               f'reaches into "{e.dst}" past the public API of {feature} ({Path(public).name})',
                               f"import it from {feature} (export it from {Path(public).name} if it is meant to be shared)"))
    return out


def _sccs(graph):
    """Tarjan's strongly connected components, iterative, deterministic (sorted nodes and neighbours)."""
    index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            node, it = work[-1]
            nxt = next(it, None)
            if nxt is None:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    out.append(sorted(comp))
            elif nxt not in index:
                index[nxt] = low[nxt] = counter[0]
                counter[0] += 1
                stack.append(nxt)
                on.add(nxt)
                work.append((nxt, iter(sorted(graph.get(nxt, ())))))
            elif nxt in on:
                low[node] = min(low[node], index[nxt])
    return [c for c in out if len(c) > 1]


def _cycle_path(comp, graph):
    """A shortest cycle through the first module of the component (BFS), as [m0, m1, ..., m0]."""
    start, members = comp[0], set(comp)
    prev, queue = {start: None}, [start]
    while queue:
        node = queue.pop(0)
        for nxt in sorted(graph[node]):
            if nxt not in members:
                continue
            if nxt == start:
                seq, p = [], node
                while p is not None:
                    seq.append(p)
                    p = prev[p]
                return seq[::-1] + [start]
            if nxt not in prev:
                prev[nxt] = node
                queue.append(nxt)
    return comp + [start]


def cycle_findings(edges, arch, touched=None):
    graph, hop_edges = defaultdict(set), defaultdict(list)
    for e in edges:
        if e.type_only or e.lazy or Path(e.src).parent == Path(e.dst).parent:
            continue  # same folder: a file-level matter, not a module boundary
        a, b = module_of(e.src, arch), module_of(e.dst, arch)
        if a != b:
            graph[a].add(b)
            graph.setdefault(b, set())
            hop_edges[(a, b)].append(e)
    out = []
    for comp in _sccs(graph):
        path = _cycle_path(comp, graph)
        hops = list(zip(path, path[1:]))
        examples = [sorted(hop_edges[h], key=lambda e: (e.src, e.line))[0] for h in hops]
        if touched is not None and not any(
                e.src in touched and (touched[e.src] is None or e.line in touched[e.src])
                for h in hops for e in hop_edges[h]):
            continue
        first = examples[0]
        shown = ", ".join(f"{e.src}:{e.line} -> {e.dst}" for e in examples[:4])
        out.append(Finding(first.src, first.line, "boundary",
                           f'module cycle "{" -> ".join(path)}" ({shown})',
                           "break one hop: move the shared piece to a lower module, or depend on an interface/callback instead"))
    return out


def findings(repo, arch, touched=None):
    """Boundary findings; touched {file: changed lines or None = all}: only imports made on those lines
    (and cycles with such an import on one of their hops)."""
    files, edges = import_graph(repo, arch)
    mine = edges if touched is None else [
        e for e in edges if e.src in touched and (touched[e.src] is None or e.line in touched[e.src])]
    return layer_findings(mine, arch) + deep_import_findings(mine, files, arch) + cycle_findings(edges, arch, touched)


# ----------------------------------------------------------------------------- CLI & selftest
def selftest():
    """cases/boundaries/<case>/ are fake repositories; expected.txt lines: `<case> + <text>` (a finding
    contains it), `<case> - <text>` (none does), `<case> 0` (no finding at all)."""
    core.SELFTEST = True
    base = HERE / "cases" / "boundaries"
    problems = patterns.run_case_spec(base, "boundaries", lambda repo: [
        str(f) for f in findings(repo, patterns.Architecture(repo))])
    # The Stop hook passes the changed lines: only imports on them (and cycles through them) count.
    repo = base / "layered"
    arch = patterns.Architecture(repo)
    for touched, want in (({"src/components/Card.tsx": {1}}, ["forbids"]),
                          ({"src/components/Card.tsx": {2}}, []),
                          ({"src/pages/Home.tsx": {1}}, ["module cycle"]),
                          ({"src/pages/Home.tsx": {2}}, [])):
        got = [f.problem for f in findings(repo, arch, touched)]
        if len(got) != len(want) or any(w not in g for w, g in zip(want, got)):
            problems.append(f"boundaries touched {touched}: expected {want}, got {got}")
    return problems


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*")
    parser.add_argument("--all", action="store_true", help="the whole repository")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        problems = selftest()
        for p in problems:
            print(f"FAIL    {p}")
        print("boundaries selftest: " + ("ok" if not problems else f"{len(problems)} failure(s)"))
        return 1 if problems else 0
    if not args.all and not args.files:
        parser.print_help()
        return 2
    repo = core.REPO
    arch = patterns.Architecture(repo)
    touched = None if args.all else patterns._resolve_touched(repo, arch, args.files)
    if not args.all and touched is None:
        return 2
    taken = {(p, r, s) for p, r, s, *_ in core.load_accepted(patterns.ACCEPTED_FILE)}
    found = [f for f in findings(repo, arch, touched) if (f.path, f.rule, f.subject) not in taken]
    for f in found:
        print(f)
    print(f"{len(found)} boundary finding(s).")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
