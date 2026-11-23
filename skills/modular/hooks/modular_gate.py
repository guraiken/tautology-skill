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
PLUGIN_ROOT = SKILL.parents[1]
SELF_IMPROVE_FILE = PLUGIN_ROOT / "SELF_IMPROVE.local.md"
EDIT_GATE = SKILL.parent / "tautology" / "hooks" / "edit_gate.py"
SKILL_INVOKE = re.compile(r"(?i)(?:^|[\s/])(?:tautology(?:-debloat-design|-audit-tests)?|modular)\b")
DEBLOAT = re.compile(r"(?i)(?:^|[\s/])(?:tautology-debloat-design|debloat[-_ ]design)(?:\s|$)|\bdebloat\b")
DESIGN_FOLLOWUP = re.compile(
    r"(?i)\b(screen\w*|tela\w*|modal\w*|dialog\w*|di[áa]logo\w*|card\w*|cart[ãa]o\w*|cart[õo]es|"
    r"button\w*|bot[ãa]o|bot[õo]es|header\w*|cabe[çc]alho\w*|layout\w*|spacing|espa[çc]\w*|"
    r"padding|margin|margem|border\w*|borda\w*|color\w*|cor|cores|theme|tema|palette|paleta|token\w*|"
    r"style\w*|estilo\w*|visual|ui|gpui|css|tailwind|minimalis\w*|enxut\w*|simplifi\w*|alinha\w*|"
    r"gap|icon\w*|[íi]cone\w*|badge\w*|divider\w*|separador\w*|hierarquia|hierarchy)\b"
)
EXIT_DESIGN = re.compile(
    r"(?i)(?:^|[\s/])(?:github-commit|ponytail|tautology-audit-tests)\b|"
    r"\b(?:git\s+(?:commit|push)|commitar|fazer\s+commit|pull\s+request|audit\w*\s+test\w*|"
    r"test(?:e|es)?\s+(?:unit[áa]rio\w*|de\s+integra[çc][ãa]o|suite)|backend|worker|daemon|updater|encerrar\s+design)\b"
)
TESTS_AUDIT = re.compile(r"(?i)(?:^|[\s/])(?:tautology-audit-tests|audit[-_ ]tests)\b|\baudit\w*\s+(?:os\s+)?test(?:e|es)\b")
TESTS_FOLLOWUP = re.compile(
    r"(?i)\b(test\w*|spec\w*|assert\w*|expect\w*|mock\w*|stub\w*|fixture\w*|parametrize|"
    r"table[-_ ]driven|clone\w*|flak\w*|sleep|skip|ignore\w*|cobertura|coverage)\b"
)
EXIT_TESTS = re.compile(
    r"(?i)(?:^|[\s/])(?:github-commit|ponytail|tautology-debloat-design)\b|"
    r"\b(?:git\s+(?:commit|push)|commitar|fazer\s+commit|pull\s+request|layout|tema|palette|css|tailwind)\b"
)
REFACTOR = re.compile(r"(?i)\b(refactor\w*|extract\w*|moduli[sz]\w*|reorgani[sz]\w*|componenti[sz]\w*|"
                      r"refator\w*|extra(ir|ia|ção|cao)\w*|modulariz\w*|reorganiz\w*|componentiz\w*)")
BUILD_ARTIFACT = re.compile(
    r"(?i)\b(create|build|add|write|implement|make|new|cri[ae]\w*|constru\w*|adicion\w*|escrev\w*|implement\w*|fa(z|ça|ca)\w*|nov[oa]s?)\b"
    r"[\s\S]{0,40}\b(component\w*|modal\w*|dialog\w*|di[áa]logo\w*|widget\w*|button\w*|bot[ãa]o\w*|card\w*|cart[ãa]o\w*|"
    r"screen\w*|tela\w*|helper\w*|script\w*|util\w*|ferramenta\w*)\b"
)
BUILD_TEST = re.compile(
    r"(?i)\b(create|build|add|write|implement|make|new|cri[ae]\w*|constru\w*|adicion\w*|escrev\w*|implement\w*|fa(z|ça|ca)\w*|nov[oa]s?)\b"
    r"[\s\S]{0,40}\b(tests?|specs?|testes?|su[íi]tes?)\b"
)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _arch_destinations(repo):
    """Lightweight read of target N: lines from architecture.md without importing patterns.py."""
    for folder in (".agents", ".claude"):
        path = Path(repo) / folder / "modular" / "architecture.md"
        if not path.is_file():
            continue
        dests = {}
        try:
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if raw.lstrip().startswith("#"):
                    continue
                m = re.match(r"^\s*target\s+(\d+)\s*:\s*(.*)$", raw, re.IGNORECASE)
                if m and m.group(2).strip():
                    dests[int(m.group(1))] = m.group(2).strip()
        except OSError:
            return []
        return [dests[k] for k in sorted(dests)]
    return []


def reuse_reminder(repo):
    dests = _arch_destinations(repo)
    where = ", ".join(dests) if dests else "components/, scripts/, bin/, lib/ or utils/"
    return (
        "Modular check (skill modular): before building a new component, modal, helper or script, ask yourself: "
        f"does this already exist in the repository? Search the shared locations ({where}) first and reuse or "
        "extend what is already there instead of creating a duplicate."
    )


SHELL_SCRIPT_EXTS = {".py", ".ps1", ".sh", ".bat", ".cmd"}
JS_SCRIPT_EXTS = {".js", ".ts", ".mjs", ".cjs"}
SCRIPT_DIRS = {"scripts", "bin"}


