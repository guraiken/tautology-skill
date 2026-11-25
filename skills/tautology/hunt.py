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

_LEAN_DESIGN_FN = None
_LEAN_DESIGN_CHECKED = False


def _lean_design_scanner():
    """Returns design_audit.scan_lean_design when available (canonical owner of [design] layout/slop rules)."""
    global _LEAN_DESIGN_FN, _LEAN_DESIGN_CHECKED
    loaded = sys.modules.get("design_audit")
    if loaded is not None and hasattr(loaded, "scan_lean_design"):
        return loaded.scan_lean_design
    if not _LEAN_DESIGN_CHECKED:
        _LEAN_DESIGN_CHECKED = True
        da_path = HERE.parent / "tautology-debloat-design" / "design_audit.py"
        if da_path.is_file():
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("_tautology_design_audit", da_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _LEAN_DESIGN_FN = getattr(mod, "scan_lean_design", None)
            except Exception:
                _LEAN_DESIGN_FN = None
    return _LEAN_DESIGN_FN


def _expand_only_for_jsx(raw_lines, norm_lines, only):
    """If an edited line was absorbed into a multi-line JSX opening tag, mark its opening line edited too."""
    if only is None:
        return None
    expanded = set(only)
    for ln in only:
        if 1 <= ln <= len(raw_lines) and raw_lines[ln - 1].strip() and not norm_lines[ln - 1]:
            for back in range(ln - 1, 0, -1):
                if norm_lines[back - 1]:
                    expanded.add(back)
                    break
    return expanded


def sibling_model_constants(path):
    """String constants in a sibling model.rs of the same screen folder (e.g. HINT_IDLE)."""
    if path.suffix.lower() != ".rs" or path.name == "model.rs":
        return {}
    model = path.parent / "model.rs"
    if not model.is_file() or model.stat().st_size > MAX_BYTES:
        return {}
    out = {}
    for line in model.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r'^\s*(?:pub(?:\([^)]*\))?\s+)?const\s+([A-Z0-9_]+)\s*:\s*&str\s*=\s*"((?:[^"\\]|\\.)*)"\s*;', line)
        if m and is_prose(m.group(2)):
            out[m.group(1)] = m.group(2)
    return out


def scan_file(path, only=None, include_bloat=True, include_design=True):
    """only: lines to look at (None = all). Returns the findings, minus the accepted ones."""
    return [f for f in raw_scan_file(path, only, include_bloat=include_bloat, include_design=include_design)
            if not accepted(f)]


