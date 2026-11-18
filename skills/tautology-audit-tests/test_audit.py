"""Deep audit of unit and integration tests for self-confirming, undesirable, and repetitive patterns.

    python test_audit.py --find parser                 # locate test files or #[cfg(test)] modules
    python test_audit.py --all tests/                  # full audit across test files/folders
    python test_audit.py --diff                        # changed test lines against HEAD
    python test_audit.py --only self-confirming,repetitive ...
    python test_audit.py --accept "<finding>" --reason "<why>"
    python test_audit.py --selftest
"""
import argparse
import ast
import difflib
import hashlib
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAUTOLOGY_DIR = HERE.parent / "tautology"
MODULAR_DIR = HERE.parent / "modular"
CASES = HERE / "cases"
for extra in (TAUTOLOGY_DIR, MODULAR_DIR):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import core  # noqa: E402
import copy_rules  # noqa: E402
import patterns as mod_patterns  # noqa: E402
import test_rules  # noqa: E402

CHECKS = {"self-confirming", "undesirable", "repetitive"}
TIME_TEST_RE = re.compile(r"(?i)timeout|backoff|retry|debounce|throttle|ttl|expir|interval|rate_?limit")
NET_MOCK_RE = re.compile(r"(?i)\b(?:wiremock|httpmock|mockito|responses|nock|respx|msw|TestServer|TcpListener|MockTransport)\b")
LIVE_URL_RE = re.compile(
    r"""(?:requests\.(?:get|post|put|delete)|httpx\.(?:get|post)|reqwest::(?:get|Client::new)|fetch)\(\s*["'](https?://[^"'\n]+)["']"""
)
LOCAL_HOST_RE = re.compile(r"^https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|example\.(?:com|org|net))(?::\d+)?(?:/|$)", re.I)
SKIP_RS_ATTR_RE = re.compile(r"^\s*#\[ignore(?:\s*=\s*\"[^\"]+\")?\s*\]")
SKIP_PY_RE = re.compile(r"@(?:pytest\.mark|unittest)\.skip(?:\(([^)]*)\))?")
ONLY_JS_RE = re.compile(r"\b(?:it|test|describe)\.only\s*\(|\bf(?:it|describe)\s*\(")
SKIP_JS_RE = re.compile(r"\b(?:it|test|describe)\.skip\s*\(")
BUILTIN_IGNORE = {"open", "sleep", "time", "now", "today", "stdout", "stderr", "getenv", "print", "input", "random"}
EMPTY_GUARD_TPL = r"(?:\b{col}\b\s*(?:\.\s*(?:is_empty|len|length)\b|==|!=)|\b(?:assert|expect)\w*!?\s*\([^;\n]*\b{col}\b)"


@dataclass
class TestBlock:
    name: str
    start: int
    end: int
    body_start: int
    ignored: bool
    has_skip_reason: bool


def _own_test_case(path, rel):
    return not core.SELFTEST and (path.resolve().is_relative_to(CASES) or core.own_cases(path, rel))


