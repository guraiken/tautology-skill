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
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
PLUGIN_ROOT = SKILL.parents[1]
SELF_IMPROVE_FILE = PLUGIN_ROOT / "SELF_IMPROVE.local.md"
MODULAR = SKILL.parent / "modular" / "hooks" / "modular_gate.py"
DESIGN_AUDIT = SKILL.parent / "tautology-debloat-design" / "design_audit.py"
TEST_AUDIT = SKILL.parent / "tautology-audit-tests" / "test_audit.py"
LIMIT = 8  # with the layout audit (4) a report stays near 12 lines
STALE_SESSION_SECONDS = 14 * 86400
GC_INTERVAL_SECONDS = 86400


def _cleanup_stale_state(state, session):
    """Prunes old per-session snapshots and markers at most once a day."""
    stamp = state / ".last_gc"
    now = time.time()
    try:
        if stamp.is_file() and (now - stamp.stat().st_mtime) < GC_INTERVAL_SECONDS:
            return
        stamp.write_text("", encoding="utf-8")
        cutoff = now - STALE_SESSION_SECONDS
        groups = {}
        for entry in state.iterdir():
            name = entry.name
            if name.startswith(".") or "." not in name:
                continue
            sid = name.split(".", 1)[0]
            if sid == session:
                continue
            groups.setdefault(sid, []).append(entry)
        for entries in groups.values():
            try:
                if max(e.stat().st_mtime for e in entries) >= cutoff:
                    continue
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
                except OSError:
                    continue
    except OSError:
        return


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


def _config_flag(repo, key, default=False):
    """Lightweight JSON read so fast-path Stop hooks can skip importing heavy modules."""
    for folder in (".agents", ".claude"):
        cfg = Path(repo) / folder / "tautology" / "config.json"
        if cfg.is_file():
            try:
                return bool(json.loads(cfg.read_text(encoding="utf-8")).get(key, default))
            except (OSError, ValueError):
                return default
    return default


def _edited_files(state, session, repo):
    edited = state / f"{session}.files"
    if not edited.is_file():
        return []
    root = repo.resolve()
    files = [Path(p) for p in dict.fromkeys(edited.read_text(encoding="utf-8").splitlines()) if p]
    return [p for p in files if p.is_file() and p.is_relative_to(root)]


def _config_signature(repo):
    """Changing the project config must re-run the scan even when no file changed."""
    for folder in (".agents", ".claude"):
        cfg = Path(repo) / folder / "tautology" / "config.json"
        try:
            st = cfg.stat()
            return f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            continue
    return "none"


def _changed_since_last_stop(files, state, session, repo):
    """Returns only files whose size or mtime changed since the previous Stop check."""
    stamp_file = state / f"{session}.checked"
    previous = {}
    if stamp_file.is_file():
        for line in stamp_file.read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, value = line.partition("\t")
            if sep:
                previous[key] = value
    config_sig = _config_signature(repo)
    config_changed = previous.get("__config__") != config_sig
    changed = []
    updated = dict(previous, __config__=config_sig)
    for path in files:
        try:
            st = path.stat()
            sig = f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            continue
        key = str(path.resolve())
        if config_changed or previous.get(key) != sig:
            changed.append(path)
            updated[key] = sig
    if changed or config_changed:
        stamp_file.write_text("\n".join(f"{k}\t{v}" for k, v in updated.items()) + "\n", encoding="utf-8")
    return changed


UI_HINT = {  # Rust generics (Vec<String>) look like tags: only JS/TS reads tags as markup
    ".js": re.compile(r"document\.createElement|\.innerHTML\b|\bReact\b|\bjsx\b"),
    ".ts": re.compile(r"document\.createElement|\.innerHTML\b|\bReact\b|\bjsx\b"),
    ".py": re.compile(r"\b(?:PySide\d|PyQt\d|tkinter|wx|kivy)\b"),
    ".rs": re.compile(r"\b(?:gpui|egui|iced|slint)\b"),
}


def _has_ui_candidate(files):
    """Cheap pre-check before importing the layout audit: JSX/TSX, or a source that builds a screen."""
    for path in files:
        suffix = path.suffix.lower()
        if suffix in {".jsx", ".tsx", ".vue", ".svelte"}:
            return True
        if suffix in UI_HINT:
            try:
                if UI_HINT[suffix].search(path.read_text(encoding="utf-8", errors="replace")):
                    return True
            except OSError:
                continue
    return False


SKILL_NON_TEST = re.compile(r"(^|/)(?:\.(?:claude|agents)/)?skills/[^/]+/test_(?:rules|audit)\.py$")