def raw_scan_file(path, only=None, include_bloat=True, include_design=True):
    rel = relative(path)
    suffix = path.suffix.lower()
    if suffix not in CODE | TEXT or C.skip_dirs & set(Path(rel).parts) or not path.is_file():
        return []
    if own_cases(path, rel):
        return []
    if path.stat().st_size > MAX_BYTES:
        return []
    source = path.read_text(encoding="utf-8", errors="replace")
    raw_lines = source.splitlines()
    raw_only = only
    lines = raw_lines
    if suffix in JSX:
        norm = normalize_jsx_tags(raw_lines)
        only = _expand_only_for_jsx(raw_lines, norm, only)
        lines = jsx_lines(norm, normalized=True)
    skip = ignored_lines(raw_lines)
    look = (lambda n: n not in skip) if only is None else (lambda n: n in only and n not in skip)
    out = []
    if suffix in TEXT:
        scan_prose(rel, lines, look, out)
        return out
    test = is_test(rel)
    ui = is_ui(rel, source) and not test
    # Rust: the #[cfg(test)] module at the end repeats the messages on purpose, as expectations.
    test_from = rust_test_start(lines) if suffix == ".rs" else None
    seen_literals = defaultdict(list)
    ui_literals = []  # (line number, text, code line)
    model_consts = sibling_model_constants(path) if ui else {}
    for number, line in enumerate(lines, 1):
        if not look(number):
            continue
        comment = comment_of(line, suffix)
        if comment is not None:
            text_checks(rel, number, comment, out)
            check_comment_restates_code(rel, number, comment, lines, suffix, out)
            continue
        in_test = test or (test_from is not None and number >= test_from)
        row = table_row(line, suffix)
        for literal in literals(line, suffix):
            if not is_prose(literal):
                # A one-word label or placeholder still names its field: "Email" against "Enter your email".
                if (ui and not test and (LABEL_LINE.search(line) or PLACEHOLDER_ATTR.search(line))
                        and on_screen(line, literal) and re.fullmatch(r"[^\W\d_][\w-]{1,30}", literal.strip())):
                    ui_literals.append((number, literal, line))
                continue
            text_checks(rel, number, literal, out)
            if not in_test and not row and not OFF_SCREEN.search(line):
                seen_literals[plain(literal)].append(number)
            if ui and not in_test and on_screen(line, literal):
                ui_literals.append((number, literal, line))
        if ui and not in_test and model_consts:
            for m in re.finditer(r"\bmodel::([A-Z0-9_]+)\b", strip_literals(line)):
                const_text = model_consts.get(m.group(1))
                if const_text:
                    ui_literals.append((number, const_text, line))
        for ident in IDENT_RE.findall(strip_literals(line)):
            parts = tokens(ident)
            if any(a == b and len(a) >= 3 and not a.isdigit() for a, b in zip(parts, parts[1:])):
                out.append(Finding(rel, number, "code", f'name stutters: {ident}', "use the term once"))
                break
        if number > 1 and look(number - 1):
            prev = lines[number - 2].strip()
            # Same text at another indentation closes a different block (an except, then the function).
            if lines[number - 2].rstrip() == line.rstrip() and len(prev) >= 12 and not prev.endswith(",") and not re.fullmatch(r"[\s{}()\[\];,]*(end|fi|done)?", prev):
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
    repeated = set()
    for norm, numbers in seen_literals.items():
        # Code organisation, not too much on screen: only in the whole-file scan (--all), not in the hook.
        if only is None and len(numbers) >= 3 and len(norm.split()) >= 4:
            repeated.add(norm)
            out.append(Finding(rel, numbers[1], "code", f'the same text appears {len(numbers)}x (lines '
                               f'{", ".join(map(str, numbers[:4]))})', "one constant or one text"))
    near_repeats(rel, ui_literals, out, lines, whole=only is None, repeated=repeated)
    if ui:
        bloat_checks(rel, lines, suffix, look, skip, test_from, out, full=include_bloat)
        lean_design = _lean_design_scanner() if include_design else None
        clutter_checks(rel, lines, suffix, look, skip, test_from, out,
                       include_layout_design=include_design and lean_design is None)
        if lean_design is not None:
            out.extend(lean_design(path, rel, source, core.REPO, only=raw_only))
    if suffix == ".rs" and (test or test_from):
        rust_test_checks(rel, lines, 1 if test else test_from, look, out)
    if suffix == ".py":
        python_checks(rel, source, look, test, out)
    if suffix in JS and test:
        js_test_checks(rel, lines, look, out)
    return out




def expand_paths(paths):
    out = []
    for raw in paths:
        path = Path(raw).resolve()
        if path.is_dir():
            out += sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in CODE | TEXT)
        else:
            out.append(path)
    return out


def raw_findings_for(path):
    return raw_scan_file(path, only=None) + [
        Finding(r, n, "copy", f'new UI text "{t}"', "")
        for r, n, t, _ in new_ui_texts(path, only=None, keep_accepted=True)
    ]


