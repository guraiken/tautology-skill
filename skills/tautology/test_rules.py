"""Tests that confirm themselves (constant vs constant, a value the test wrote itself, a test with no
check) and the per-line checks of Rust and Python code (a function that only forwards its arguments)."""
import ast
import re
from collections import Counter

import core
from core import *  # noqa: F403


# Trait methods (impl From for ...) exist for the compiler, not to shorten a call.
TRAIT_FNS = {"from", "into", "try_from", "fmt", "deref", "deref_mut", "as_ref", "borrow", "default", "clone",
             "eq", "hash", "drop", "index", "partial_cmp", "cmp"}


def macro_args(line, start):
    """Top-level arguments of a call that opens at line[start - 1] == "("."""
    depth, end = 1, start
    while end < len(line) and depth:
        depth += {"(": 1, ")": -1}.get(line[end], 0)
        end += 1
    return split_top(line[start:end - 1])


RUST_LET = re.compile(r"^\s*let\s+(?:mut\s+)?(\w+)\s*(?::[^=]+)?=\s*(.+?);\s*$")


RUST_FN = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)")
RUST_TEST_ATTR = re.compile(r"^\s*#\[(?:\w+::)*test\b")
RUST_CHECK = re.compile(r"\b(assert\w*!|panic!|unreachable!|unwrap\w*\(|expect\w*\(|check\w*\(|verify\w*\()")


RUST_LITERAL = re.compile(r'-?\d[\d_.]*\w*|"(?:[^"\\]|\\.)*"|true|false|\'.\'')


JS_LET = re.compile(r"^\s*(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=\s*(.+?);?\s*$")


JS_TEST = re.compile(r"^\s*(?:it|test)(?:\.\w+)?\(\s*[\"'\x60]")


JS_LITERAL = re.compile(r"-?\d[\d_.]*n?|([\"'\x60])(?:(?!\1).)*\1|true|false|null|undefined")


JS_EXPECT = re.compile(r"\bexpect\(")


JS_MATCHER = re.compile(r"\)\s*(?:\.not)?\.(toBe|toEqual|toStrictEqual|toMatchObject)\(")


JS_ASSERT = re.compile(r"\bassert\.(?:strict)?(?:equal|deepEqual|strictEqual|deepStrictEqual|notEqual)\(")


def js_test_checks(rel, lines, look, out):
    """JS/TS tests (Jest, Vitest, node:test): the same rules as for Rust and Python."""
    lets, count, since, opened, checks = {}, Counter(), {}, None, 0

    def close():
        if opened and look(opened) and checks == 0:
            out.append(Finding(rel, opened, "test", "test without expect or assert: checks nothing",
                               "check what the code produces"))

    for number, line in enumerate(lines, 1):
        if JS_TEST.match(line):
            close()
            lets, count, since, opened, checks = {}, Counter(), {}, number, 0
            continue
        m = JS_LET.match(line)
        if m:
            lets[m.group(1)] = m.group(2).strip()
            count[m.group(1)] += 1
            since[m.group(1)] = True
            continue
        pairs = []
        for call in JS_EXPECT.finditer(line):
            checks += 1
            args = split_top(macro_args_js(line, call.end()))
            rest = line[call.end():]
            matcher = JS_MATCHER.search(rest)
            if args and matcher:
                expected = split_top(macro_args_js(rest, matcher.end()))
                if expected:
                    pairs.append((args[0].strip(), expected[0].strip()))
        for call in JS_ASSERT.finditer(line):
            checks += 1
            args = split_top(macro_args_js(line, call.end()))
            if len(args) >= 2:
                pairs.append((args[0].strip(), args[1].strip()))
        if re.search(r"\bassert\(|\bassert\.\w+\(", line) and not JS_ASSERT.search(line):
            checks += 1
        if line.strip() and not pairs:
            since = dict.fromkeys(since, False)
        if not look(number):
            continue
        for a, b in pairs:
            if a == b and a in ("true", "false"):
                out.append(Finding(rel, number, "test", "checks a constant against itself", FIX_EXPECTED))
                continue
            sides = []
            for arg in (a, b):
                known = count[arg] == 1 and arg in lets and since.get(arg, False)
                sides.append((lets[arg] if known else arg, known))
            (x, rx), (y, ry) = sides
            if x == y and (rx or ry or not JS_LITERAL.fullmatch(x)):
                problem = SELF_WRITTEN if JS_LITERAL.fullmatch(x) else SAME_CALL
                out.append(Finding(rel, number, "test", problem, FIX_EXPECTED))
    close()


def macro_args_js(line, start):
    """Text between the parenthesis opened at start-1 and the one that closes it."""
    depth = 1
    for index in range(start, len(line)):
        depth += {"(": 1, ")": -1}.get(line[index], 0)
        if depth == 0:
            return line[start:index]
    return line[start:]


