"""Hunts tautology in UI copy, tests, code and the agent's answers: what repeats, what confirms itself.

    python hunt.py --diff                     # changes against HEAD
    python hunt.py a.py b.rs                  # new lines of these files
    python hunt.py --all a.py                 # the whole file
    python hunt.py --message answer.md        # a text by the agent
    python hunt.py --diff --only copy,tests   # only these competencies
    python hunt.py --accept "<finding>" --reason "<why>"
    python hunt.py --rates --all <files>      # accepted vs found per rule
    python hunt.py --selftest                 # cases/ vs cases/expected.txt, and the hooks

Competencies (finding kind): copy = [copy], code = [code], tests = [test], answer = [answer].
Rules live in copy_rules.py, test_rules.py and answer_rules.py; the shared part in core.py; this file
dispatches, holds the CLI and the selftest. Project settings: .claude/tautology/config.json.
One line per finding: file:line [kind] problem -> fix. Exit code 1 when there are findings.
Findings are candidates: the skill (SKILL.md) decides what is a tautology.
"""
import argparse
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
from core import *  # noqa: E402,F403
from copy_rules import *  # noqa: E402,F403
from test_rules import *  # noqa: E402,F403
from answer_rules import *  # noqa: E402,F403

COMPETENCIES = {"copy": "copy", "code": "code", "tests": "test", "answer": "answer"}


def scan_file(path, only=None):
    """only: lines to look at (None = all). Returns the findings, minus the accepted ones."""
    return [f for f in raw_scan_file(path, only) if not accepted(f)]


def raw_scan_file(path, only=None):
    rel = relative(path)
    suffix = path.suffix.lower()
    if suffix not in CODE | TEXT or C.skip_dirs & set(Path(rel).parts) or not path.is_file():
        return []
    if own_cases(path, rel):
        return []
    if path.stat().st_size > MAX_BYTES:
        return []
    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    if suffix in JSX:
        lines = jsx_lines(lines)
    skip = ignored_lines(lines)
    look = (lambda n: n not in skip) if only is None else (lambda n: n in only and n not in skip)
    out = []
    if suffix in TEXT:
        scan_prose(rel, lines, look, out)
        return out
    test = is_test(rel)
    ui = is_ui(rel, source) and not test
    # Rust: the #[cfg(test)] module at the end repeats the messages on purpose, as expectations.
    test_from = next((i for i, l in enumerate(lines, 1) if l.strip() == "#[cfg(test)]"), None) if suffix == ".rs" else None
    seen_literals = defaultdict(list)
    ui_literals = []  # (line number, text, code line)
    for number, line in enumerate(lines, 1):
        if not look(number):
            continue
        comment = comment_of(line, suffix)
        if comment is not None:
            text_checks(rel, number, comment, out)
            check_comment_restates_code(rel, number, comment, lines, suffix, out)
            continue
        in_test = test or (test_from is not None and number >= test_from)
        for literal in literals(line, suffix):
            if not is_prose(literal):
                continue
            text_checks(rel, number, literal, out)
            if not in_test:
                seen_literals[plain(literal)].append(number)
            if ui and not in_test and on_screen(line, literal):
                ui_literals.append((number, literal, line))
        for ident in IDENT_RE.findall(strip_literals(line)):
            parts = tokens(ident)
            if any(a == b and len(a) >= 3 and not a.isdigit() for a, b in zip(parts, parts[1:])):
                out.append(Finding(rel, number, "code", f'name stutters: {ident}', "use the term once"))
                break
        if number > 1 and look(number - 1):
            prev = lines[number - 2].strip()
            if prev == line.strip() and len(prev) >= 12 and not prev.endswith(",") and not re.fullmatch(r"[\s{}()\[\];,]*(end|fi|done)?", prev):
                out.append(Finding(rel, number, "code", "line identical to the previous one", "delete the copy"))
        if suffix == ".rs":
            rust_line_checks(rel, number, line, out)
        m = TOOLTIP_RE.search(line)
        if m:
            target = re.escape(m.group(1))
            window = lines[max(0, number - 9):number + 8]
            if any(re.search(LABEL_RE.format(target), other) for other in window):
                out.append(Finding(rel, number, "copy", f"tooltip shows the same {m.group(1)} as the label",
                                   "drop the tooltip, or say something the control does not show"))
    for norm, numbers in seen_literals.items():
        # Code organisation, not too much on screen: only in the whole-file scan (--all), not in the hook.
        if only is None and len(numbers) >= 2 and len(norm.split()) >= 4:
            out.append(Finding(rel, numbers[1], "code", f'the same text appears {len(numbers)}x (lines '
                               f'{", ".join(map(str, numbers[:4]))})', "one constant or one text"))
    near_repeats(rel, ui_literals, out, lines, whole=only is None)
    if ui:
        bloat_checks(rel, lines, suffix, look, skip, test_from, out)
    if suffix == ".rs" and (test or test_from):
        rust_test_checks(rel, lines, 1 if test else test_from, look, out)
    if suffix == ".py":
        python_checks(rel, source, look, test, out)
    if suffix in JS and test:
        js_test_checks(rel, lines, look, out)
    return out