def scan(paths, whole=False, prune=False):
    expanded = expand_paths(paths)
    found = []
    for path in expanded:
        only = None if whole else added_lines(path)
        if only is not None and not only:
            continue
        found += scan_file(path, only)
    if prune:
        core.prune_paths(expanded, raw_findings_for, "hunt.py")
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

        # Default lean Stop hook: skips chat-only turns with no edited files, and catches objective tautologies
        # without blocking on bloat/New UI texts unless opted in (or in debloat-design mode).
        if scan_message("O trecho deriva do `id` do bloco."):
            problems.append("scan_message false positive on doubled word separated by inline code")
        if is_test("skills/tautology/test_rules.py") or is_test("skills/tautology-audit-tests/test_audit.py"):
            problems.append("is_test misclassified skill rule/audit module as a unit test suite")
        code, report = hook("stop_gate.py", "Stop", transcript_path=str(transcript))
        if code != 0 or report:
            problems.append(f"default Stop without edited files exited {code}, expected 0")
        hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(target)})
        target.parent.mkdir(parents=True)
        target.write_text((CASES / "app/src/explicacoes.rs").read_text(encoding="utf-8"), encoding="utf-8")
        code, lean_report = hook("stop_gate.py", "Stop", transcript_path=str(transcript))
        if code != 2 or "says the same as" not in lean_report:
            problems.append(f"default lean Stop missed objective tautology: code={code} {lean_report!r}")
        for noise in ("is wordy", "text with 3 sentences", "return back", "New UI texts"):  # tautology: example
            if noise in lean_report:
                problems.append(f"default lean Stop emitted opt-in noise {noise!r}")

        # Opt-in full hook mode (bloat + New UI texts + chat answer scan)
        cfg_path = repo / ".claude" / "tautology" / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg.update({"hook_scan_answer": True, "hook_review_new_ui_texts": True, "hook_bloat": True})
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        shown_file = repo / ".git" / "tautology" / "selftest.shown"
        if shown_file.is_file():
            shown_file.unlink()
        code, report = hook("stop_gate.py", "Stop", transcript_path=str(transcript))
        if code != 2:
            problems.append(f"Stop with a new finding exited {code}, expected 2")
        for needed in ("is wordy", "says the same as", "text with 3 sentences", "return back",  # tautology: example
                       "New UI texts", "--accept -" if sys.platform == "win32" else "--accept"):  # tautology: example
            if needed not in report:
                problems.append(f"hook report without {needed!r}")
        if sys.platform == "win32" and '--accept "<finding>"' in report:
            problems.append("Windows hook report suggested --accept \"<finding>\" instead of --accept -")
        for forbidden in ("Nunca abrir", "Client key", "Carregando tarefa", "Excluir esta tarefa"):
            if forbidden in report.split("New UI texts")[0]:
                problems.append(f"false positive in the hook: {forbidden!r}")
        code, _ = hook("stop_gate.py", "Stop", transcript_path=str(transcript))
        if code != 0:
            problems.append(f"repeated Stop exited {code}, expected 0 (finding already shown)")
        code, _ = hook("stop_gate.py", "Stop", transcript_path=str(transcript), stop_hook_active=True)
        if code != 0:
            problems.append(f"stop_hook_active exited {code}, expected 0")
        ui_ts = repo / "app" / "src" / "Panel.tsx"
        hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(ui_ts)})
        ui_ts.write_text(
            'import React from "react"\n'
            'const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches\n'
            'export const Panel = () => <div><span>Order saved safely</span></div>\n',
            encoding="utf-8",
        )
        code, ui_report = hook("stop_gate.py", "Stop", transcript_path=str(transcript))
        if "prefers-reduced-motion" in ui_report:
            problems.append("stop_gate flagged window.matchMedia argument as New UI text")
        ui_lines = [l for l in ui_report.splitlines() if '[copy] new UI text "Order saved safely"' in l]
        if not ui_lines:
            problems.append(f"New UI texts missing --accept-compatible line:\n{ui_report}")
        else:
            stripped_ui = ui_lines[0].replace('"', "")
            bad_acc = subprocess.run([sys.executable, str(HERE / "hunt.py"), "--accept", stripped_ui, "--reason", "stripped test"],
                                     capture_output=True, text=True, encoding="utf-8", env=env, cwd=repo)
            if bad_acc.returncode == 0:
                problems.append("hunt.py --accept accepted a quote-stripped finding line")
            for _ in range(2):
                acc = subprocess.run([sys.executable, str(HERE / "hunt.py"), "--accept", "-", "--reason", "valid copy"],
                                     input=ui_lines[0] + "\n", capture_output=True, text=True, encoding="utf-8", env=env, cwd=repo)
                if acc.returncode != 0:
                    problems.append(f"hunt.py --accept - rejected New UI texts line: {acc.stdout}{acc.stderr}")
            acc_rows = [l for l in (repo / ".claude" / "tautology" / "accepted.txt").read_text(encoding="utf-8").splitlines()
                        if l and not l.startswith("#")]
            if len(acc_rows) != 1:
                problems.append(f"hunt.py --accept duplicated entry in accepted.txt: {acc_rows!r}")
            git_st = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
            if "accepted.txt" in git_st:
                problems.append(f"accepted.txt was not auto-ignored in git status: {git_st!r}")
            ui_ts.write_text("export const Panel = () => <div />\n", encoding="utf-8")
            hook("stop_gate.py", "Stop", transcript_path=str(transcript))
            if (repo / ".claude" / "tautology" / "accepted.txt").exists():
                problems.append("stop_gate did not delete resolved accepted.txt")
            excl = repo / ".git" / "info" / "exclude"
            if excl.is_file() and "accepted.txt" in excl.read_text(encoding="utf-8"):
                problems.append("stop_gate did not clean accepted.txt from .git/info/exclude")

        # Refactored existing UI strings that only moved lines/structure must not be flagged as new UI text.
        modal_rs = repo / "app" / "src" / "telegram_modal.rs"
        modal_rs.write_text(
            'use gpui::{div, prelude::*};\n'
            'fn render() -> impl IntoElement {\n'
            '    div()\n'
            '        .child("Buscar no Telegram")\n'
            '        .child("Ofertas antigas dos seus canais")\n'
            '        .child("Nenhuma oferta encontrada")\n'
            '}\n',
            encoding="utf-8",
        )
        hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(modal_rs)})
        modal_rs.write_text(
            'use gpui::{div, prelude::*};\n'
            'fn header() -> impl IntoElement { div().child("Buscar no Telegram") }\n'
            'fn empty() -> impl IntoElement {\n'
            '    div()\n'
            '        .child("Ofertas antigas dos seus canais")\n'
            '        .child("Nenhuma oferta encontrada")\n'
            '        .child("Filtro salvo com sucesso")\n'
            '}\n',
            encoding="utf-8",
        )
        _, refactor_report = hook("stop_gate.py", "Stop", transcript_path=str(transcript))
        for moved in ("Buscar no Telegram", "Ofertas antigas dos seus canais", "Nenhuma oferta encontrada"):
            if f'new UI text "{moved}"' in refactor_report:
                problems.append(f"stop_gate flagged moved existing UI text as new: {moved!r}\n{refactor_report}")
        if 'new UI text "Filtro salvo com sucesso"' not in refactor_report:
            problems.append(f"stop_gate missed genuinely new UI text after refactor:\n{refactor_report}")

        # Status dot with .rounded_full() + .bg() placed before .size(px(8.)) triggers decorative status dot + tic guidance.
        dot_rs = repo / "app" / "src" / "status_bar.rs"
        hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(dot_rs)})
        dot_rs.write_text(
            'use gpui::{div, prelude::*, px};\n'
            'fn status_bar(p: Palette) -> impl IntoElement {\n'
            '    div()\n'
            '        .child(\n'
            '            div()\n'
            '                .rounded_full()\n'
            '                .bg(p.success)\n'
            '                .size(px(8.)),\n'
            '        )\n'
            '        .child(div().text_color(p.success).child("Monitorando"))\n'
            '}\n',
            encoding="utf-8",
        )
        _, dot_report = hook("stop_gate.py", "Stop", transcript_path=str(transcript))
        if "decorative status dot" not in dot_report or "AI visual tics:" not in dot_report:
            problems.append(f"stop_gate missed decorative status dot or tic guidance:\n{dot_report}")

        # An svg().size(px(8.)) inside a rounded_full() pill must not inherit the parent div()'s rounded_full/bg.
        svg_pill_rs = repo / "app" / "src" / "svg_badge.rs"
        hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(svg_pill_rs)})
        svg_pill_rs.write_text(
            'use gpui::{div, prelude::*, px, svg};\n'
            'fn status_badge(p: Palette) -> impl IntoElement {\n'
            '    div()\n'
            '        .rounded_full()\n'
            '        .bg(p.surface)\n'
            '        .child(\n'
            '            svg()\n'
            '                .size(px(8.))\n'
            '                .text_color(p.success),\n'
            '        )\n'
            '}\n',
            encoding="utf-8",
        )
        _, svg_report = hook("stop_gate.py", "Stop", transcript_path=str(transcript))
        if "decorative status dot" in svg_report:
            problems.append(f"status_dots bled parent rounded_full/bg into child svg():\n{svg_report}")
        code, _ = hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(repo / "missing" / "x.rs")})
        if code != 0:
            problems.append(f"PreToolUse on a new file exited {code}, expected 0")
        outside = Path(tempfile.gettempdir()) / "tautology-outside-secret.py"
        env_file = repo / ".env"
        env_file.write_text("SECRET=1\n", encoding="utf-8")
        hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(outside)})
        hook("edit_gate.py", "PreToolUse", tool_input={"file_path": str(env_file)})
        tracked_files = (repo / ".git" / "tautology" / "selftest.files").read_text(encoding="utf-8")
        if "tautology-outside-secret" in tracked_files or ".env" in tracked_files:
            problems.append("edit_gate tracked an external or .env file")

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
        if code != 0 or out.get("decision") != "continue" or "return back" not in out.get("reason", ""):  # tautology: example
            problems.append(f"Antigravity Stop with a new finding returned {code} {out!r}")
        code, out = agy_hook("stop_gate.py", "Stop", transcriptPath=str(agy_transcript), terminationReason="NO_TOOL_CALL")
        if code != 0 or out != {}:
            problems.append(f"Antigravity repeated Stop returned {code} {out!r}, expected empty object")
        code, out = agy_hook("stop_gate.py", "Stop", transcriptPath=str(agy_transcript), continuationActive=True)
        if code != 0 or out != {}:
            problems.append(f"Antigravity continuationActive returned {code} {out!r}, expected empty object")

        # Regression checks: core/copy/answer/test rules + hook safety & normalization
        # 1. normalize_jsx_tags preserves 1-to-1 line count, catches Prettier nested cards, avoids multiline self-close FP
        pretty_tsx = repo / "app" / "src" / "PrettyNested.tsx"
        pretty_tsx.write_text(
            'export const PrettyNested = ({ x }: Props): JSX.Element => {\n'
            '  const [s, setS] = useState<\n'
            '    string | null\n'
            '  >(null);\n'
            '  return (\n'
            '    <div\n'
            '      // outer card\n'
            '      className={cn(\n'
            '        "border rounded p-4",\n'
            '        x && "bg-card"\n'
            '      )}\n'
            '    >\n'
            '      <div\n'
            '        className="border rounded p-4"\n'
            '      >\n'
            '        <div\n'
            '          className="border rounded p-4"\n'
            '        >\n'
            '          <p>This page shows {x} saved reminders</p>\n'
            '          <p>\n'
            "            Don't forget to return back\n"  # tautology: example
            '          </p>\n'
            '          <span>✅</span>\n'
            '        </div>\n'
            '      </div>\n'
            '    </div>\n'
            '  );\n'
            '};\n',
            encoding="utf-8",
        )
        pretty_raw = pretty_tsx.read_text(encoding="utf-8").splitlines()
        if len(normalize_jsx_tags(pretty_raw)) != len(pretty_raw):
            problems.append("normalize_jsx_tags changed line count")
        pretty_out = "\n".join(str(f) for f in raw_scan_file(pretty_tsx))
        for needed in ("3 cards nested", "This page shows", "return back", "decorative emoji"):  # tautology: example
            if needed not in pretty_out:
                problems.append(f"PrettyNested.tsx missing {needed!r}:\n{pretty_out}")
        selfclose_tsx = repo / "app" / "src" / "SelfCloseCards.tsx"
        selfclose_tsx.write_text(
            'export const SelfCloseCards = React.memo(({ items }: Props) => (\n'
            '  <div>\n'
            '    <Card\n'
            '      title="One"\n'
            '    />\n'
            '    <Card\n'
            '      title="Two"\n'
            '    />\n'
            '    <Card\n'
            '      title="Three"\n'
            '    />\n'
            '  </div>\n'
            '));\n',
            encoding="utf-8",
        )
        sc_out = [str(f) for f in raw_scan_file(selfclose_tsx)]
        if any("cards nested" in s for s in sc_out):
            problems.append(f"multiline self-closing <Card /> triggered nested_cards FP: {sc_out!r}")

        # 2. Hyphenated UI prose preserved, Tailwind utility skipped, PT FILLER strips verb, split_top quote-aware
        for prose_line in ('const a = "Auto-save enabled";', 'const b = "Enter your e-mail address";'):
            if not literals(prose_line, ".tsx"):
                problems.append(f"JS literals() dropped hyphenated prose: {prose_line!r}")
        if literals('const c = "flex items-center gap-2";', ".tsx"):
            problems.append("JS literals() failed to skip Tailwind utility string")
        if core_concepts("Digite seu telefone") != {"telefon"}:
            problems.append(f"PT FILLER kept verb: {core_concepts('Digite seu telefone')!r}")
        if split_top('"a, b", "c"') != ['"a, b"', '"c"']:
            problems.append(f"split_top broke on comma inside quotes: {split_top('\"a, b\", \"c\"')!r}")

        # 3. Python/Qt massive setToolTip caught; render_owner recognizes typed/memo/forwardRef; LEGAL narrows \d
        qt_py = repo / "app" / "src" / "qt_tip.py"
        qt_py.write_text(
            'from PySide6.QtWidgets import QPushButton\n'
            'def build():\n'
            '    btn = QPushButton("Save")\n'
            '    btn.setToolTip("This tooltip explains way too much across multiple sentences. It exceeds two balloon lines easily.")\n',
            encoding="utf-8",
        )
        qt_out = "\n".join(str(f) for f in raw_scan_file(qt_py))
        if "tooltip of ~" not in qt_out:
            problems.append(f"Python/Qt tooltip_checks missed massive setToolTip:\n{qt_out}")
        for hdr in (
            "export const P = ({ x }: Props): JSX.Element => {",
            "export const P = React.memo(({ x }: Props) => {",
            "export const P = forwardRef<HTMLDivElement, Props>((p, r) => {",
        ):
            if render_owner([hdr, "  return <div />;"], 2) != (1, "P"):
                problems.append(f"render_owner missed component header: {hdr!r}")
        if LEGAL.search("Changes are saved every 5 seconds."):
            problems.append("LEGAL regex still matches bare digit in non-legal footnote")

        # 4. Partial limits merge, EN security populated, .agents without config.json does not shadow .claude/config.json
        tmp_cfg = repo / "partial_cfg.json"
        tmp_cfg.write_text('{"languages": ["en"], "limits": {"button": [5, 32, 1]}}', encoding="utf-8")
        s_test = core.Settings()
        s_test.load(tmp_cfg)
        if not {"button", "title", "hint", "notice", "text"} <= set(s_test.limits):
            problems.append(f"Settings.load partial limits clobbered defaults: {list(s_test.limits)!r}")
        if s_test.security is None or not s_test.security.search("Your payment was declined"):
            problems.append("EN-only Settings.security missing payment terms")
        (repo / ".agents" / "tautology").mkdir(parents=True, exist_ok=True)
        (repo / ".agents" / "tautology" / "accepted.txt").write_text("# empty\n", encoding="utf-8")
        if core.config_file_for(repo, "tautology") != repo / ".claude" / "tautology" / "config.json":
            problems.append(f"config_file_for shadowed .claude/config.json: {core.config_file_for(repo, 'tautology')}")

        # 5. TIC_PROBLEM covers TICS.md rows 20-21 & modal cards; answer_rules inline triple backticks; Rust #[should_panic]
        for tic_msg in (
            "bar: 2 competing primary accents in one view (limit 1)",
            "bar: stacked filter controls (2 trigger(s) + exposed filter-chip strip)",
            "bar: fixed-width filter/sort trigger (175px) crowds toolbar",
            "modal: inner boxed card inside a modal (nested surface)",
            "view: overloaded hierarchy with 4 nested styled boxes (limit 3)",
        ):
            if not TIC_PROBLEM.search(tic_msg):
                problems.append(f"TIC_PROBLEM missed {tic_msg!r}")
        ans_hits = scan_message("```code```\nPlease return back.\n")  # tautology: example
        if not ans_hits or ans_hits[0].line != 2:
            problems.append(f"answer_rules failed after inline triple backticks: {[str(f) for f in ans_hits]!r}")
        rs_sp_out = []
        rust_test_checks("tests/sp.rs", [
            "#[test]", "fn empty_test() {", "    let _x = 1;", "}",
            "#[test]", "#[should_panic]", "fn expects_panic() {", "    explode();", "}",
        ], 1, lambda n: True, rs_sp_out)
        if len(rs_sp_out) != 1 or "empty_test" not in rs_sp_out[0].problem:
            problems.append(f"rust_test_checks #[should_panic] misattributed check: {[str(f) for f in rs_sp_out]!r}")

        # 6. edit_gate anchors relative paths when cwd is plugin/bin & tracks .css/.md; stop_gate respects USER_CANCELED
        rel_css = repo / "app" / "src" / "panel.css"
        rel_css.write_text(".p { color: #ff00aa; }\n", encoding="utf-8")
        rel_run = subprocess.run(
            [sys.executable, str(hooks / "edit_gate.py")],
            input=json.dumps({
                "hookEventName": "PreToolUse", "conversationId": "rel-check", "cwd": str(repo),
                "toolCall": {"name": "edit_file", "args": {"Path": "app/src/panel.css"}},
            }),
            capture_output=True, text=True, encoding="utf-8", cwd=str(HERE.parents[1] / "bin"),
        )
        rel_files = repo / ".git" / "tautology" / "rel-check.files"
        if rel_run.returncode != 0 or not rel_files.is_file() or "panel.css" not in rel_files.read_text(encoding="utf-8"):
            problems.append("edit_gate failed to anchor relative .css path from plugin bin/")
        (repo / ".git" / "tautology" / "agy-selftest.tics").write_text("app/src/screen2.rs\n", encoding="utf-8")
        code, cancel_out = agy_hook("stop_gate.py", "Stop", continuationActive=True, terminationReason="USER_CANCELED")
        if code != 0 or cancel_out != {}:
            problems.append(f"stop_gate fought USER_CANCELED on continuation turn: {code} {cancel_out!r}")
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
    parser.add_argument("--accept", help="finding line (or '-' to read from stdin) that is not a tautology")
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
    if args.diff:
        targets = changed_files()
        if args.files:
            scoped = {p.resolve() for p in expand_paths(args.files)}
            targets = [p for p in targets if p.resolve() in scoped]
    else:
        targets = args.files
    found = scan(targets, whole=args.all, prune=args.all and not args.diff and not wanted and not args.rates)
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
