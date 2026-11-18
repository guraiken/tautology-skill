"""Hook of the modular skill: when a component is created or changed, or in a refactor, looks for the
same code sequence copied in several places and tells the agent what to extract and where to.

UserPromptSubmit: marks the session as a refactor when the request talks about refactoring,
extracting, modularizing or reorganizing.
Stop (called by the tautology skill's stop_gate.py): looks at the files the session edited (noted by
the tautology skill's edit_gate.py, which also keeps the copy from before, so it knows a new file).
A new pattern that touches those files sends the answer back once with the briefing.
The hook does not edit code: the agent extracts, following the briefing.
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
SKILL = HERE.parents[1]
EDIT_GATE = SKILL.parent / "tautology" / "hooks" / "edit_gate.py"
REFACTOR = re.compile(r"(?i)\b(refactor\w*|extract\w*|moduli[sz]\w*|reorgani[sz]\w*|componenti[sz]\w*|"
                      r"refator\w*|extra(ir|ia|ção|cao)\w*|modulariz\w*|reorganiz\w*|componentiz\w*)")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    payload = json.load(sys.stdin)
    agy = "hookEventName" in payload
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    if event not in ("UserPromptSubmit", "PreInvocation"):
        if agy:
            print("{}")
        return 0
    workspace = (payload.get("workspacePaths") or [None])[0]
    repo = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or workspace or ".").resolve()
    state = load("edit_gate", EDIT_GATE).state_dir(repo)
    raw_session = payload.get("session_id") or payload.get("conversationId") or "session"
    session = "".join(c for c in str(raw_session) if c.isalnum() or c in "-_")[:80]
    flag = state / f"{session}.refactor"
    prompt = payload.get("prompt") or payload.get("lastUserInput") or ""
    if REFACTOR.search(prompt):  # every request passes here: no detector loaded
        flag.write_text("1", encoding="utf-8")
    elif flag.exists():
        flag.unlink()
    if agy:
        print("{}")
    return 0


def stop_report(repo, session, state, gate):
    """The extraction briefing that sends the answer back, or None when there is no new pattern."""
    flag, shown = state / f"{session}.refactor", state / f"{session}.modular.shown"
    edited = state / f"{session}.files"
    if not edited.is_file():
        return None
    patterns = load("patterns", SKILL / "patterns.py")
    arch = patterns.Architecture(repo)
    refactor = flag.exists()
    touched, triggers = {}, []
    for raw in dict.fromkeys(edited.read_text(encoding="utf-8").splitlines()):
        path = Path(raw)
        if not raw or not path.is_file() or not path.resolve().is_relative_to(repo):
            continue
        rel = path.resolve().relative_to(repo).as_posix()
        if (path.suffix not in patterns.CODE or arch.excluded(rel) or patterns.core.is_test(rel)
                or rel.startswith(".claude/") or rel.startswith(".agents/")):
            continue
        snap = gate.snapshot_of(state, session, path)
        new = snap.is_file() and snap.stat().st_size == 0
        changed = None if new or not snap.is_file() else gate.new_lines(snap, path)
        if changed == set():
            continue  # edited and undone
        if arch.is_component(rel):
            touched[rel] = changed
            triggers.append(f"new component {rel}" if new else f"changed component {rel}")
        elif refactor:
            touched[rel] = changed
            triggers.append(f"refactor in {rel}")
    if not touched:
        return None
    os.chdir(repo)
    suffixes = {Path(rel).suffix for rel in touched}
    found = patterns.findings(repo, arch, touched, suffixes)
    before = set(shown.read_text(encoding="utf-8").splitlines()) if shown.is_file() else set()
    keyed = [(hashlib.sha1(f"{f.rule}:{f.subject}".encode()).hexdigest()[:16], f) for f in found]
    new = [(k, f) for k, f in keyed if k not in before]
    if not new:
        return None
    batch = new[:patterns.PER_ROUND]
    with shown.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(k for k, _ in batch) + "\n")
    trigger = "; ".join(triggers[:3]) + (f" (+{len(triggers) - 3})" if len(triggers) > 3 else "")
    return patterns.briefing([f for _, f in new], arch, trigger)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # a broken hook must never block a prompt
        print(f"modular_gate: {error}", file=sys.stderr)
        print("{}")
        sys.exit(0)