def rust_test_checks(rel, lines, start, look, out):
    """Rust tests: the expected value is a variable the test built from the same expression, or a test without checks."""
    lets, count, since = {}, Counter(), {}
    pending_test, opened, fn_name, checks = False, None, "", 0

    def close():
        if opened and look(opened) and checks == 0:
            out.append(Finding(rel, opened, "test", f"{fn_name} checks nothing (no assert)",
                               "check the result, or delete the test"))

    for number in range(start, len(lines) + 1):
        line = lines[number - 1]
        if RUST_TEST_ATTR.match(line):
            pending_test = True
            continue
        if re.match(r"^\s*#\[should_panic\b", line):
            checks += 1
            continue
        fn_match = RUST_FN.match(line)
        if fn_match:
            close()
            lets, count, since = {}, Counter(), {}
            if pending_test:
                opened, fn_name = number, fn_match.group(1)
                checks = 1 if "->" in line else 0
                brace = line.find("{")
                if brace >= 0 and RUST_CHECK.search(line[brace:]):
                    checks += 1
            else:
                opened, fn_name, checks = None, "", 0
            pending_test = False
            continue
        if opened and RUST_CHECK.search(line):
            checks += 1
        m = RUST_LET.match(line)
        if m:
            lets[m.group(1)] = m.group(2).strip()
            count[m.group(1)] += 1
            since[m.group(1)] = True  # True: nothing ran after the let (a before/after snapshot is a valid test)
            continue
        if line.strip() and not line.strip().startswith("//") and not re.match(r"\s*assert_(eq|ne)!\(", line):
            since = dict.fromkeys(since, False)
        if not look(number):
            continue
        for call in re.finditer(r"\b(assert_eq|assert_ne)!\(", line):
            args = macro_args(line, call.end())
            if len(args) < 2:
                continue
            sides = []
            for arg in args[:2]:
                known = count[arg] == 1 and arg in lets and since.get(arg, False)
                sides.append((lets[arg] if known else arg, known))
            (a, ra), (b, rb) = sides
            if (ra or rb) and a == b:
                if RUST_LITERAL.fullmatch(a):
                    problem = SELF_WRITTEN
                else:
                    problem = SAME_CALL
                out.append(Finding(rel, number, "test", problem, FIX_EXPECTED))
    close()


def rust_line_checks(rel, number, line, out):
    for m in re.finditer(r"\b(assert_eq|assert_ne)!\(", line):
        args = macro_args(line, m.end())
        if len(args) >= 2 and args[0] == args[1]:
            out.append(Finding(rel, number, "test", f"{m.group(1)}! compares the expression with itself",
                               "compare with the expected value, from outside the code under test"))
    if re.search(r"\bassert!\(\s*true\s*[,)]", line):
        out.append(Finding(rel, number, "test", "assert!(true) checks nothing", "check a result"))
    m = re.search(r"\bfn\s+(\w+)\s*\(([^)]*)\)[^{;]*\{\s*([\w:]+)\(([^)]*)\)\s*\}", line)
    if m and m.group(1) not in TRAIT_FNS:
        params = [p.split(":")[0].strip().lstrip("&").replace("mut ", "") for p in split_top(m.group(2))]
        params = [p for p in params if p and "self" not in p]
        if params and params == split_top(m.group(4)):
            out.append(Finding(rel, number, "code", f"fn {m.group(1)} only forwards to {m.group(3)}",
                               FIX_DIRECT))


# --------------------------------------------------------------------------- python
def python_checks(rel, source, look, test, out):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    module_functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if test and node.name.startswith("test"):
            if look(node.lineno):
                test_function_checks(rel, node, out)
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Assert, ast.Call)) and look(getattr(sub, "lineno", 0)):
                    assertion_checks(rel, sub, out)
            self_written_checks(rel, node, look, out)
            inlined_identical(rel, node, look, out)
            continue
        if not look(node.lineno):
            continue
        docstring = ast.get_docstring(node)
        if docstring:
            said = set(words(docstring))
            if said and said <= set(tokens(node.name)) | {"returns", "retorna", "function", "funcao", "method", "metodo"}:
                out.append(Finding(rel, node.lineno, "code", f'docstring of {node.name} only repeats the name',
                                   "delete it, or say what the name does not"))
        pass_through(rel, node, module_functions, out)


def pass_through(rel, node, module_functions, out):
    body = node.body[1:] if ast.get_docstring(node) else node.body
    if len(body) != 1 or node.decorator_list or not isinstance(body[0], (ast.Return, ast.Expr)):
        return
    call = body[0].value
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        return
    params = [a.arg for a in node.args.args + node.args.kwonlyargs if a.arg not in ("self", "cls")]
    passed = [a.id for a in call.args if isinstance(a, ast.Name)]
    passed += [k.value.id for k in call.keywords if isinstance(k.value, ast.Name) and k.arg == k.value.id]
    if params and len(passed) == len(call.args) + len(call.keywords) and passed == params and not node.args.vararg:
        out.append(Finding(rel, node.lineno, "code", f"{node.name}() only forwards to {call.func.id}()",
                           FIX_DIRECT))