def scan(paths, whole=False):
    found = []
    for path in paths:
        path = Path(path).resolve()
        only = None if whole else added_lines(path)
        if only is not None and not only:
            continue
        found += scan_file(path, only)
    return found



def rates(found):
    """Accepted vs found per rule: a rule with many accepted findings is badly calibrated."""
    current = Counter(f.rule for f in found)
    taken = Counter(rule for _, rule, *_ in load_accepted())
    print(f"{'accepted':>8} {'found':>8} {'% accepted':>10}  rule")
    for rule in sorted(set(current) | set(taken), key=lambda r: -(taken[r] / max(1, taken[r] + current[r]))):
        share = 100 * taken[rule] / max(1, taken[rule] + current[rule])
        print(f"{taken[rule]:>8} {current[rule]:>8} {share:>9.0f}%  {rule}")


def selftest(record=False):
    """Runs the hunt over cases/ (with cases/config.json) and compares with cases/expected.txt."""
    core.SELFTEST = True
    C.load(CASES / "config.json")
    os.chdir(CASES)
    files = sorted(p for p in CASES.rglob("*") if p.is_file() and p.suffix in CODE and "answers" not in p.parts)
    found = [str(f).split(" -> ")[0] for f in scan(files, whole=True)]
    for answer in sorted((CASES / "answers").glob("*.md")):
        found += [str(f).split(" -> ")[0] for f in
                  scan_message(answer.read_text(encoding="utf-8"), label=f"answers/{answer.name}")]
    if record:
        return compare_expected(found, CASES / "expected.txt", record=True)
    differs = compare_expected(found, CASES / "expected.txt")
    hook_problems = hook_selftest()
    return 1 if differs or hook_problems else 0