def _has_test_candidate(files):
    for path in files:
        if SKILL_NON_TEST.search(path.as_posix()):
            continue
        name = path.name.lower()
        parts = {p.lower() for p in path.parts}
        if ({"tests", "test", "__tests__"} & parts
                or name.startswith("test_")
                or name.endswith(("_test.py", "_test.rs", ".test.ts", ".test.tsx", ".test.js", ".test.jsx",
                                  ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx"))):
            return True
        if path.suffix.lower() == ".rs":
            try:
                if "#[cfg(test)]" in path.read_text(encoding="utf-8", errors="replace"):
                    return True
            except OSError:
                continue
    return False


def stop_report(payload, repo, session, state, files=None, design_mode=False):
    """The text that sends the answer back to the agent, or None when nothing new was found."""
    shown = state / f"{session}.shown"
    if files is None:
        files = _edited_files(state, session, repo)
    if not files and not _config_flag(repo, "hook_scan_answer", False):
        return None
    sys.path.insert(0, str(SKILL))
    hunt = load("hunt", SKILL / "hunt.py")
    os.chdir(repo)
    hunt.core.use_repo(repo)
    findings, review = [], []
    root = repo.resolve()
    include_bloat = design_mode or hunt.C.hook_bloat
    review_ui = design_mode or hunt.C.hook_review_new_ui_texts
    before_sources = {}
    removed_by_file = {}
    if review_ui:
        multi = len(files) > 1
        for path in files:
            snap = gate.snapshot_of(state, session, path)
            before_src = snap.read_text(encoding="utf-8", errors="replace") if snap.is_file() else hunt.core.head_source(path)
            before_sources[path] = before_src
            suffix = path.suffix.lower()
            if multi and before_src and suffix in {".py", ".rs"} | hunt.core.JS and path.is_file() and path.stat().st_size <= hunt.core.MAX_BYTES:
                old_c = Counter(t.strip() for _, t in hunt.screen_ui_texts(before_src, suffix))
                cur_src = path.read_text(encoding="utf-8", errors="replace")
                new_c = Counter(t.strip() for _, t in hunt.screen_ui_texts(cur_src, suffix))
                diff = old_c - new_c
                if diff:
                    removed_by_file[path] = diff
    for path in files:
        snap = gate.snapshot_of(state, session, path)
        only = gate.new_lines(snap, path) if snap.is_file() else hunt.added_lines(path)
        if only is None or only:
            findings += hunt.scan_file(path, only, include_bloat=include_bloat)
            if review_ui:
                sibling_moved = Counter()
                if removed_by_file:
                    for other, removed in removed_by_file.items():
                        if other != path and other.parent == path.parent:
                            sibling_moved.update(removed)
                review += hunt.new_ui_texts(path, only, before_source=before_sources.get(path),
                                            moved_texts=sibling_moved or None)
    if files:
        hunt.core.prune_paths(files, hunt.raw_findings_for, "hunt.py", repo=root)
    if hunt.C.hook_scan_answer:
        transcript = payload.get("transcript_path") or payload.get("transcriptPath") or ""
        findings += hunt.scan_message(last_answer(transcript))
    before = set(shown.read_text(encoding="utf-8").splitlines()) if shown.is_file() else set()
    keys = {hashlib.sha1(f.key().encode()).hexdigest()[:16]: f for f in findings}
    # Screen-level findings first (helpers on every field, emoji, nested boxes): one line says the most.
    fresh = sorted(((k, f) for k, f in keys.items() if k not in before),
                   key=lambda kf: (kf[1].kind != "design" and "(lines " not in kf[1].problem, kf[1].path, kf[1].line))
    new = [f for _, f in fresh]
    hunt.core.note_tics(new, state, session)
    flagged = {(f.path, f.line) for f in findings}  # already under "Findings": not listed again
    texts = {hashlib.sha1(f"ui:{rel}:{text}".encode()).hexdigest()[:16]: (rel, n, text, near)
             for rel, n, text, near in review if (rel, n) not in flagged}
    new_texts = [t for k, t in texts.items() if k not in before]
    if not new and not new_texts:
        return None
    # Everything found counts as shown, also what LIMIT cuts: a cut finding must never come back in a
    # later, unrelated turn. Screen-level findings sort first, so the cut ones are single-line repeats.
    with shown.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(k for k in [*keys, *texts] if k not in before) + "\n")
    report = [f"Tautology hunt (rules: {(SKILL / 'SKILL.md').as_posix()}):"]
    if new:
        report += hunt.core.report_lines(new[:LIMIT])
        if len(new) > LIMIT:
            report.append(f"(+{len(new) - LIMIT} findings)")
    if new_texts:
        # The comparison only sees words and synonyms; the agent judges the paraphrase.
        report.append("New UI texts: check whether one repeats its neighbour in other words, explains what "
                      "the control already shows, or holds more than one idea:")
        for rel, n, text, near in new_texts[:LIMIT]:
            beside = "; ".join(f'"{t[:40]}"' for _, t in near[:4]) or "none"
            report.append(f'- {rel}:{n} [copy] new UI text "{text}" -> keep if distinct; neighbours: {beside}')
    report.append("Fix what repeats; accept what does not, silently: "
                  + hunt.core.accept_command(SKILL / "hunt.py"))
    return "\n".join(report)


def tic_guidance(core, repo):
    """Steers the fix of an AI visual tic toward the screen's own look instead of another default."""
    vocab = core.vocabulary(core.TICS, repo.resolve())
    line = ("AI visual tics: rebuild each with this screen's own tokens and components, not a new default look "
            f"(options per tic: {(SKILL / 'TICS.md').as_posix()}). No hardcoded color, size or radius in the fix.")
    return line + (f"\nThis screen already uses: {', '.join(vocab)}." if vocab else "")


def _tic_fix_report(repo, session, state, changed):
    """Once per answer: the files whose tic was just fixed, checked for hardcoded styles only."""
    marker = state / f"{session}.ticfix"
    if not changed or marker.is_file() or not (state / f"{session}.tics").is_file() or not DESIGN_AUDIT.is_file():
        return None
    marker.write_text("1", encoding="utf-8")
    return load("design_audit", DESIGN_AUDIT).stop_report(
        repo.resolve(), session, state, gate, files=changed, lean=True, style_only=True)


def _self_improve():
    return SELF_IMPROVE_FILE.read_text(encoding="utf-8").strip() if SELF_IMPROVE_FILE.is_file() else ""


def _deliver(joined, agy):
    self_improve = _self_improve()
    exception = " (exception: the local self-improve note below, only if real skill friction occurred)" if self_improve else ""
    if agy:
        joined += ("\n\nDelivery rule: Your next reply replaces the previous draft. Deliver the complete final answer "
                   f"incorporating fixes, without mentioning internal hooks or false positives{exception}.")
        if self_improve:
            joined += "\n\n" + self_improve
        print(json.dumps({"decision": "continue", "reason": joined}, ensure_ascii=False))
        return 0
    joined += f"\n\nDelivery rule: Report only concrete changes made; do not mention hooks or repeat the previous delivery{exception}."
    if self_improve:
        joined += "\n\n" + self_improve
    print(joined, file=sys.stderr)
    return 2


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):  # Windows defaults to the ANSI codepage
        stream.reconfigure(encoding="utf-8")
    payload = json.load(sys.stdin)
    agy = gate.is_antigravity(payload)
    reason = payload.get("terminationReason")
    if (gate.event_of(payload) != "Stop" or payload.get("stop_hook_active") or payload.get("continuationActive")
            or (reason and reason != "NO_TOOL_CALL")):
        if gate.event_of(payload) == "Stop" and (payload.get("stop_hook_active") or payload.get("continuationActive")):
            # The fix made after a block is not judged again, now or in a later unrelated turn, except
            # once: a tic fix that brought a hardcoded style.
            repo = gate.repo_of(payload)
            session, state = gate.session_of(payload), gate.state_dir(repo)
            changed = _changed_since_last_stop(_edited_files(state, session, repo), state, session, repo)
            report = _tic_fix_report(repo, session, state, changed)
            if report:
                return _deliver(report, agy)
        if agy:
            print("{}")
        return 0
    repo = gate.repo_of(payload)
    session, state = gate.session_of(payload), gate.state_dir(repo)
    _cleanup_stale_state(state, session)
    (state / f"{session}.ticfix").unlink(missing_ok=True)  # a new answer: its tic fix may be checked once
    files = _changed_since_last_stop(_edited_files(state, session, repo), state, session, repo)
    if not files and not _config_flag(repo, "hook_scan_answer", False):
        if agy:
            print("{}")
        return 0
    design_mode = (state / f"{session}.design").is_file()
    tests_mode = (state / f"{session}.tests_audit").is_file()
    reports = [stop_report(payload, repo, session, state, files=files, design_mode=design_mode)]
    if files:
        if MODULAR.is_file():
            reports.append(load("modular_gate", MODULAR).stop_report(repo.resolve(), session, state, gate, files=files))
        if DESIGN_AUDIT.is_file() and (design_mode or _has_ui_candidate(files)):
            reports.append(load("design_audit", DESIGN_AUDIT).stop_report(
                repo.resolve(), session, state, gate, files=files, lean=not design_mode))
        if TEST_AUDIT.is_file() and (tests_mode or _has_test_candidate(files)):
            reports.append(load("test_audit", TEST_AUDIT).stop_report(
                repo.resolve(), session, state, gate,
                only_new_tests=not tests_mode,
                files=files,
            ))
    reports = [r for r in reports if r]
    if not reports:
        if agy:
            print("{}")
        return 0
    core = sys.modules.get("core")
    if core is not None and core.TICS:
        reports.append(tic_guidance(core, repo))
    return _deliver("\n\n".join(reports), agy)


if __name__ == "__main__":
    sys.exit(main())
