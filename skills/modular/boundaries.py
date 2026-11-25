"""Module boundaries: imports that erode a modular architecture.

    python boundaries.py --all              # the whole repository
    python boundaries.py src/features/x     # imports made by these files or folders
    python boundaries.py --suggest-map      # draft .claude/modular/architecture.md from the import graph
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
import hashlib
import json
import os
import re
import sys
import tempfile
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
WORKSPACE_ROOTS = {"packages", "apps", "services"}
SHARED_SEGS = {"lib", "libs", "utils", "util", "shared", "common", "core", "domain", "helpers"}
UPPER_SEGS = {"features", "feature", "modules", "pages", "screens", "views", "routes"}
JS_UPPER_SEGS = UPPER_SEGS | {"app"}
CACHE_VERSION = 1
MAX_CYCLE_FILES = int(os.environ.get("MODULAR_BOUNDARY_MAX_FILES", "4000") or "4000")

_JS_STRIP_COMMENTS = re.compile(
    r"(?P<bc>/\*[\s\S]*?\*/)"
    r"|(?P<hc><!--[\s\S]*?-->)"
    r"|(?P<lc>//[^\n]*)"
    r'|(?P<dq>"(?:[^"\\\n]|\\.)*")'
    r"|(?P<sq>(?<![A-Za-z0-9_])'(?:[^'\\\n]|\\.)*')"
    r"|(?P<tpl>`(?:[^`\\]|\\[\s\S])*`)"
)
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


def _read_ts_opts(cfg_path, seen=None):
    """Reads compilerOptions (baseUrl, paths) from a tsconfig/jsconfig, following local `extends`."""
    seen = set() if seen is None else seen
    key = cfg_path.resolve()
    if key in seen or not cfg_path.is_file():
        return None, {}
    seen.add(key)
    try:
        data = json.loads(_strip_jsonc(cfg_path.read_text(encoding="utf-8", errors="replace")))
    except (OSError, ValueError):
        return None, {}
    if not isinstance(data, dict):
        return None, {}
    base_url, paths = None, {}
    ext = data.get("extends")
    if isinstance(ext, str) and ext.startswith("."):
        parent = cfg_path.parent / ext
        if not parent.is_file() and parent.with_suffix(".json").is_file():
            parent = parent.with_suffix(".json")
        p_base, p_paths = _read_ts_opts(parent, seen)
        base_url = p_base
        paths.update(p_paths)
    opts = data.get("compilerOptions") or {}
    if isinstance(opts, dict):
        if opts.get("baseUrl") is not None:
            base_url = str(opts["baseUrl"])
        if isinstance(opts.get("paths"), dict):
            paths.update(opts["paths"])
    return base_url, paths


def _aliases_for_dir(repo, scope_dir):
    """Returns (aliases, base_url) for one config directory, with targets normalized relative to repo."""
    repo = Path(repo)
    rel_dir = "" if scope_dir == repo else scope_dir.relative_to(repo).as_posix()
    aliases, base_url = [], None
    for name in ("tsconfig.json", "tsconfig.app.json", "jsconfig.json"):
        cfg = scope_dir / name
        if not cfg.is_file():
            continue
        raw_base, raw_paths = _read_ts_opts(cfg)
        base = _norm("/".join(p for p in (rel_dir, str(raw_base)) if p)) if raw_base is not None else rel_dir
        if raw_base is not None:
            base_url = base or ""
        for key, targets in raw_paths.items():
            prefix = key[:-1] if key.endswith("*") else key
            dirs = []
            for t in targets if isinstance(targets, list) else []:
                t = t[:-1] if t.endswith("*") else t
                t = re.sub(r"^\./", "", t)
                joined = _norm("/".join(p for p in (base or "", t) if p))
                if joined is not None:
                    dirs.append((joined + "/") if joined and not joined.endswith("/") else joined)
            if prefix and dirs:
                aliases.append((prefix, dirs))
    if not aliases and (scope_dir / "src").is_dir():
        src_dir = f"{rel_dir}/src/" if rel_dir else "src/"
        aliases = [("@/", [src_dir]), ("~/", [src_dir])]
    aliases.sort(key=lambda a: -len(a[0]))
    return aliases, base_url


def js_alias_scopes(repo):
    """[(scope_prefix, aliases, base_url)] for the repo root and monorepo package roots, longest prefix first."""
    repo = Path(repo)
    dirs = [repo]
    try:
        for child in sorted(repo.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name in patterns.SKIP:
                continue
            if any((child / n).is_file() for n in ("tsconfig.json", "tsconfig.app.json", "jsconfig.json")):
                dirs.append(child)
            if child.name in WORKSPACE_ROOTS:
                for pkg in sorted(child.iterdir()):
                    if pkg.is_dir() and not pkg.name.startswith(".") and pkg.name not in patterns.SKIP:
                        if (pkg / "src").is_dir() or any(
                            (pkg / n).is_file() for n in ("tsconfig.json", "tsconfig.app.json", "jsconfig.json")
                        ):
                            dirs.append(pkg)
    except OSError:
        pass
    scopes = []
    seen = set()
    for d in dirs:
        scope = "" if d == repo else (d.relative_to(repo).as_posix() + "/")
        if scope in seen:
            continue
        seen.add(scope)
        aliases, base_url = _aliases_for_dir(repo, d)
        if aliases or base_url is not None or scope == "":
            scopes.append((scope, aliases, base_url))
    scopes.sort(key=lambda s: -len(s[0]))
    return scopes


def js_aliases(repo):
    """[(prefix, [target dirs relative to the repo])] from root tsconfig/jsconfig `paths`, plus baseUrl."""
    return _aliases_for_dir(repo, Path(repo))


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


def _resolve_js_scoped(spec, src, files, scopes):
    if spec.startswith("."):
        return resolve_js(spec, src, files, (), None)
    for scope, aliases, base_url in scopes:
        if not scope or src.startswith(scope):
            hit = resolve_js(spec, src, files, aliases, base_url)
            if hit:
                return hit
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


# ----------------------------------------------------------------------------- import graph & cache
def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def _strip_js_comments(text):
    """Strips JS/TS/HTML comments and multi-line template literals while preserving exact line numbers."""
    def repl(m):
        kind = m.lastgroup
        if kind in ("bc", "hc", "lc") or (kind == "tpl" and "\n" in m.group(0)):
            return "\n" * m.group(0).count("\n")
        return m.group(0)
    return _JS_STRIP_COMMENTS.sub(repl, text)


def _raw_js_imports(text):
    """Pure file-level JS/TS import extraction: [[line, spec, type_only, lazy], ...] (safe to cache by mtime)."""
    clean = _strip_js_comments(text)
    out = []
    for m in JS_STATIC.finditer(clean):
        clause = m.group(3) or ""
        names = re.findall(r"\{([^}]*)\}", clause)
        all_typed = bool(names) and not re.sub(r"\{[^}]*\}", "", clause).strip(" ,") and all(
            item.strip().startswith("type ") for item in names[0].split(",") if item.strip())
        out.append([_line_of(clean, m.start(1)), m.group(5), bool(m.group(2)) or all_typed, False])
    for m in JS_SIDE.finditer(clean):
        out.append([_line_of(clean, m.start()), m.group(2), False, False])
    for m in JS_REQUIRE.finditer(clean):
        prefix = clean[clean.rfind("\n", 0, m.start()) + 1:m.start()]
        out.append([_line_of(clean, m.start()), m.group(2), False, prefix[:1] in (" ", "\t")])
    for m in JS_DYNAMIC.finditer(clean):
        out.append([_line_of(clean, m.start()), m.group(2), False, True])
    return out


def _raw_py_imports(text):
    """Pure file-level Python import extraction: [[line, dots, module, names, type_only, lazy], ...]."""
    clean = patterns.blank_strings(text, ".py")
    out = []
    typed_blocks = []  # (start, end) offsets of `if TYPE_CHECKING:` bodies
    for m in PY_TYPE_CHECKING.finditer(clean):
        indent = len(m.group(1))
        end = m.end()
        for line in re.finditer(r"^([ \t]*)(\S.*)$", clean[m.end():], re.M):
            if len(line.group(1)) <= indent:
                end = m.end() + line.start()
                break
        else:
            end = len(clean)
        typed_blocks.append((m.end(), end))

    def flags(m):
        typed = any(a <= m.start() < b for a, b in typed_blocks)
        return typed, bool(m.group(1)) and not typed

    for m in PY_FROM.finditer(clean):
        names = [n.strip().split(" as ")[0].strip(" ()") for n in m.group(4).split(",")]
        typed, lazy = flags(m)
        out.append([_line_of(clean, m.start(2)), m.group(2), m.group(3), [n for n in names if n and n != "*"], typed, lazy])
    for m in PY_IMPORT.finditer(clean):
        typed, lazy = flags(m)
        line = _line_of(clean, m.start(2))
        for mod in m.group(2).split(","):
            mod = mod.strip()
            if mod:
                out.append([line, "", mod, [], typed, lazy])
    return out


def _edges_from_raw_js(rel, raw, files, scopes):
    out, seen = [], set()
    for line, spec, type_only, lazy in raw:
        dst = _resolve_js_scoped(spec, rel, files, scopes)
        if dst and dst != rel and (dst, line) not in seen:
            seen.add((dst, line))
            out.append(Edge(rel, dst, line, spec, type_only, lazy))
    return out


def _edges_from_raw_py(rel, raw, files):
    out = []
    for line, dots, module, names, typed, lazy in raw:
        for dst in resolve_py(dots, module, names, rel, files):
            if dst != rel:
                out.append(Edge(rel, dst, line, dots + module, typed, lazy))
    return out


def _cache_path(repo):
    repo_path = Path(repo).resolve()
    git = repo_path / ".git"
    if git.is_dir():
        base = git / "tautology"
    else:
        key = hashlib.sha1(str(repo_path).lower().encode()).hexdigest()[:16]
        base = Path(tempfile.gettempdir()) / "tautology-state" / key
    return base / "boundaries-cache.json"


def _load_cache(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("v") == CACHE_VERSION and isinstance(data.get("files"), dict):
            return data["files"]
    except (OSError, ValueError):
        pass
    return {}


def _save_cache(path, cached_files):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"v": CACHE_VERSION, "files": cached_files}, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _build_edges(repo, files, scan_files, use_cache=None):
    repo = Path(repo)
    use_cache = (not core.SELFTEST) if use_cache is None else use_cache
    cpath = _cache_path(repo) if use_cache else None
    cached = _load_cache(cpath) if cpath else {}
    dirty = False
    if cached:
        stale = [k for k in cached if k not in files]
        if stale:
            for k in stale:
                del cached[k]
            dirty = True
    scopes = js_alias_scopes(repo)
    edges = []
    for rel in sorted(scan_files):
        fpath = repo / rel
        suffix = Path(rel).suffix.lower()
        raw = None
        st = None
        if cpath:
            try:
                st = fpath.stat()
            except OSError:
                continue
            entry = cached.get(rel)
            if entry and len(entry) == 3 and entry[0] == st.st_mtime_ns and entry[1] == st.st_size:
                raw = entry[2]
        if raw is None:
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            raw = _raw_js_imports(text) if suffix in JS else _raw_py_imports(text)
            if cpath and st is not None:
                cached[rel] = [st.st_mtime_ns, st.st_size, raw]
                dirty = True
        if suffix in JS:
            edges += _edges_from_raw_js(rel, raw, files, scopes)
        else:
            edges += _edges_from_raw_py(rel, raw, files)
    if cpath and dirty:
        _save_cache(cpath, cached)
    return edges


def import_graph(repo, arch, target_files=None, use_cache=None):
    repo = Path(repo)
    files = {rel for rel in patterns.code_files(repo, arch, JS | PY)}
    scan = files if target_files is None else (set(target_files) & files)
    return files, _build_edges(repo, files, scan, use_cache=use_cache)


# ----------------------------------------------------------------------------- rules
def _workspace_prefix(rel):
    parts = rel.split("/")
    if len(parts) > 2 and parts[0] in WORKSPACE_ROOTS:
        return f"{parts[0]}/{parts[1]}/"
    return None


def _area(rel):
    parts = rel.split("/")
    suffix = Path(rel).suffix.lower()
    upper_segs = JS_UPPER_SEGS if suffix in JS else UPPER_SEGS
    start = 2 if len(parts) > 3 and parts[0] in WORKSPACE_ROOTS else 0
    if len(parts) - start > 2 and parts[start] in CONTAINERS:
        start += 1
    for i in range(start, len(parts) - 1):
        seg = parts[i]
        if seg == "components" and i + 1 < len(parts) - 1 and parts[i + 1] == "ui":
            return "shared"
        if seg in upper_segs:
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


def findings(repo, arch, touched=None, max_cycle_files=None, use_cache=None):
    """Boundary findings; touched {file: changed lines or None = all}: only imports made on those lines
    (and cycles with such an import on one of their hops). In targeted/hook mode, repos above
    max_cycle_files scope cycles to the touched workspace packages or skip cycles while keeping
    instant layer and deep-import checks on touched files."""
    repo = Path(repo)
    files = {rel for rel in patterns.code_files(repo, arch, JS | PY)}
    cap = MAX_CYCLE_FILES if max_cycle_files is None else max_cycle_files
    run_cycles = True
    scan = files
    if touched is not None and cap > 0 and len(files) > cap:
        touched_set = set(touched) & files
        prefixes = {_workspace_prefix(rel) for rel in touched_set}
        scoped = (
            {f for f in files if any(f.startswith(p) for p in prefixes)} | touched_set
            if prefixes and None not in prefixes else files
        )
        if len(scoped) <= cap:
            scan = scoped
        else:
            scan = touched_set
            run_cycles = False
    edges = _build_edges(repo, files, scan, use_cache=use_cache)
    mine = edges if touched is None else [
        e for e in edges if e.src in touched and (touched[e.src] is None or e.line in touched[e.src])]
    out = layer_findings(mine, arch) + deep_import_findings(mine, files, arch)
    if run_cycles:
        out += cycle_findings(edges, arch, touched)
    return out


# ----------------------------------------------------------------------------- map suggestion
_NON_FEATURE_PARENTS = CONTAINERS | WORKSPACE_ROOTS | SHARED_SEGS | UPPER_SEGS | {
    "components", "component", "ui", "widgets", "hooks", "composables", "styles", "assets", "types", "api", "services", "db", "app",
}
_DATA_LAYER_SEGS = {"api", "db", "database", "data", "store", "stores", "server", "services", "service", "repo", "repos", "infra", "clients", "client"}
_TOP_ONLY_SEGS = {"pages", "routes", "screens", "views", "app"}
_TARGET_PREF_SEGS = SHARED_SEGS | {"components", "ui", "widgets", "hooks", "composables", "services"}


def _discover_feature_roots(files):
    """Feature roots: folders named features/modules/domains with sub-folders, plus any other folder
    containing >=2 sub-folders that expose a public index.* file."""
    subfolders = defaultdict(set)
    indexed_children = defaultdict(set)
    for rel in files:
        parts = rel.split("/")
        for i in range(len(parts) - 2):
            parent = "/".join(parts[:i + 1]) + "/"
            child = "/".join(parts[:i + 2])
            subfolders[parent].add(child)
    for parent, children in subfolders.items():
        for child in children:
            if _public_index(child, files):
                indexed_children[parent].add(child)
    roots = []
    for parent in sorted(subfolders):
        leaf = parent.rstrip("/").rsplit("/", 1)[-1]
        if leaf in patterns.FEATURE_ROOT_SEGS and (len(subfolders[parent]) >= 2 or indexed_children[parent]):
            roots.append(parent)
        elif leaf not in _NON_FEATURE_PARENTS and len(indexed_children[parent]) >= 2:
            roots.append(parent)
    # Keep outermost feature roots only (avoid nesting a root inside another feature root).
    kept = []
    for r in roots:
        if not any(r.startswith(prev) for prev in kept):
            kept.append(r)
    return kept


def _bucket_of(rel, feature_roots):
    """Maps a file to its architectural layer folder prefix (ending with '/'), or None for loose entry files."""
    for root in feature_roots:
        if rel.startswith(root):
            return root
    parts = rel.split("/")
    suffix = Path(rel).suffix.lower()
    i = 0
    if len(parts) > 3 and parts[0] in WORKSPACE_ROOTS:
        i = 2
    if len(parts) - i > 2 and parts[i] in CONTAINERS:
        i += 1
    elif suffix in PY and len(parts) - i > 2 and parts[i] == "app":
        i += 1
    elif suffix in PY and len(parts) - i > 3 and parts[i + 1] == "app":
        i += 2  # backend/app/<layer>/...
    rem = len(parts) - i
    if rem < 2:
        # Loose file directly inside a workspace package's src/ (e.g. packages/shared/src/core.ts)
        if i >= 2:
            return "/".join(parts[:i]) + "/"
        return None
    if parts[i] == "components" and rem >= 3 and parts[i + 1] == "ui":
        return "/".join(parts[:i + 2]) + "/"
    return "/".join(parts[:i + 1]) + "/"


def _bucket_leaf(bucket):
    parts = bucket.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-2] == "components" and parts[-1] == "ui":
        return "ui"
    return parts[-1]


def _pick_target(buckets, fan_in):
    """Picks the best extraction destination folder in a layer, or None for page/route-only layers."""
    non_top = [b for b in buckets if _bucket_leaf(b) not in _TOP_ONLY_SEGS]
    if not non_top:
        return None
    return sorted(
        non_top,
        key=lambda b: (0 if _bucket_leaf(b) in _TARGET_PREF_SEGS else 1, -fan_in.get(b, 0), len(b), b),
    )[0]


def suggest_map(repo, arch=None, use_cache=None):
    """Drafts `.claude/modular/architecture.md` from the repository's current import graph:
    - layers from who imports whom (condensation DAG longest path to sinks);
    - features from feature roots and sibling folders with a public `index`;
    - forbid rules from non-redundant missing directions (same-layer isolation & layer-skip encapsulation)."""
    repo = Path(repo)
    arch = patterns.Architecture(repo) if arch is None else arch
    files, edges = import_graph(repo, arch, use_cache=use_cache)
    if not files:
        return "# No JS/TS/Vue/Svelte or Python files found to infer an architecture map.\n"

    feature_roots = _discover_feature_roots(files)
    bucket_files = defaultdict(int)
    file_bucket = {}
    for rel in sorted(files):
        b = _bucket_of(rel, feature_roots)
        if b:
            file_bucket[rel] = b
            bucket_files[b] += 1

    dep = defaultdict(set)
    rev = defaultdict(set)
    edge_counts = defaultdict(int)
    for e in edges:
        a, b = file_bucket.get(e.src), file_bucket.get(e.dst)
        if a and b and a != b:
            dep[a].add(b)
            rev[b].add(a)
            edge_counts[(a, b)] += 1

    connected = sorted({b for b in bucket_files if dep[b] or rev[b]})
    isolated = sorted(b for b in bucket_files if b not in connected)

    # Condense any pre-existing bucket cycles with Tarjan SCCs so layer numbering is a DAG.
    graph = {b: set(dep[b]) for b in connected}
    sccs = _sccs(graph)
    comp_of = {}
    for idx, comp in enumerate(sccs):
        for b in comp:
            comp_of[b] = idx
    next_id = len(sccs)
    for b in connected:
        if b not in comp_of:
            comp_of[b] = next_id
            next_id += 1

    dag = defaultdict(set)
    for a in connected:
        ca = comp_of[a]
        dag.setdefault(ca, set())
        for b in dep[a]:
            cb = comp_of[b]
            if ca != cb:
                dag[ca].add(cb)

    memo = {}

    def depth(cid):
        if cid not in memo:
            memo[cid] = 1 if not dag[cid] else 1 + max(depth(nxt) for nxt in dag[cid])
        return memo[cid]

    bucket_layer = {b: depth(comp_of[b]) for b in connected}
    max_layer = max(bucket_layer.values(), default=1)
    unlinked = []
    for b in isolated:
        sample = next((rel for rel, fb in file_bucket.items() if fb == b), b + "x.ts")
        area = _area(sample)
        if area == "shared":
            bucket_layer[b] = 1
        elif area == "upper":
            bucket_layer[b] = max(2, max_layer)
        else:
            unlinked.append(b)

    by_layer = defaultdict(list)
    for b, n in bucket_layer.items():
        by_layer[n].append(b)
    for n in by_layer:
        by_layer[n].sort()

    # Transitive reachability over bucket dependencies (for multi-hop layer-skip encapsulation).
    reach = {}
    for start in connected:
        seen_r, stack = set(), list(dep[start])
        while stack:
            cur = stack.pop()
            if cur not in seen_r:
                seen_r.add(cur)
                stack.extend(dep[cur] - seen_r)
        reach[start] = seen_r

    # Non-redundant forbid rules: directions that never occur and are not already forbidden by layer N.
    # Never forbid importing generic shared utility folders (lib/, utils/, shared/, core/, domain/, helpers/).
    forbids = []
    seen_forbid = set()
    # 1) Same-layer horizontal isolation (between active/connected buckets in the same layer)
    for n, members in sorted(by_layer.items()):
        active = [b for b in members if b in connected or bucket_files[b] >= 2]
        if len(active) < 2 or len(active) > 6:
            continue
        for a in active:
            for b in active:
                if a == b or b in dep[a] or _bucket_leaf(b) in SHARED_SEGS:
                    continue
                if comp_of.get(a) is not None and comp_of.get(a) == comp_of.get(b):
                    continue  # already in a cycle
                a_ui = bool(patterns.UI_DIR_HINT.search(a))
                b_data = _bucket_leaf(b) in _DATA_LAYER_SEGS
                prio = 0 if (a_ui and b_data) else 1
                seen_forbid.add((a, b))
                forbids.append((prio, a, b, "same-layer isolation"))
    # 2) Layer-skip encapsulation: A (layer >= L+2) reaches B (layer L) only through intermediate
    # layers and never imports B directly, so B is encapsulated behind the middle layer(s).
    for a in connected:
        la = bucket_layer[a]
        for b in connected:
            lb = bucket_layer[b]
            if la < lb + 2 or b in dep[a] or (a, b) in seen_forbid or _bucket_leaf(b) in SHARED_SEGS:
                continue
            intermediates = [m for m in dep[a] if lb < bucket_layer[m] < la]
            if intermediates and any(b in reach[m] for m in intermediates):
                seen_forbid.add((a, b))
                forbids.append((0, a, b, "layer-skip encapsulation"))
    forbids.sort(key=lambda item: (item[0], item[1], item[2]))
    forbids = forbids[:10]

    fan_in = {b: len(rev[b]) for b in bucket_files}
    comp_buckets = sorted(b for b in bucket_layer if patterns.UI_DIR_HINT.search(b))
    ui_layer = min((bucket_layer[b] for b in comp_buckets), default=None)
    docs = patterns.described_architecture(repo)

    lines = [
        f"# Draft architecture map inferred from {len(files)} file(s) and {sum(edge_counts.values())} cross-folder import(s).",
        f"# Review and save to {patterns.MAP_FILE.as_posix()} (or .agents/modular/architecture.md).",
    ]
    for comp in sccs:
        lines.append(f"# WARNING: existing cycle between {', '.join(comp)} (placed in the same layer).")
    if by_layer:
        lines.append("")
        for n in sorted(by_layer):
            lines.append(f"layer {n}: {' '.join(by_layer[n])}")
    targets = []
    for n in sorted(by_layer):
        t = _pick_target(by_layer[n], fan_in)
        if t:
            targets.append(f"target {n}: {t}")
    if targets:
        lines.append("")
        lines += targets
    if ui_layer is not None:
        lines.append(f"ui_layer: {ui_layer}")
    if comp_buckets:
        lines.append(f"components: {' '.join(comp_buckets)}")
    if feature_roots:
        lines.append(f"features: {' '.join(feature_roots)}")
    if forbids:
        lines.append("")
        for _, a, b, _ in forbids:
            lines.append(f"forbid: {a} -> {b}")
    if docs:
        lines.append("")
        lines.append(f"source: {docs[0]}")
    if unlinked:
        lines.append("")
        lines.append(f"# Unlinked folders (no cross-folder imports): {' '.join(unlinked)}")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- CLI & selftest
def _cache_and_cap_selftest():
    """Verifies mtime raw-import cache (hit, edit invalidation, new-file resolution without re-parsing,
    deleted-file pruning) and max_cycle_files package scoping / graceful skip in targeted mode."""
    import shutil

    problems = []
    tmp = Path(tempfile.mkdtemp(prefix="boundaries-cache-"))
    try:
        (tmp / ".git").mkdir()
        (tmp / "src" / "lib").mkdir(parents=True)
        (tmp / "src" / "features" / "billing" / "internal").mkdir(parents=True)
        (tmp / "src" / "pages").mkdir(parents=True)
        lib_f = tmp / "src" / "lib" / "fmt.ts"
        calc_f = tmp / "src" / "features" / "billing" / "internal" / "calc.ts"
        page_f = tmp / "src" / "pages" / "Home.tsx"
        lib_f.write_text("export const fmt = (v: number) => String(v);\n", encoding="utf-8")
        calc_f.write_text("export const calc = () => 1;\n", encoding="utf-8")
        page_f.write_text('import { calc } from "../features/billing/internal/calc";\nexport const Home = () => calc();\n', encoding="utf-8")

        arch = patterns.Architecture(tmp)
        cpath = _cache_path(tmp)

        # 1. Cold run populates cache; no deep-import yet (billing has no index.ts).
        r1 = findings(tmp, arch, use_cache=True)
        if r1 or not cpath.is_file():
            problems.append(f"cache cold run: findings={r1}, cache_exists={cpath.is_file()}")

        # 2. Warm run must hit cache without re-reading unchanged files (verified by replacing read_text).
        orig_read = Path.read_text
        reads = []

        def tracked_read(self, *a, **kw):
            if self.suffix in (JS | PY):
                reads.append(self.name)
            return orig_read(self, *a, **kw)

        Path.read_text = tracked_read
        try:
            r2 = findings(tmp, arch, use_cache=True)
        finally:
            Path.read_text = orig_read
        if r2 or reads:
            problems.append(f"cache warm hit: findings={r2}, unexpected reads={reads}")

        # 3. Adding billing/index.ts WITHOUT editing Home.tsx must immediately flag deep import on cache hit.
        (tmp / "src" / "features" / "billing" / "index.ts").write_text('export { calc } from "./internal/calc";\n', encoding="utf-8")
        r3 = findings(tmp, arch, use_cache=True)
        if not any("reaches into" in f.problem for f in r3):
            problems.append(f"cache new-file resolution: expected deep import, got {r3}")

        # 4. Editing lib/fmt.ts invalidates its entry and catches new layer violation.
        lib_f.write_text('import { Home } from "../pages/Home";\nexport const fmt = () => Home();\n', encoding="utf-8")
        r4 = findings(tmp, arch, use_cache=True)
        if not any("shared code imports" in f.problem for f in r4):
            problems.append(f"cache edit invalidation: expected layer finding, got {r4}")

        # 5. Deleting a file prunes it from cache.
        (tmp / "src" / "features" / "billing" / "index.ts").unlink()
        findings(tmp, arch, use_cache=True)
        if "src/features/billing/index.ts" in _load_cache(cpath):
            problems.append("cache deletion pruning: deleted file still in cache")

        # 6. max_cycle_files: flat repo over cap still catches layer findings on touched files, skips cycles.
        r6 = findings(tmp, arch, touched={"src/lib/fmt.ts": {1}}, max_cycle_files=1, use_cache=True)
        if not any("shared code imports" in f.problem for f in r6):
            problems.append(f"cap flat skip-cycles: expected layer finding on touched, got {r6}")

        # 7. max_cycle_files: monorepo over cap scopes cycles to touched workspace package when it fits.
        mono = tmp / "mono"
        for d in ("packages/web/src/features/a", "packages/web/src/features/b", "packages/huge/src/m"):
            (mono / d).mkdir(parents=True)
        (mono / "packages/web/src/features/a/x.ts").write_text('import { y } from "../b/y";\nexport const x = () => y();\n', encoding="utf-8")
        (mono / "packages/web/src/features/b/y.ts").write_text('import { x } from "../a/x";\nexport const y = () => x();\n', encoding="utf-8")
        for i in range(6):
            (mono / f"packages/huge/src/m/f{i}.ts").write_text(f"export const v{i} = {i};\n", encoding="utf-8")
        mono_arch = patterns.Architecture(mono)
        r7 = findings(mono, mono_arch, touched={"packages/web/src/features/a/x.ts": {1}}, max_cycle_files=4, use_cache=False)
        if not any("module cycle" in f.problem for f in r7):
            problems.append(f"cap monorepo package-scoped cycle: expected cycle in packages/web, got {r7}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return problems


def _suggest_map_selftest():
    """Verifies --suggest-map end-to-end: layers from DAG depth, non-default feature roots from
    sibling index.* files, non-redundant forbid rules (horizontal + layer-skip), round-trip into
    Architecture with 0 false findings, immediate enforcement on new violations, and cycle safety."""
    import shutil

    problems = []
    tmp = Path(tempfile.mkdtemp(prefix="boundaries-suggest-"))
    try:
        for d in (
            "src/lib",
            "src/db",
            "src/services",
            "src/components/ui",
            "src/slices/billing/internal",
            "src/slices/orders",
            "src/pages",
        ):
            (tmp / d).mkdir(parents=True)
        (tmp / "src/lib/fmt.ts").write_text("export const fmt = (n: number) => String(n);\n", encoding="utf-8")
        (tmp / "src/db/client.ts").write_text("export const query = () => [];\n", encoding="utf-8")
        (tmp / "src/services/order.ts").write_text(
            'import { query } from "../db/client";\nimport { fmt } from "../lib/fmt";\nexport const load = () => fmt(query().length);\n',
            encoding="utf-8",
        )
        (tmp / "src/components/ui/Button.tsx").write_text(
            'import { fmt } from "../../lib/fmt";\nexport const Button = () => fmt(1);\n',
            encoding="utf-8",
        )
        (tmp / "src/slices/billing/internal/calc.ts").write_text("export const calc = () => 1;\n", encoding="utf-8")
        (tmp / "src/slices/billing/index.ts").write_text(
            'import { load } from "../../services/order";\nimport { Button } from "../../components/ui/Button";\n'
            'export { calc } from "./internal/calc";\nexport const bill = () => load() + Button();\n',
            encoding="utf-8",
        )
        (tmp / "src/slices/orders/index.ts").write_text(
            'import { load } from "../../services/order";\nexport const orders = () => load();\n',
            encoding="utf-8",
        )
        (tmp / "src/pages/Home.tsx").write_text(
            'import { bill } from "../slices/billing";\nimport { Button } from "../components/ui/Button";\n'
            'import { fmt } from "../lib/fmt";\nexport const Home = () => bill() + Button() + fmt(2);\n',
            encoding="utf-8",
        )

        draft = suggest_map(tmp, use_cache=False)
        map_path = tmp / patterns.MAP_FILE
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(draft, encoding="utf-8")
        arch = patterns.Architecture(tmp)

        if not arch.defined:
            problems.append(f"suggest_map: parsed Architecture not defined:\n{draft}")
        l_lib = arch.layer("src/lib/fmt.ts")
        l_db = arch.layer("src/db/client.ts")
        l_svc = arch.layer("src/services/order.ts")
        l_ui = arch.layer("src/components/ui/Button.tsx")
        l_slice = arch.layer("src/slices/billing/index.ts")
        l_page = arch.layer("src/pages/Home.tsx")
        if not (l_lib == 1 and l_db == 1 and l_svc == 2 and l_ui == 2 and l_slice == 3 and l_page == 4):
            problems.append(f"suggest_map layers: got lib={l_lib}, db={l_db}, svc={l_svc}, ui={l_ui}, slice={l_slice}, page={l_page}\n{draft}")
        if "src/slices/" not in arch.features:
            problems.append(f"suggest_map features: expected src/slices/ in {arch.features}\n{draft}")
        if arch.visual_layer() != 2:
            problems.append(f"suggest_map ui_layer: expected 2, got {arch.visual_layer()}\n{draft}")
        if ("src/components/ui/", "src/services/") not in arch.forbid:
            problems.append(f"suggest_map horizontal forbid missing: {arch.forbid}\n{draft}")
        if ("src/pages/", "src/db/") not in arch.forbid:
            problems.append(f"suggest_map layer-skip forbid missing: {arch.forbid}\n{draft}")
        if ("src/pages/", "src/lib/") in arch.forbid or ("src/slices/", "src/lib/") in arch.forbid or ("src/db/", "src/lib/") in arch.forbid:
            problems.append(f"suggest_map over-forbid: must not forbid importing shared util folder lib/:\n{draft}")

        # Zero-violation invariant on the clean codebase the map was inferred from.
        clean_found = findings(tmp, arch, use_cache=False)
        if clean_found:
            problems.append(f"suggest_map clean repo had findings: {clean_found}\n{draft}")

        # Enforcement: introducing a forbid bypass (pages -> db) and deep import (pages -> slices/billing/internal/calc) must be caught.
        (tmp / "src/pages/Home.tsx").write_text(
            'import { query } from "../db/client";\nimport { calc } from "../slices/billing/internal/calc";\n'
            'export const Home = () => query().length + calc();\n',
            encoding="utf-8",
        )
        after = findings(tmp, arch, use_cache=False)
        if not any("forbids" in f.problem for f in after) or not any("reaches into" in f.problem for f in after):
            problems.append(f"suggest_map enforcement: expected forbid + deep import, got {after}")

        # Cycle safety: repo with existing cycle still produces a valid draft with a WARNING comment.
        cyc = tmp / "cyc"
        (cyc / "src/a").mkdir(parents=True)
        (cyc / "src/b").mkdir(parents=True)
        (cyc / "src/a/x.ts").write_text('import { y } from "../b/y";\nexport const x = () => y();\n', encoding="utf-8")
        (cyc / "src/b/y.ts").write_text('import { x } from "../a/x";\nexport const y = () => x();\n', encoding="utf-8")
        cyc_draft = suggest_map(cyc, use_cache=False)
        if "# WARNING: existing cycle" not in cyc_draft or "layer 1: src/a/ src/b/" not in cyc_draft:
            problems.append(f"suggest_map cycle draft unexpected:\n{cyc_draft}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return problems


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
    problems += _cache_and_cap_selftest()
    problems += _suggest_map_selftest()
    return problems


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*")
    parser.add_argument("--all", action="store_true", help="the whole repository")
    parser.add_argument("--suggest-map", action="store_true", help="draft .claude/modular/architecture.md from the import graph")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        problems = selftest()
        for p in problems:
            print(f"FAIL    {p}")
        print("boundaries selftest: " + ("ok" if not problems else f"{len(problems)} failure(s)"))
        return 1 if problems else 0
    if args.suggest_map:
        print(suggest_map(core.REPO), end="")
        return 0
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