def hook_selftest():
    """The hooks end to end in a temporary repository: block a new finding, never repeat it, let go."""
    import json
    import shutil
    import tempfile
    hooks = HERE / "hooks"
    repo = Path(tempfile.mkdtemp(prefix="tautology-"))
    problems = []
    try:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / ".claude" / "tautology").mkdir(parents=True)
        shutil.copy(CASES / "config.json", repo / ".claude" / "tautology" / "config.json")
        target = repo / "app" / "src" / "screen.rs"
        transcript = repo / "conversation.jsonl"
        answer = "Done. Now the button rises up for the scroll; please return back to it."  # tautology: example
        transcript.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in [
            {"type": "user", "message": {"content": "request"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}},
        ]) + "\n", encoding="utf-8")
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}

        def hook(script, event, **extra):
            payload = {"hook_event_name": event, "session_id": "selftest", "cwd": str(repo), **extra}
            run = subprocess.run([sys.executable, str(hooks / script)], input=json.dumps(payload), capture_output=True,
                                 text=True, encoding="utf-8", env=env, cwd=repo)
            return run.returncode, run.stderr

        hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(target)})
        target.parent.mkdir(parents=True)
        target.write_text((CASES / "app/src/explicacoes.rs").read_text(encoding="utf-8"), encoding="utf-8")
        code, report = hook("stop_gate.py", "Stop", transcript_path=str(transcript))
        if code != 2:
            problems.append(f"Stop with a new finding exited {code}, expected 2")
        for needed in ("is wordy", "says the same as", "text with 3 sentences", "return back", "New UI texts",
                       "--accept"):  # tautology: example
            if needed not in report:
                problems.append(f"hook report without {needed!r}")
        for forbidden in ("Nunca abrir", "Client key", "Carregando tarefa", "Excluir esta tarefa"):
            if forbidden in report.split("New UI texts")[0]:
                problems.append(f"false positive in the hook: {forbidden!r}")
        code, _ = hook("stop_gate.py", "Stop", transcript_path=str(transcript))
        if code != 0:
            problems.append(f"repeated Stop exited {code}, expected 0 (finding already shown)")
        code, _ = hook("stop_gate.py", "Stop", transcript_path=str(transcript), stop_hook_active=True)
        if code != 0:
            problems.append(f"stop_hook_active exited {code}, expected 0")
        code, _ = hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(repo / "missing" / "x.rs")})
        if code != 0:
            problems.append(f"PreToolUse on a new file exited {code}, expected 0")

        # Antigravity hook protocol (camelCase payload, JSON on stdout, exit 0)
        agy_transcript = repo / "transcript.jsonl"
        agy_transcript.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in [
            {"type": "USER_INPUT", "content": "request"},
            {"type": "PLANNER_RESPONSE", "content": answer},
        ]) + "\n", encoding="utf-8")

        def agy_hook(script, event, **extra):
            payload = {"hookEventName": event, "conversationId": "agy-selftest", "cwd": str(repo), **extra}
            run = subprocess.run([sys.executable, str(hooks / script)], input=json.dumps(payload), capture_output=True,
                                 text=True, encoding="utf-8", cwd=repo)
            return run.returncode, json.loads(run.stdout or "{}")

        target2 = repo / "app" / "src" / "screen2.rs"
        code, out = agy_hook("edit_gate.py", "PreToolUse", toolCall={"name": "edit_file", "args": {"Path": str(target2)}})
        if code != 0 or out.get("decision") != "allow":
            problems.append(f"Antigravity PreToolUse returned {code} {out!r}")
        target2.write_text((CASES / "app/src/explicacoes.rs").read_text(encoding="utf-8"), encoding="utf-8")
        code, out = agy_hook("stop_gate.py", "Stop", transcriptPath=str(agy_transcript), terminationReason="NO_TOOL_CALL")
        if code != 0 or out.get("decision") != "continue" or "return back" not in out.get("reason", ""):
            problems.append(f"Antigravity Stop with a new finding returned {code} {out!r}")
        code, out = agy_hook("stop_gate.py", "Stop", transcriptPath=str(agy_transcript), terminationReason="NO_TOOL_CALL")
        if code != 0 or out != {}:
            problems.append(f"Antigravity repeated Stop returned {code} {out!r}, expected empty object")
        code, out = agy_hook("stop_gate.py", "Stop", transcriptPath=str(agy_transcript), continuationActive=True)
        if code != 0 or out != {}:
            problems.append(f"Antigravity continuationActive returned {code} {out!r}, expected empty object")
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    for problem in problems:
        print(f"HOOK    {problem}")
    print(f"hooks: {'ok' if not problems else f'{len(problems)} problem(s)'}")
    return problems


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*")
    parser.add_argument("--diff", action="store_true", help="files changed against HEAD")
    parser.add_argument("--all", action="store_true", help="the whole file, not only the new lines")
    parser.add_argument("--message", help="file with a text by the agent")
    parser.add_argument("--only", help="comma-separated competencies: " + ", ".join(COMPETENCIES))
    parser.add_argument("--accept", help="line of a finding that is not a tautology")
    parser.add_argument("--reason", default="", help="why the accepted finding is not a tautology")
    parser.add_argument("--rates", action="store_true", help="accepted vs found per rule")
    parser.add_argument("--selftest", action="store_true", help="checks the hunt against cases/expected.txt")
    parser.add_argument("--record", action="store_true", help="with --selftest: records the output as expected")
    args = parser.parse_args()
    if args.accept:
        return accept(args.accept, args.reason)
    if args.selftest:
        return selftest(record=args.record)
    wanted = None
    if args.only:
        wanted = {COMPETENCIES.get(c.strip().lower()) for c in args.only.split(",")} - {None}
        if not wanted:
            print(f"--only takes: {', '.join(COMPETENCIES)}")
            return 2
    found = scan(changed_files() if args.diff else args.files, whole=args.all)
    if args.rates:
        rates(found)
        return 0
    if args.message:
        found += scan_message(Path(args.message).read_text(encoding="utf-8", errors="replace"))
    if wanted:
        found = [f for f in found if f.kind in wanted]
    for finding in found:
        print(finding)
    print(f"{len(found)} finding(s).")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