CHECK_CALLS = re.compile(r"^(assert|expect|check|verify|raises|fail|_assert|_check|_expect|compare|ensure|wait_until)", re.I)


def test_function_checks(rel, node, out):
    checks = False
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Assert, ast.Raise)):
            checks = True
        elif isinstance(sub, ast.Call):
            name = sub.func.attr if isinstance(sub.func, ast.Attribute) else getattr(sub.func, "id", "")
            if CHECK_CALLS.match(name or ""):
                checks = True
    if not checks:
        out.append(Finding(rel, node.lineno, "test", f"{node.name} checks nothing (no assert)",
                           "check the result, or delete the test"))


def assertion_checks(rel, node, out):
    if isinstance(node, ast.Assert):
        test = node.test
        if isinstance(test, ast.Constant) and test.value:
            out.append(Finding(rel, node.lineno, "test", "assert of a true constant", "check a result"))
        elif (isinstance(test, ast.Compare) and len(test.ops) == 1
              and isinstance(test.ops[0], (ast.Eq, ast.Is, ast.GtE, ast.LtE))
              and ast.dump(test.left) == ast.dump(test.comparators[0])):
            out.append(Finding(rel, node.lineno, "test", "compares the expression with itself",
                               FIX_EXPECTED))
        return
    name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
    if name in ("assertEqual", "assertIs", "assertEquals", "assertCountEqual", "assertDictEqual",
                "assertListEqual") and len(node.args) >= 2 and ast.dump(node.args[0]) == ast.dump(node.args[1]):
        out.append(Finding(rel, node.lineno, "test", f"{name} compares the expression with itself",
                           FIX_EXPECTED))
    if name == "assertTrue" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value:
        out.append(Finding(rel, node.lineno, "test", "assertTrue of a constant", "check a result"))


def inlined_identical(rel, node, look, out):
    """r = f(x) ... assert r == f(x): the expected value is the call that made the result, renamed."""
    count, value = Counter(), {}
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign):
            for target in sub.targets:
                if isinstance(target, ast.Name):
                    count[target.id] += 1
                    value[target.id] = sub.value

    order = {id(stmt): i for i, stmt in enumerate(node.body)}
    where = {}
    for i, stmt in enumerate(node.body):
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    where[target.id] = i

    def untouched(name, check):
        """Only assignments between the assignment and the assert: nothing changed the state in between."""
        end = order.get(id(check))
        return end is not None and all(isinstance(s, (ast.Assign, ast.Assert)) for s in node.body[where[name] + 1:end])

    def resolve(side, check):
        if (isinstance(side, ast.Name) and count[side.id] == 1 and isinstance(value[side.id], ast.Call)
                and side.id in where and untouched(side.id, check)):
            return value[side.id], True
        return side, False

    for sub in ast.walk(node):
        pair = None
        if isinstance(sub, ast.Assert) and isinstance(sub.test, ast.Compare) and len(sub.test.comparators) == 1:
            pair = (sub.test.left, sub.test.comparators[0])
        elif (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
              and sub.func.attr in ("assertEqual", "assertIs") and len(sub.args) >= 2):
            pair = (sub.args[0], sub.args[1])
        if not pair or not look(getattr(sub, "lineno", 0)):
            continue
        statement = sub if isinstance(sub, ast.Assert) else next(
            (s for s in node.body if isinstance(s, ast.Expr) and s.value is sub), None)
        (a, ra), (b, rb) = resolve(pair[0], statement), resolve(pair[1], statement)
        if (ra or rb) and ast.dump(a) == ast.dump(b):
            out.append(Finding(rel, sub.lineno, "test", SAME_CALL,
                               FIX_EXPECTED))


def self_written_checks(rel, node, look, out):
    """x = 5 ... assert x == 5 with nothing in between that uses x: the test checks what it wrote."""
    consts, assigned = {}, Counter()
    for sub in ast.walk(node):
        targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target] if isinstance(sub, (ast.AugAssign, ast.AnnAssign)) else []
        for target in targets:
            if isinstance(target, ast.Name):
                assigned[target.id] += 1
                if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Constant):
                    consts[target.id] = sub.value.value
    used_in_calls = {n.id for sub in ast.walk(node) if isinstance(sub, ast.Call)
                     for n in ast.walk(sub) if isinstance(n, ast.Name)}
    for sub in ast.walk(node):
        if not (isinstance(sub, ast.Assert) and isinstance(sub.test, ast.Compare) and look(sub.lineno)):
            continue
        sides = [sub.test.left, *sub.test.comparators]
        if len(sides) != 2:
            continue
        for a, b in (sides, sides[::-1]):
            if (isinstance(a, ast.Name) and isinstance(b, ast.Constant) and a.id in consts
                    and assigned[a.id] == 1 and consts[a.id] == b.value and a.id not in used_in_calls):
                out.append(Finding(rel, sub.lineno, "test", f"checks the value the test itself put in {a.id}",
                                   "check what the code produces"))
                break

