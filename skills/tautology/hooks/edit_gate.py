"""PreToolUse hook (Edit|Write|MultiEdit|NotebookEdit) of the tautology skill.

Before the first edit of a file in a session it keeps a copy of the file and notes its path, so the
Stop hook judges only what the session changed (also for files outside git, and with other agents
editing the same tree). It never blocks: any failure exits 0.
"""
import difflib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def state_dir(repo):
    """Per-session state under the git dir (never committed); .tautology-state outside git."""
    git = Path(repo, ".git")
    if git.is_dir():
        path = git / "tautology"
    else:
        out = subprocess.run(["git", "rev-parse", "--git-path", "tautology"], cwd=repo, capture_output=True,
                             text=True).stdout.strip()
        path = Path(repo, out) if out else Path(repo, ".tautology-state")
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_antigravity(payload):
    return "hookEventName" in payload


def event_of(payload):
    return payload.get("hook_event_name") or payload.get("hookEventName") or ""


def session_of(payload):
    raw = payload.get("session_id") or payload.get("conversationId") or "session"
    return "".join(c for c in str(raw) if c.isalnum() or c in "-_")[:80]


def repo_of(payload):
    workspace = (payload.get("workspacePaths") or [None])[0]
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or workspace or ".")


def target_of(payload):
    tool_input = payload.get("tool_input") or (payload.get("toolCall") or {}).get("args") or {}
    return (tool_input.get("file_path") or tool_input.get("notebook_path")
            or tool_input.get("Path") or tool_input.get("TargetFile"))


def snapshot_of(state, session, path):
    key = hashlib.sha1(str(Path(path).resolve()).lower().encode()).hexdigest()[:20]
    return state / f"{session}.snap" / key


def new_lines(before, path):
    """Lines of the current file that were not in the copy taken before the session edited it."""
    old = before.read_text(encoding="utf-8", errors="replace").splitlines()
    new = path.read_text(encoding="utf-8", errors="replace").splitlines()
    lines = set()
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            lines.update(range(j1 + 1, j2 + 1))
    return lines


def main():
    payload = json.load(sys.stdin)
    target = target_of(payload)
    if event_of(payload) != "PreToolUse" or not target:
        if is_antigravity(payload):
            print('{"decision": "allow"}')
        return 0
    state, session = state_dir(repo_of(payload)), session_of(payload)
    snap = snapshot_of(state, session, target)
    if not snap.exists():  # first edit of the file in this session: copy and note
        snap.parent.mkdir(parents=True, exist_ok=True)
        source = Path(target)
        snap.write_bytes(source.read_bytes() if source.is_file() else b"")
        with (state / f"{session}.files").open("a", encoding="utf-8") as handle:
            handle.write(str(source.resolve()) + "\n")
    if is_antigravity(payload):
        print('{"decision": "allow"}')
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # a broken hook must never block an edit
        print(f"tautology edit_gate: {error}", file=sys.stderr)
        print('{"decision": "allow"}')
        sys.exit(0)