def _norm_expr(text, suffix):
    lines = []
    for raw in text.splitlines():
        if core.comment_of(raw, suffix) is not None:
            continue
        cleaned = re.sub(r"//.*$" if suffix != ".py" else r"#.*$", "", raw)
        lines.append(cleaned.strip())
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _norm_clone_line(raw, suffix):
    if core.comment_of(raw, suffix) is not None:
        return ""
    s = re.sub(r"\s+", " ", raw.strip())
    if not s or s in ("{", "}", ");", ")"):
        return ""
    s = re.sub(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`[^`\n]*`', '"S"', s)
    s = re.sub(r"\b\d+(?:\.\d+)?\b", "N", s)
    return s


def _block_end_brace(lines, open_line, suffix):
    """Finds the closing line of a braced Rust/JS test block using string-stripped code."""
    depth = 0
    started = False
    for idx in range(open_line - 1, len(lines)):
        raw = lines[idx]
        if core.comment_of(raw, suffix) is not None:
            continue
        code = core.strip_literals(raw)
        code = re.sub(r"//.*$", "", code)
        for ch in code:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth <= 0:
                    return idx + 1
    return len(lines)


def _has_comment_above(lines, number, lookback=4):
    for ln in range(max(1, number - lookback), number):
        s = lines[ln - 1].strip()
        if s.startswith(("//", "#", "/*", "*")):
            return True
    return False


def _extract_test_blocks(lines, suffix, is_test_file, test_from):
    """Returns TestBlock spans for Rust #[test], Python test_*, and JS/TS it/test blocks."""
    blocks = []
    if suffix == ".py" and is_test_file:
        try:
            tree = ast.parse("\n".join(lines))
        except SyntaxError:
            return []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test"):
                continue
            decs = [lines[d.lineno - 1].strip() for d in node.decorator_list]
            skip_dec = any("skip" in d and "skipif" not in d for d in decs)
            has_reason = any("reason=" in d for d in decs) or _has_comment_above(lines, node.lineno, 2)
            body_start = node.body[0].lineno if node.body else node.lineno + 1
            blocks.append(TestBlock(node.name, node.lineno, node.end_lineno or node.lineno, body_start, skip_dec, has_reason))
        blocks.sort(key=lambda b: b.start)
        return blocks

    if suffix == ".rs" and (is_test_file or test_from):
        start_at = test_from or 1
        pending = False
        ignored = False
        has_reason = False
        first_attr = None
        for number in range(start_at, len(lines) + 1):
            line = lines[number - 1]
            if test_rules.RUST_TEST_ATTR.match(line):
                pending = True
                first_attr = first_attr or number
                continue
            ign_m = SKIP_RS_ATTR_RE.match(line)
            if ign_m:
                ignored = True
                has_reason = "=" in ign_m.group(0) or _has_comment_above(lines, first_attr or number, 3)
                continue
            if line.strip().startswith("#["):
                continue
            fn_m = test_rules.RUST_FN.match(line)
            if fn_m:
                if pending:
                    end = _block_end_brace(lines, number, suffix)
                    blocks.append(TestBlock(fn_m.group(1), number, end, number + 1, ignored, has_reason))
            if fn_m or line.strip():
                pending, ignored, has_reason, first_attr = False, False, False, None
        return blocks

    if suffix in core.JS and is_test_file:
        for number, line in enumerate(lines, 1):
            m = re.match(r"""^\s*(?:it|test)(?:\.(only|skip))?\(\s*["'`]([^"'`]+)["'`]""", line)
            if m:
                end = _block_end_brace(lines, number, suffix)
                skipped = m.group(1) == "skip"
                has_reason = _has_comment_above(lines, number, 2)
                blocks.append(TestBlock(m.group(2), number, end, number + 1, skipped, has_reason))
    return blocks


def repo_test_files(root=None):
    """All tracked test files and Rust files containing inline #[cfg(test)] modules."""
    base = Path(root or core.REPO).resolve()
    out = []
    for rel in mod_patterns.tracked(base):
        path = (base / rel).resolve()
        suffix = path.suffix.lower()
        if suffix not in core.CODE or _own_test_case(path, rel):
            continue
        if core.C.skip_dirs & set(Path(rel).parts) or not path.is_file() or path.stat().st_size > core.MAX_BYTES:
            continue
        is_t = core.is_test(rel)
        if not is_t and suffix != ".rs":
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if is_t or "#[cfg(test)]" in source:
            out.append((rel, path, source))
    return out


def find_tests(query, root=None):
    """Locates test files matching module name, test function name, or tested symbol."""
    terms = [t for t in core.plain(query).split() if len(t) >= 2]
    if not terms:
        return []
    scored = []
    for rel, _, source in repo_test_files(root):
        norm_rel = core.plain(rel)
        score = sum(8 for t in terms if f" {t} " in f" {norm_rel} ") + sum(4 for t in terms if t in norm_rel)
        test_syms = " ".join(
            a or b for a, b in re.findall(r"""\b(?:fn|def)\s+([A-Za-z_]\w*)|(?:it|test)\(\s*["'`]([^"'`]+)["'`]""", source)
        )
        norm_syms = core.plain(test_syms)
        score += sum(3 for t in terms if t in norm_syms)
        if score == 0 and any(t in core.plain(source[:12000]) for t in terms):
            score = 2
        if score > 0:
            scored.append((score, rel))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[:15]


def _check_multiline_and_bounds(rel, lines, suffix, block, look, out):
    """Multi-line identical assertions, len >= 0 checks, silent conditionals, and vacuous loops."""
    bindings = {}  # var -> (rhs_expr, intact_since)
    for number in range(block.start, block.end + 1):
        raw = lines[number - 1]
        if core.comment_of(raw, suffix) is not None or not raw.strip():
            continue
        code = core.strip_literals(raw)
        if suffix == ".rs":
            lm = test_rules.RUST_LET.match(raw)
            if lm and lm.group(1) not in bindings:
                bindings[lm.group(1)] = [lm.group(2).strip(), True]
                continue
        elif suffix in core.JS:
            jm = test_rules.JS_LET.match(raw)
            if jm and jm.group(1) not in bindings:
                bindings[jm.group(1)] = [jm.group(2).strip(), True]
                continue

        if look(number):
            if re.search(r"\bassert!\([^;\n]*\.len\(\)\s*>=\s*0\b|\bassert\s+len\([^)]+\)\s*>=\s*0\b|\bexpect\([^)]*\.length\)\.toBeGreaterThanOrEqual\(\s*0\s*\)", raw):
                out.append(core.Finding(rel, number, "self-confirming", "length >= 0 is always true by type", "check exact length or > 0"))

            # Multi-line Rust assert_eq!/assert_ne!
            if suffix == ".rs":
                for m in re.finditer(r"\b(assert_eq|assert_ne)!\(", raw):
                    span_text, last = copy_rules.call_span(lines, number, m.end(), limit=25)
                    if last > number:
                        args = core.split_top(span_text)
                        if len(args) >= 2:
                            a, b = _norm_expr(args[0], suffix), _norm_expr(args[1], suffix)
                            ra = bindings[a][0] if a in bindings and bindings[a][1] else a
                            rb = bindings[b][0] if b in bindings and bindings[b][1] else b
                            if a == b or (ra == rb and not test_rules.RUST_LITERAL.fullmatch(ra)):
                                msg = core.SAME_CALL if a != b else f"{m.group(1)}! compares identical multi-line expressions"
                                out.append(core.Finding(rel, number, "self-confirming", msg, core.FIX_EXPECTED))

            # Multi-line JS expect(...).toEqual(...)
            if suffix in core.JS:
                for m in test_rules.JS_EXPECT.finditer(raw):
                    span_text, last = copy_rules.call_span(lines, number, m.end(), limit=20)
                    tail_idx = min(len(lines), last + 5)
                    after = "\n".join(lines[last - 1:tail_idx])
                    mm = re.search(r"\)\s*(?:\.not)?\.(toBe|toEqual|toStrictEqual|toMatchObject)\(", after)
                    if mm and (last > number or "\n" in after[:mm.end()]):
                        open_line = last - 1 + after[:mm.end()].count("\n") + 1
                        col = mm.end() - after[:mm.end()].rfind("\n") - 1
                        exp_text, _ = copy_rules.call_span(lines, open_line, col, limit=20)
                        a, b = _norm_expr(span_text, suffix), _norm_expr(exp_text, suffix)
                        if a and a == b:
                            out.append(core.Finding(rel, number, "self-confirming", "expect compares identical multi-line expressions", core.FIX_EXPECTED))

        if raw.strip() and not re.match(r"\s*(?:assert|expect)", raw):
            for v in bindings:
                bindings[v][1] = False

    # Conditional silent pass: if let Some/Ok/Err(...) without else, match with empty Err/_ => {}, or vacuous for loop
    body_lines = lines[block.start - 1:block.end]
    for offset, raw in enumerate(body_lines):
        number = block.start + offset
        if not look(number) or core.comment_of(raw, suffix) is not None:
            continue
        if suffix == ".rs":
            m_if = re.match(r"^\s*if\s+let\s+(?:Some|Ok|Err)\([^)]+\)\s*=\s*([A-Za-z_]\w*)", raw)
            if m_if:
                expr = m_if.group(1)
                prior_ctx = "\n".join(lines[block.start - 1:number - 1])
                pre_checked = bool(re.search(rf"\b{re.escape(expr)}\.(?:is_ok|is_err|is_some|is_none)\(", prior_ctx))
                end_if = _block_end_brace(lines, number, suffix)
                inner = "\n".join(lines[number - 1:end_if])
                after_close = lines[end_if - 1] if end_if <= len(lines) else ""
                has_else = bool(re.search(r"\}\s*else\b", after_close)) or (
                    end_if < len(lines) and re.match(r"^\s*else\b", lines[end_if])
                )
                if not pre_checked and not has_else and re.search(r"\bassert\w*!", inner):
                    out.append(core.Finding(
                        rel, number, "self-confirming",
                        "conditional `if let` wraps assertion without `else`: wrong variant passes silently",
                        "use `let ... else { panic!(...) }` or assert the expected variant directly",
                    ))
        if suffix == ".rs" and re.search(r"\b(?:Err|None)\([^)]*\)\s*=>\s*\{\s*\}|_\s*=>\s*(?:\{\s*\}|\(\))\s*,?$", raw):
            whole = "\n".join(body_lines)
            if re.search(r"\bassert\w*!", whole):
                out.append(core.Finding(
                    rel, number, "self-confirming",
                    "match arm silently ignores unexpected variant in test",
                    "panic or assert expected failure instead of empty `{}` / `()` arm",
                ))
        # Vacuous for loop over collection in Rust/JS (Python handled in AST check)
        col = None
        if suffix == ".rs":
            fm = re.match(r"^\s*for\s+\S+\s+in\s+(?:&(?:mut\s+)?)?([A-Za-z_]\w*)\b(?!\s*\.\.(?!\S*\.\.))", raw)
            if fm and not re.search(r"\b(?:vec!|\[|0\.\.)", raw):
                col = fm.group(1)
        elif suffix in core.JS:
            jm = re.search(r"\b([A-Za-z_]\w*)\.forEach\s*\(|\bfor\s*\([^)]+\bof\s+([A-Za-z_]\w*)\)", raw)
            if jm:
                col = jm.group(1) or jm.group(2)
        if col:
            end_loop = _block_end_brace(lines, number, suffix)
            loop_body = "\n".join(lines[number - 1:end_loop])
            prior = "\n".join(lines[block.start - 1:number - 1])
            is_local_lit = bool(re.search(rf"\b(?:let|const)\s+(?:mut\s+)?{re.escape(col)}\b[^=]*=\s*(?:vec!)?\[", prior))
            checked_before = bool(re.search(EMPTY_GUARD_TPL.format(col=re.escape(col)), prior))
            if not is_local_lit and not checked_before and re.search(r"\b(?:assert\w*!|expect\()", loop_body):
                out.append(core.Finding(
                    rel, number, "self-confirming",
                    f"vacuous loop over `{col}`: assertion runs 0 times if `{col}` is empty",
                    f"assert `{col}` is non-empty or exact length before the loop",
                ))


def _check_rust_js_echo(rel, lines, suffix, block, look, out):
    """Detects local struct/container construction asserted immediately with zero SUT calls in between."""
    locals_written = {}  # var -> (kind, line_set)
    for number in range(block.body_start, block.end + 1):
        raw = lines[number - 1]
        if core.comment_of(raw, suffix) is not None or not raw.strip():
            continue
        code = core.strip_literals(raw)
        if suffix == ".rs":
            lm = re.match(r"^\s*let\s+(?:mut\s+)?(\w+)\s*(?::[^=]+)?=\s*(?:vec!\[|Vec::new\(|(?:HashMap|BTreeMap)::new\(|([A-Z]\w*)\s*\{([^;]*)\});\s*$", code)
            if lm and lm.group(2) not in (None, "Ok", "Some") or (lm and not lm.group(2)):
                locals_written[lm.group(1)] = number
                continue
            pm = re.match(r"^\s*(\w+)\.(?:push|insert)\(", code)
            if pm and pm.group(1) in locals_written:
                continue
        else:
            jm = re.match(r"^\s*(?:const|let)\s+(\w+)\s*=\s*(?:\{|\[)", code)
            if jm:
                locals_written[jm.group(1)] = number
                continue
        if look(number) and re.search(r"\b(?:assert\w*!|expect\()", raw):
            for var in list(locals_written):
                if re.search(rf"\b{re.escape(var)}\b(?:\.\w+|\.get\(|\.contains\(|\[\S+\])", raw):
                    between = "\n".join(lines[locals_written[var]:number - 1])
                    calls = [c for c in re.findall(r"\b([A-Za-z_]\w*)\s*\(", between) if c not in ("push", "insert", "new", "from", "to_string", "to_owned", "clone")]
                    if not calls and not re.search(rf"\b[A-Za-z_]\w*\(\s*&?(?:mut\s+)?{re.escape(var)}\b", between):
                        out.append(core.Finding(
                            rel, number, "self-confirming",
                            f"checks local literal/container `{var}` built inside the test without calling code under test",
                            "pass through or test real production behavior instead of reading back setup fields",
                        ))
                        del locals_written[var]
                        break


def _check_python_ast(rel, source, lines, look, out):
    """Deep AST audit for Python tests: try/except without fail, vacuous loop, local echo, mock self-patch, skip/sleep."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test"):
            continue
        subj = re.sub(r"^test_+", "", node.name)
        decs = [lines[d.lineno - 1].strip() for d in node.decorator_list]
        for dec in node.decorator_list:
            dec_src = ast.get_source_segment(source, dec) or ""
            pm = re.search(r"""\bpatch(?:\.object)?\(\s*(?:[^,]+,\s*)?["']([^"']+)["']""", dec_src)
            if pm:
                target = pm.group(1).split(".")[-1]
                if target not in BUILTIN_IGNORE and (subj == target or subj.startswith(target + "_")) and look(dec.lineno):
                    out.append(core.Finding(
                        rel, dec.lineno, "self-confirming",
                        f"`{node.name}` patches target `{target}` under test",
                        "test real logic and mock only external I/O boundaries",
                    ))
        skip_m = next((SKIP_PY_RE.search(d) for d in decs if "skip" in d and "skipif" not in d), None)
        if skip_m and "reason=" not in (skip_m.group(1) or "") and not _has_comment_above(lines, node.lineno, 2) and look(node.lineno):
            out.append(core.Finding(rel, node.lineno, "undesirable", f"`{node.name}` skipped without reason", "fix or add explicit `reason=...`"))

        # Track non-empty guarded names and local literals
        guarded = set()
        local_containers = {}  # name -> lineno
        passed_to_call = set()
        mock_returns = {}  # mock_name -> lineno
        for sub in ast.walk(node):
            ln = getattr(sub, "lineno", 0)
            if isinstance(sub, ast.Assert):
                txt = ast.get_source_segment(source, sub.test) or ""
                if re.search(r"\blen\([^)]+\)\s*>=\s*0\b", txt) and look(ln):
                    out.append(core.Finding(rel, ln, "self-confirming", "len() >= 0 is always true", "assert > 0 or exact count"))
                for name_node in ast.walk(sub.test):
                    if isinstance(name_node, ast.Name):
                        guarded.add(name_node.id)
                if isinstance(sub.test, ast.Compare) and len(sub.test.comparators) == 1:
                    left_s = ast.get_source_segment(source, sub.test.left) or ""
                    for container, c_ln in list(local_containers.items()):
                        if re.match(rf"^{re.escape(container)}\[", left_s) and container not in passed_to_call and look(ln):
                            out.append(core.Finding(rel, ln, "self-confirming", f"checks key/element written locally in `{container}` without calling code under test", "test real function output"))
                            del local_containers[container]
            elif isinstance(sub, ast.Try):
                has_fail = any(
                    re.search(r"\b(?:fail\(|pytest\.fail|assert\s+False|raise\b)", lines[s.lineno - 1])
                    for s in sub.body
                )
                handler_asserts = any(
                    isinstance(item, ast.Assert)
                    for h in sub.handlers for item in ast.walk(h)
                )
                if handler_asserts and not has_fail and look(sub.lineno):
                    out.append(core.Finding(
                        rel, sub.lineno, "self-confirming",
                        "`try/except` asserts inside `except` without failing if no exception is raised",
                        "use `with pytest.raises(...)` or add unconditional failure at end of `try`",
                    ))
                for h in sub.handlers:
                    if len(h.body) == 1 and isinstance(h.body[0], ast.Pass) and look(h.lineno):
                        out.append(core.Finding(rel, h.lineno, "undesirable", "`except: pass` swallows failure inside test", "assert or handle explicit condition cleanly"))
            elif isinstance(sub, ast.For):
                has_assert = any(isinstance(x, ast.Assert) for x in ast.walk(sub))
                if has_assert and isinstance(sub.iter, ast.Name) and sub.iter.id not in guarded and sub.iter.id not in local_containers and look(sub.lineno):
                    out.append(core.Finding(
                        rel, sub.lineno, "self-confirming",
                        f"vacuous loop over `{sub.iter.id}`: if empty, loop asserts pass silently",
                        f"assert `len({sub.iter.id}) > 0` before the loop",
                    ))
            elif isinstance(sub, ast.Assign) and len(sub.targets) == 1:
                tgt = sub.targets[0]
                if isinstance(tgt, ast.Name) and isinstance(sub.value, (ast.Dict, ast.List, ast.Set)):
                    local_containers[tgt.id] = sub.lineno
                elif isinstance(tgt, ast.Attribute) and tgt.attr == "return_value" and isinstance(tgt.value, ast.Name):
                    mock_returns[tgt.value.id] = sub.lineno
            elif isinstance(sub, ast.Call):
                for arg in sub.args:
                    if isinstance(arg, ast.Name):
                        passed_to_call.add(arg.id)
                call_src = ast.get_source_segment(source, sub) or ""
                pm2 = re.search(r"""monkeypatch\.setattr\(\s*[^,]+,\s*["']([^"']+)["']""", call_src)
                if pm2:
                    t2 = pm2.group(1)
                    if t2 not in BUILTIN_IGNORE and (subj == t2 or subj.startswith(t2 + "_")) and look(ln):
                        out.append(core.Finding(rel, ln, "self-confirming", f"`{node.name}` monkeypatches target `{t2}` under test", "mock I/O dependency, not the function under test"))
                if isinstance(sub.func, ast.Name) and sub.func.id in mock_returns and look(ln):
                    # Direct call to mocked object itself inside assertion check
                    for p in ast.walk(node):
                        if isinstance(p, ast.Assert) and p.lineno == ln:
                            out.append(core.Finding(rel, ln, "self-confirming", f"asserts directly on mock `{sub.func.id}` return_value", "call the real unit that uses the mock instead"))
                            break


def _check_undesirable_lines(rel, source, lines, suffix, block, look, out):
    """Detects flaky sleeps, skipped/only focus markers, empty JS catch, and live HTTP requests."""
    has_net_mock = NET_MOCK_RE.search(source) is not None
    skip_time = TIME_TEST_RE.search(block.name) is not None

    if block.ignored and not block.has_skip_reason and look(block.start):
        out.append(core.Finding(rel, block.start, "undesirable", f"`{block.name}` skipped/ignored with no reason", "enable the test or document why it is ignored"))

    for number in range(block.start, block.end + 1):
        raw = lines[number - 1]
        if not look(number) or core.comment_of(raw, suffix) is not None:
            continue
        if suffix in core.JS and ONLY_JS_RE.search(raw):
            out.append(core.Finding(rel, number, "undesirable", "focused `.only` / `fit` disables the rest of the test suite", "remove `.only` before committing"))
        if suffix in core.JS and re.search(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}", raw):
            out.append(core.Finding(rel, number, "undesirable", "empty `catch {}` swallows failure in test", "assert error details or rethrow"))
        if not has_net_mock:
            um = LIVE_URL_RE.search(raw)
            if um and not LOCAL_HOST_RE.match(um.group(1)):
                out.append(core.Finding(rel, number, "undesirable", f"live network URL in unit test ({um.group(1)[:36]})", "mock transport or use recorded fixture"))
        if not skip_time:
            sm = re.search(r"\b(?:time\.sleep|std::thread::sleep|tokio::time::sleep)\(\s*(?:Duration::from_(?P<unit>millis|secs)\(\s*(?P<d>\d+)|(?P<s>\d+(?:\.\d+)?))", raw)
            ms = 0.0
            if sm:
                if sm.group("unit") == "millis":
                    ms = float(sm.group("d"))
                elif sm.group("unit") == "secs":
                    ms = float(sm.group("d")) * 1000.0
                elif sm.group("s"):
                    ms = float(sm.group("s")) * 1000.0
            jm = re.search(r"\bsetTimeout\([^,]+,\s*(\d+)", raw)
            if jm:
                ms = float(jm.group(1))
            if ms >= 100.0:
                out.append(core.Finding(rel, number, "undesirable", f"arbitrary {int(ms)}ms wall-clock sleep makes test flaky/slow", "use deterministic trigger, fake clock, or short condition wait"))


def _check_repetitive_tests(rel, lines, suffix, blocks, look, out):
    """Detects near-duplicate cloned test bodies (>=85% normalized match) and 5+ line copied setups."""
    normed = []
    for blk in blocks:
        if blk.ignored:
            continue
        seq = [(ln, _norm_clone_line(lines[ln - 1], suffix)) for ln in range(blk.body_start, blk.end + 1)]
        seq = [(ln, t) for ln, t in seq if t and len(t) >= 8]
        if len(seq) >= 5:
            normed.append((blk, seq, [t for _, t in seq]))

    clustered = set()
    for i, (b_a, _, seq_a) in enumerate(normed):
        if b_a.name in clustered:
            continue
        group = []
        for b_b, _, seq_b in normed[i + 1:]:
            if b_b.name in clustered:
                continue
            ratio = difflib.SequenceMatcher(None, seq_a, seq_b).ratio()
            if ratio >= 0.85:
                group.append((b_b, int(round(ratio * 100))))
        if group:
            clustered.add(b_a.name)
            for b_b, _ in group:
                clustered.add(b_b.name)
            if look(b_a.start) or any(look(b.start) for b, _ in group):
                names = ", ".join(b.name for b, _ in group[:3])
                more = f" (+{len(group) - 3})" if len(group) > 3 else ""
                out.append(core.Finding(
                    rel, b_a.start, "repetitive",
                    f"`{names}{more}` repeat `{b_a.name}` structure (>={min(p for _, p in group)}% match)",
                    "consolidate into a table-driven loop / `@pytest.mark.parametrize` / `test.each`",
                ))

    # Detect 5+ identical setup lines copied across >= 3 non-clone tests in same file
    win = 5
    windows = defaultdict(list)  # digest -> [(blk_name, first_line, sample)]
    for blk, seq, _ in normed:
        if blk.name in clustered:
            continue
        for idx in range(len(seq) - win + 1):
            chunk = [t for _, t in seq[idx:idx + win]]
            if any(re.search(r"\b(?:assert|expect)", t) for t in chunk):
                continue
            digest = hashlib.sha1("\n".join(chunk).encode()).hexdigest()
            windows[digest].append((blk.name, seq[idx][0], chunk[0]))
    seen_lines = set()
    for sites in windows.values():
        distinct = {}
        for name, ln, sample in sites:
            distinct.setdefault(name, (ln, sample))
        if len(distinct) >= 3:
            first_ln, sample = next(iter(distinct.values()))
            if first_ln not in seen_lines and any(look(ln) for ln, _ in distinct.values()):
                seen_lines.add(first_ln)
                out.append(core.Finding(
                    rel, first_ln, "repetitive",
                    f"{win}+ line test setup (`{sample[:40]}`) copied across {len(distinct)} tests",
                    "extract a shared test fixture / builder helper",
                ))


def scan_file(path, only=None, checks=None, include_baseline=True):
    return [f for f in raw_scan_file(path, only=only, checks=checks, include_baseline=include_baseline) if not core.accepted(f)]


def raw_scan_file(path, only=None, checks=None, include_baseline=True):
    rel = core.relative(path)
    suffix = path.suffix.lower()
    if suffix not in core.CODE or core.C.skip_dirs & set(Path(rel).parts) or not path.is_file():
        return []
    if _own_test_case(path, rel) or path.stat().st_size > core.MAX_BYTES:
        return []
    source = path.read_text(encoding="utf-8", errors="replace")
    is_t = core.is_test(rel)
    test_from = next((i for i, l in enumerate(source.splitlines(), 1) if l.strip() == "#[cfg(test)]"), None) if suffix == ".rs" else None
    if not is_t and test_from is None:
        return []

    wanted = checks or CHECKS
    lines = source.splitlines()
    skip = core.ignored_lines(lines)
    look = (lambda n: n not in skip) if only is None else (lambda n: n in only and n not in skip)
    blocks = _extract_test_blocks(lines, suffix, is_t, test_from)

    raw_out = []
    documented_ignored_lines = {blk.start for blk in blocks if blk.ignored and blk.has_skip_reason}
    if "self-confirming" in wanted:
        if include_baseline:
            if suffix == ".rs":
                test_rules.rust_test_checks(rel, lines, test_from or 1, look, raw_out)
                raw_out = [
                    f for f in raw_out
                    if not (f.line in documented_ignored_lines and "checks nothing" in f.problem)
                ]
                for n in range(test_from or 1, len(lines) + 1):
                    if look(n) and core.comment_of(lines[n - 1], suffix) is None:
                        before_n = len(raw_out)
                        test_rules.rust_line_checks(rel, n, lines[n - 1], raw_out)
                        raw_out[before_n:] = [f for f in raw_out[before_n:] if f.kind == "test"]
            elif suffix == ".py":
                test_rules.python_checks(rel, source, look, True, raw_out)
                raw_out = [f for f in raw_out if f.kind == "test"]
            elif suffix in core.JS and is_t:
                test_rules.js_test_checks(rel, lines, look, raw_out)
        if suffix == ".py":
            _check_python_ast(rel, source, lines, look, raw_out)
        for blk in blocks:
            _check_multiline_and_bounds(rel, lines, suffix, blk, look, raw_out)
            _check_rust_js_echo(rel, lines, suffix, blk, look, raw_out)

    for blk in blocks:
        if "undesirable" in wanted:
            _check_undesirable_lines(rel, source, lines, suffix, blk, look, raw_out)
    if "repetitive" in wanted and len(blocks) >= 2:
        _check_repetitive_tests(rel, lines, suffix, blocks, look, raw_out)

    # Deduplicate same file:line findings keeping most descriptive message.
    dedup, seen = [], set()
    for f in raw_out:
        key = (f.path, f.line, f.problem)
        if key not in seen:
            seen.add(key)
            dedup.append(f)
    return dedup


def _has_new_test(path, snap, only):
    """True when the session created a new test file or added a new test function header."""
    suffix = path.suffix.lower()
    if suffix not in core.CODE or path.stat().st_size > core.MAX_BYTES:
        return False
    rel = core.relative(path)
    if _own_test_case(path, rel) or core.C.skip_dirs & set(Path(rel).parts):
        return False
    is_t = core.is_test(rel)
    is_new_file = snap.is_file() and snap.stat().st_size == 0
    if is_new_file and is_t:
        return True
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if suffix == ".rs" and is_new_file and any("#[cfg(test)]" in ln for ln in lines):
        return True
    added = lines if only is None else [lines[n - 1] for n in sorted(only) if 1 <= n <= len(lines)]
    if suffix == ".rs":
        return any(test_rules.RUST_TEST_ATTR.match(ln) for ln in added)
    if suffix == ".py" and is_t:
        return any(re.match(r"^\s*(?:async\s+)?def\s+test_\w+\s*\(", ln) for ln in added)
    if suffix in core.JS and is_t:
        return any(test_rules.JS_TEST.match(ln) or ONLY_JS_RE.search(ln) for ln in added)
    return False


def stop_report(repo, session, state, gate, only_new_tests=False):
    """Hook report on edited test files during /tautology-audit-tests, or automatically when creating a new test."""
    edited = state / f"{session}.files"
    shown = state / f"{session}.tests_audit.shown"
    if not edited.is_file():
        return None
    os.chdir(repo)
    root = repo.resolve()
    core.use_repo(root)
    findings = []
    for raw in dict.fromkeys(edited.read_text(encoding="utf-8").splitlines()):
        path = Path(raw)
        if not raw or not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        snap = gate.snapshot_of(state, session, path)
        only = gate.new_lines(snap, path) if snap.is_file() else core.added_lines(path)
        if only is not None and not only:
            continue
        if only_new_tests and not _has_new_test(path, snap, only):
            continue
        findings += scan_file(path, only=only, include_baseline=False)
    before = set(shown.read_text(encoding="utf-8").splitlines()) if shown.is_file() else set()
    keyed = {hashlib.sha1(f.key().encode()).hexdigest()[:16]: f for f in findings}
    new = [f for k, f in keyed.items() if k not in before]
    if not new:
        return None
    with shown.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(k for k in keyed if k not in before) + "\n")
    skill_md = (HERE / "SKILL.md").as_posix()
    script_py = (HERE / "test_audit.py").as_posix()
    lines = [f"Test audit before delivering (skill tautology-audit-tests, {skill_md}).", "Findings:"]
    lines += [f"- {f}" for f in new[:20]]
    lines.append(
        "Fix self-confirming assertions, flaky skips/sleeps, and cloned tests without removing test coverage. "
        f'Record intentional exceptions with: "{Path(sys.executable).as_posix()}" "{script_py}" --accept "<finding>" --reason "<why>"'
    )
    return "\n".join(lines)


def selftest():
    """Checks self-confirming, undesirable, repetitive rules, clean fixtures, --find, and session hooks."""
    import json
    import shutil
    import subprocess
    import tempfile

    problems = []
    repo = Path(tempfile.mkdtemp(prefix="audit-tests-"))
    try:
        shutil.copytree(CASES, repo, dirs_exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        core.SELFTEST = True
        os.chdir(repo)
        core.use_repo(repo)

        found_t = find_tests("parser", root=repo)
        if not found_t or "bad_tests.rs" not in found_t[0][1]:
            problems.append(f"--find failed to locate bad_tests.rs: {found_t!r}")

        rs_bad = "\n".join(str(f) for f in scan_file(repo / "rust" / "tests" / "bad_tests.rs"))
        for needed in (
            "identical multi-line",
            "conditional `if let`",
            "vacuous loop",
            "length >= 0",
            "no reason",
            "repeat `clone_case_1` structure",
            "test setup",
        ):
            if needed not in rs_bad:
                problems.append(f"bad_tests.rs missing expected finding {needed!r}:\n{rs_bad}")

        rs_good = scan_file(repo / "rust" / "tests" / "good_tests.rs")
        if rs_good:
            problems.append(f"good_tests.rs reported false positive(s): {[str(f) for f in rs_good]!r}")

        py_bad = "\n".join(str(f) for f in scan_file(repo / "py" / "test_bad.py"))
        for needed in (
            "patches target `format_price`",
            "without failing if no exception is raised",
            "vacuous loop over `results`",
            "wall-clock sleep",
            "swallows failure",
            "repeat `test_parse_case_a` structure",
        ):
            if needed not in py_bad:
                problems.append(f"test_bad.py missing expected finding {needed!r}:\n{py_bad}")

        py_good = scan_file(repo / "py" / "test_good.py")
        if py_good:
            problems.append(f"test_good.py reported false positive(s): {[str(f) for f in py_good]!r}")

        js_bad = "\n".join(str(f) for f in scan_file(repo / "web" / "bad.test.ts"))
        for needed in (
            "identical multi-line",
            "focused `.only`",
            "empty `catch {}`",
            "wall-clock sleep",
            "vacuous loop over `items`",
        ):
            if needed not in js_bad:
                problems.append(f"bad.test.ts missing expected finding {needed!r}:\n{js_bad}")

        js_good = scan_file(repo / "web" / "good.test.ts")
        if js_good:
            problems.append(f"good.test.ts reported false positive(s): {[str(f) for f in js_good]!r}")

        # End-to-end hook isolation check: /tautology-audit-tests flag lifecycle & Stop integration
        mod_gate = MODULAR_DIR / "hooks" / "modular_gate.py"
        edit_gate = TAUTOLOGY_DIR / "hooks" / "edit_gate.py"
        stop_gate = TAUTOLOGY_DIR / "hooks" / "stop_gate.py"

        def prompt_hook(text):
            return subprocess.run(
                [sys.executable, str(mod_gate)],
                input=json.dumps({
                    "hookEventName": "PreInvocation",
                    "conversationId": "tests-selftest",
                    "cwd": str(repo),
                    "lastUserInput": text,
                }),
                capture_output=True, text=True, encoding="utf-8", cwd=repo,
            )

        r = prompt_hook("/tautology-audit-tests rust/tests/bad_tests.rs")
        flag = repo / ".git/tautology/tests-selftest.tests_audit"
        if r.returncode != 0 or not flag.is_file():
            problems.append(f"modular_gate did not set .tests_audit flag: rc={r.returncode} stderr={r.stderr}")

        prompt_hook("consolide esses testes repetitivos em uma tabela")
        if not flag.is_file():
            problems.append("modular_gate dropped .tests_audit flag on test follow-up")
        prompt_hook("agora faça git commit das alteracoes")
        if flag.is_file():
            problems.append("modular_gate kept .tests_audit flag after switching to commit")

        prompt_hook("/tautology-audit-tests")
        target = repo / "py" / "test_edited.py"
        subprocess.run(
            [sys.executable, str(edit_gate)],
            input=json.dumps({
                "hookEventName": "PreToolUse",
                "conversationId": "tests-selftest",
                "cwd": str(repo),
                "toolCall": {"name": "edit_file", "args": {"Path": str(target)}},
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        target.write_text((repo / "py" / "test_bad.py").read_text(encoding="utf-8"), encoding="utf-8")
        r1 = subprocess.run(
            [sys.executable, str(stop_gate)],
            input=json.dumps({
                "hookEventName": "Stop",
                "conversationId": "tests-selftest",
                "cwd": str(repo),
                "terminationReason": "NO_TOOL_CALL",
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        out1 = json.loads(r1.stdout or "{}")
        if out1.get("decision") != "continue" or "Test audit before delivering" not in out1.get("reason", ""):
            problems.append(f"stop_gate did not enforce test_audit during .tests_audit session: {out1!r}")

        r2 = subprocess.run(
            [sys.executable, str(stop_gate)],
            input=json.dumps({
                "hookEventName": "Stop",
                "conversationId": "tests-selftest",
                "cwd": str(repo),
                "terminationReason": "NO_TOOL_CALL",
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        if json.loads(r2.stdout or "{}") != {}:
            problems.append(f"repeated stop_gate was not suppressed: {r2.stdout!r}")

        # Auto-trigger check: creating a new test file or adding a new #[test]/def test_ WITHOUT /tautology-audit-tests
        r_build = subprocess.run(
            [sys.executable, str(mod_gate)],
            input=json.dumps({
                "hookEventName": "PreInvocation",
                "conversationId": "tests-build-prompt",
                "cwd": str(repo),
                "lastUserInput": "crie um teste unitario para o parser",
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        if not (repo / ".git/tautology/tests-build-prompt.tests_audit").is_file() or "tautology-audit-tests" not in r_build.stdout:
            problems.append(f"BUILD_TEST prompt did not activate tests_audit reminder: {r_build.stdout!r}")

        auto_file = repo / "py" / "test_auto_new.py"
        subprocess.run(
            [sys.executable, str(edit_gate)],
            input=json.dumps({
                "hookEventName": "PreToolUse",
                "conversationId": "tests-auto-selftest",
                "cwd": str(repo),
                "toolCall": {"name": "write_file", "args": {"Path": str(auto_file)}},
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        auto_file.write_text("import time\ndef test_auto_bad():\n    time.sleep(0.5)\n    assert True\n", encoding="utf-8")
        r_auto = subprocess.run(
            [sys.executable, str(stop_gate)],
            input=json.dumps({
                "hookEventName": "Stop",
                "conversationId": "tests-auto-selftest",
                "cwd": str(repo),
                "terminationReason": "NO_TOOL_CALL",
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        out_auto = json.loads(r_auto.stdout or "{}")
        if out_auto.get("decision") != "continue" or "Test audit before delivering" not in out_auto.get("reason", ""):
            problems.append(f"stop_gate did not auto-trigger test_audit on brand-new test file: {out_auto!r}")

        # Appending a new #[test] fn inside an existing file in a normal session must also auto-trigger
        rs_existing = repo / "rust" / "tests" / "good_tests.rs"
        subprocess.run(
            [sys.executable, str(edit_gate)],
            input=json.dumps({
                "hookEventName": "PreToolUse",
                "conversationId": "tests-append-selftest",
                "cwd": str(repo),
                "toolCall": {"name": "edit_file", "args": {"Path": str(rs_existing)}},
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        rs_existing.write_text(
            rs_existing.read_text(encoding="utf-8")
            + "\n#[test]\nfn added_bad_test() {\n    let xs = fetch_items();\n    assert!(xs.len() >= 0);\n}\n",
            encoding="utf-8",
        )
        r_append = subprocess.run(
            [sys.executable, str(stop_gate)],
            input=json.dumps({
                "hookEventName": "Stop",
                "conversationId": "tests-append-selftest",
                "cwd": str(repo),
                "terminationReason": "NO_TOOL_CALL",
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=repo, check=True,
        )
        out_append = json.loads(r_append.stdout or "{}")
        if out_append.get("decision") != "continue" or "length >= 0" not in out_append.get("reason", ""):
            problems.append(f"stop_gate did not auto-trigger test_audit on newly added #[test]: {out_append!r}")
    finally:
        core.SELFTEST = False
        shutil.rmtree(repo, ignore_errors=True)
    for p in problems:
        print(f"FAIL    {p}")
    print(f"test_audit selftest: {'ok' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


def _expand_targets(targets):
    out = []
    for raw in targets:
        path = Path(raw).resolve()
        if path.is_dir():
            out += sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in core.CODE)
        else:
            out.append(path)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*")
    parser.add_argument("--find", help="locate test files matching module, test name, or symbol")
    parser.add_argument("--diff", action="store_true", help="changed test lines against HEAD")
    parser.add_argument("--all", action="store_true", help="scan the whole file, not only changed lines")
    parser.add_argument("--only", help="comma-separated checks: " + ", ".join(sorted(CHECKS)))
    parser.add_argument("--accept", help="line of a finding that is not a defect")
    parser.add_argument("--reason", default="", help="why the accepted finding is kept")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.accept:
        return core.accept(args.accept, args.reason, tool="test_audit.py")
    if args.selftest:
        return selftest()
    if args.find:
        hits = find_tests(args.find)
        if not hits:
            print(f"No test files matched {args.find!r}.")
            return 1
        for score, rel in hits:
            print(f"[{score}] {rel}")
        return 0

    wanted = CHECKS
    if args.only:
        wanted = {c.strip().lower() for c in args.only.split(",")} & CHECKS
        if not wanted:
            print(f"--only takes: {', '.join(sorted(CHECKS))}")
            return 2

    targets = core.changed_files() if args.diff else args.files
    paths = _expand_targets(targets) if targets else [p for _, p, _ in repo_test_files()]
    found = []
    for path in paths:
        only = None if args.all else core.added_lines(path)
        if only is not None and not only:
            continue
        found += scan_file(path, only=only, checks=wanted)
    for f in found:
        print(f)
    print(f"{len(found)} test finding(s).")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