def is_script(rel, suffix):
    if suffix in SHELL_SCRIPT_EXTS:
        return True
    if suffix in JS_SCRIPT_EXTS:
        parts = {p.lower() for p in Path(rel).parts[:-1]}
        return bool(parts & SCRIPT_DIRS)
    return False


APPROVAL = re.compile(
    r"(?i)^\W*(pode|podemos|sim|ok|okay|beleza|manda|segue|continu\w*|aplic\w*|fa[çc]a|faz|vai|yes|go|apply|"
    r"proceed|continue|do it|lgtm)\b"
)


def _sync_mode_flag(flag_path, triggered, keep_re, exit_re, prompt):
    short_approval = len(prompt.split()) <= 6 and bool(APPROVAL.search(prompt))
    keep = bool(prompt) and flag_path.exists() and not exit_re.search(prompt) and (
        bool(keep_re.search(prompt)) or short_approval
    )
    if triggered or keep:
        flag_path.write_text("1", encoding="utf-8")
    elif flag_path.exists():
        flag_path.unlink()


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):  # Windows defaults to the ANSI codepage
        stream.reconfigure(encoding="utf-8")
    payload = json.load(sys.stdin)
    agy = "hookEventName" in payload
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    if event not in ("UserPromptSubmit", "PreInvocation"):
        if agy:
            print("{}")
        return 0
    workspace = (payload.get("workspacePaths") or [None])[0]
    repo = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or workspace or ".").resolve()
    gate = load("edit_gate", EDIT_GATE)
    state = gate.state_dir(repo)
    raw_session = payload.get("session_id") or payload.get("conversationId") or "session"
    session = "".join(c for c in str(raw_session) if c.isalnum() or c in "-_")[:80]
    flag = state / f"{session}.refactor"
    design_flag = state / f"{session}.design"
    tests_flag = state / f"{session}.tests_audit"
    prompt_stamp = state / f"{session}.prompt"
    prompt = (payload.get("prompt") or payload.get("lastUserInput") or "").strip()
    is_skill_invoke = bool(SKILL_INVOKE.search(prompt))
    is_debloat = bool(DEBLOAT.search(prompt))
    is_tests_audit = bool(TESTS_AUDIT.search(prompt))
    is_question = prompt.endswith("?") and not (is_skill_invoke or is_debloat or is_tests_audit)
    is_build_test = (not is_question) and bool(BUILD_TEST.search(prompt))
    is_refactor = ((not is_question) and bool(REFACTOR.search(prompt))) or is_debloat or is_tests_audit
    is_build = (not is_question) and bool(BUILD_ARTIFACT.search(prompt))
    _sync_mode_flag(design_flag, is_debloat, DESIGN_FOLLOWUP, EXIT_DESIGN, prompt)
    _sync_mode_flag(tests_flag, is_tests_audit or is_build_test, TESTS_FOLLOWUP, EXIT_TESTS, prompt)
    if is_refactor or is_build:  # every request passes here: fast regex check
        flag.write_text("1", encoding="utf-8")
    elif flag.exists():
        flag.unlink()
    digest = hashlib.sha1(prompt.encode("utf-8", errors="replace")).hexdigest()[:16] if prompt else ""
    already_reminded = prompt_stamp.is_file() and prompt_stamp.read_text(encoding="utf-8").strip() == digest
    if (is_refactor or is_build or is_build_test or is_skill_invoke) and digest and not already_reminded:
        prompt_stamp.write_text(digest, encoding="utf-8")
        parts = []
        if is_refactor or is_build:
            parts.append(reuse_reminder(repo))
        if is_debloat:
            parts.append("During tautology-debloat-design, use only project theme tokens/variables (no hardcoded colors, font sizes or corner radii).")
        if is_tests_audit or is_build_test:
            parts.append("During tautology-audit-tests, check results against independent expected values, guard loops against empty inputs, and consolidate cloned tests without dropping coverage.")
        if is_skill_invoke and SELF_IMPROVE_FILE.is_file():
            parts.append(SELF_IMPROVE_FILE.read_text(encoding="utf-8").strip())
        msg = " ".join(parts)
        if not msg:
            if agy:
                print("{}")
            return 0
        if agy:
            print(json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": msg}}]}, ensure_ascii=False))
        else:
            print(msg)
        return 0
    if agy:
        print("{}")
    return 0


def stop_report(repo, session, state, gate, files=None):
    """The extraction briefing that sends the answer back, or None when there is no new pattern."""
    flag, shown = state / f"{session}.refactor", state / f"{session}.modular.shown"
    edited = state / f"{session}.files"
    if files is None:
        if not edited.is_file():
            return None
        file_paths = [Path(raw) for raw in dict.fromkeys(edited.read_text(encoding="utf-8").splitlines()) if raw]
    else:
        file_paths = [Path(raw) for raw in files if raw]
    if not file_paths:
        return None
    patterns = load("patterns", SKILL / "patterns.py")
    arch = patterns.Architecture(repo)
    refactor = flag.exists()
    touched, triggers, created = {}, [], []
    for path in file_paths:
        if not path.is_file() or not path.resolve().is_relative_to(repo):
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
            if new:
                created.append(rel)
        elif new and is_script(rel, path.suffix.lower()):
            touched[rel] = changed
            triggers.append(f"new script {rel}")
            created.append(rel)
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
        entries = [k for k, _ in batch]
        handle.write("\n".join(entries) + "\n")
    trigger = "; ".join(triggers[:3]) + (f" (+{len(triggers) - 3})" if len(triggers) > 3 else "")
    return patterns.briefing([f for _, f in new], arch, trigger)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # a broken hook must never block a prompt
        print(f"modular_gate: {error}", file=sys.stderr)
        print("{}")
        sys.exit(0)
