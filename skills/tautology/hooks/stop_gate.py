"""Stop hook of the tautology skill (and of the modular skill, when installed next to it).

Runs, in one process, the tautology hunt over the lines this session changed and over the agent's
last answer, plus the modular skill's search for copied code. A new finding sends the answer back
once (exit 2) with the list; a finding already shown never blocks again, and stop_hook_active
lets the answer through, so the agent is never caught in a loop.
"""
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
MODULAR = SKILL.parent / "modular" / "hooks" / "modular_gate.py"
DESIGN_AUDIT = SKILL.parent / "tautology-debloat-design" / "design_audit.py"
TEST_AUDIT = SKILL.parent / "tautology-audit-tests" / "test_audit.py"
LIMIT = 25


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load("edit_gate", HERE / "edit_gate.py")


def last_answer(transcript):
    """The agent's text after the user's last message (Claude Code and Antigravity transcripts)."""
    if not transcript:
        return ""
    texts = []
    path = Path(transcript)
    full = path.with_name("transcript_full.jsonl")
    if full.is_file():
        path = full
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        kind = entry.get("type")
        message = entry.get("message") or {}
        content = message.get("content")
        if kind == "user":
            real = isinstance(content, str) or any(
                isinstance(c, dict) and c.get("type") == "text" for c in (content or []))
            if real:
                texts = []
        elif kind == "assistant" and isinstance(content, list):
            texts += [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        elif kind == "USER_INPUT":
            texts = []
        elif kind == "PLANNER_RESPONSE" and isinstance(entry.get("content"), str):
            texts.append(entry["content"])
    return "\n\n".join(t for t in texts if t.strip())


def command(*args):
    """How to run a skill script with this interpreter, for the messages to the agent."""
    return " ".join(f'"{a}"' if " " in str(a) else str(a) for a in (Path(sys.executable).as_posix(), *args))


def stop_report(payload, repo, session, state):
    """The text that sends the answer back to the agent, or None when nothing new was found."""
    edited, shown = state / f"{session}.files", state / f"{session}.shown"
    sys.path.insert(0, str(SKILL))
    hunt = load("hunt", SKILL / "hunt.py")
    os.chdir(repo)
    hunt.core.use_repo(repo)
    files = []
    if edited.is_file():
        files = [Path(p) for p in dict.fromkeys(edited.read_text(encoding="utf-8").splitlines()) if p]
    findings, review = [], []
    root = repo.resolve()
    for path in (p for p in files if p.is_file() and p.is_relative_to(root)):
        snap = gate.snapshot_of(state, session, path)
        only = gate.new_lines(snap, path) if snap.is_file() else hunt.added_lines(path)
        if only is None or only:
            findings += hunt.scan_file(path, only)
            review += hunt.new_ui_texts(path, only)
    transcript = payload.get("transcript_path") or payload.get("transcriptPath") or ""
    findings += hunt.scan_message(last_answer(transcript))
    before = set(shown.read_text(encoding="utf-8").splitlines()) if shown.is_file() else set()
    keys = {hashlib.sha1(f.key().encode()).hexdigest()[:16]: f for f in findings}
    new = [f for k, f in keys.items() if k not in before]
    flagged = {(f.path, f.line) for f in findings}  # already under "Findings": not listed again
    texts = {hashlib.sha1(f"ui:{rel}:{text}".encode()).hexdigest()[:16]: (rel, n, text, near)
             for rel, n, text, near in review if (rel, n) not in flagged}
    new_texts = [t for k, t in texts.items() if k not in before]
    if not new and not new_texts:
        return None
    with shown.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(k for k in [*keys, *texts] if k not in before) + "\n")
    report = [f"Tautology hunt before delivering (skill tautology, {(SKILL / 'SKILL.md').as_posix()})."]
    if new:
        report.append("Findings:")
        report += [f"- {f}" for f in new[:LIMIT]]
        if len(new) > LIMIT:
            report.append(f"(+{len(new) - LIMIT} findings)")
    if new_texts:
        # The comparison only sees words and synonyms; the agent judges the paraphrase.
        report.append("New UI texts: check whether one repeats its neighbour in other words, explains what "
                      "the control already shows, or holds more than one idea:")
        for rel, n, text, near in new_texts[:LIMIT]:
            beside = "; ".join(f'"{t[:40]}"' for _, t in near[:4]) or "none"
            report.append(f'- {rel}:{n} "{text}" (neighbours: {beside})')
    report.append("Fix what is a tautology. Keep what is not, say why in one line, and record it: "
                  + command((SKILL / "hunt.py").as_posix(), "--accept", '"<finding>"', "--reason", '"<reason>"')
                  + " (it holds for later sessions). In the answer, say only what changed: do not repeat the "
                  "previous delivery.")
    return "\n".join(report)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    payload = json.load(sys.stdin)
    agy = gate.is_antigravity(payload)
    reason = payload.get("terminationReason")
    if (gate.event_of(payload) != "Stop" or payload.get("stop_hook_active") or payload.get("continuationActive")
            or (reason and reason != "NO_TOOL_CALL")):
        if agy:
            print("{}")
        return 0
    repo = gate.repo_of(payload)
    session, state = gate.session_of(payload), gate.state_dir(repo)
    reports = [stop_report(payload, repo, session, state)]
    if MODULAR.is_file():
        reports.append(load("modular_gate", MODULAR).stop_report(repo.resolve(), session, state, gate))
    if (state / f"{session}.design").is_file() and DESIGN_AUDIT.is_file():
        reports.append(load("design_audit", DESIGN_AUDIT).stop_report(repo.resolve(), session, state, gate))
    if TEST_AUDIT.is_file():
        reports.append(load("test_audit", TEST_AUDIT).stop_report(
            repo.resolve(), session, state, gate,
            only_new_tests=not (state / f"{session}.tests_audit").is_file(),
        ))
    reports = [r for r in reports if r]
    if not reports:
        if agy:
            print("{}")
        return 0
    joined = "\n\n".join(reports)
    if agy:
        print(json.dumps({"decision": "continue", "reason": joined}, ensure_ascii=False))
        return 0
    print(joined, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
