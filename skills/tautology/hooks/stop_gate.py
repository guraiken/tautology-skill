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
LIMIT = 25


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load("edit_gate", HERE / "edit_gate.py")


def last_answer(transcript):
    """The agent's text after the user's last message."""
    texts = []
    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        message = entry.get("message") or {}
        content = message.get("content")
        if entry.get("type") == "user":
            real = isinstance(content, str) or any(
                isinstance(c, dict) and c.get("type") == "text" for c in (content or []))
            if real:
                texts = []
        elif entry.get("type") == "assistant" and isinstance(content, list):
            texts += [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
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
    findings += hunt.scan_message(last_answer(payload.get("transcript_path", "")))
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
    sys.stderr.reconfigure(encoding="utf-8")
    payload = json.load(sys.stdin)
    if payload.get("hook_event_name") != "Stop" or payload.get("stop_hook_active"):
        return 0
    repo = gate.repo_of(payload)
    session, state = gate.session_of(payload), gate.state_dir(repo)
    reports = [stop_report(payload, repo, session, state)]
    if MODULAR.is_file():
        reports.append(load("modular_gate", MODULAR).stop_report(repo.resolve(), session, state, gate))
    reports = [r for r in reports if r]
    if not reports:
        return 0
    print("\n\n".join(reports), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
