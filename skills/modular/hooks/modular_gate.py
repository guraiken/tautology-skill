"""Hook of the modular skill: when a component is created or changed, or in a refactor, looks for
copied sequences and structurally near-duplicate components, and tells the agent what to extract or unify.

UserPromptSubmit / PreInvocation: marks session mode flags (.refactor, .design, .tests_audit) and
injects a pre-creation reuse reminder when building/refactoring UI components or helpers.
Stop (called by the tautology skill's stop_gate.py): inspects edited/created files for copied chains,
blocks, duplicate UI helpers, near-duplicate parameterizable components, and smart reuse matches.
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
DEBLOAT = re.compile(
    r"(?i)(?:^|[\s/])(?:tautology-debloat-design|debloat[-_ ]design)(?:\s|$)|\b(?:debloat|de-?clutter)\b|"
    r"\b(?:visual\s+(?:clutter|pollution|noise|audit|slop|inconsistenc\w*)|"
    r"polui[çc][ãa]o\s+visual|ai[- ]?slop|v[íi]cios?\s+de\s+ia|"
    r"(?:limp\w*|enxug\w*)\s+(?:a\s+)?(?:ui|interface|tela|visual|layout))\b"
)
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
REFACTOR = re.compile(
    r"(?i)\b(refactor\w*|extract\w*|moduli[sz]\w*|reorgani[sz]\w*|componenti[sz]\w*|dedup\w*|unif(?:y|ic\w*)|"
    r"refator\w*|extra(ir|ia|ção|cao)\w*|modulariz\w*|reorganiz\w*|componentiz\w*)"
)
REFACTOR_FOLLOWUP = re.compile(
    r"(?i)\b(refactor\w*|extract\w*|moduli[sz]\w*|dedup\w*|consolidat\w*|unif\w*|merge\w*|component\w*|"
    r"shell|prop\w*|hook\w*|shared|helper\w*|refator\w*|extra\w*|modulariz\w*|reaproveit\w*|reutiliz\w*)\b"
)
EXIT_REFACTOR = re.compile(
    r"(?i)(?:^|[\s/])(?:github-commit|ponytail|tautology-audit-tests)\b|"
    r"\b(?:git\s+(?:commit|push)|commitar|fazer\s+commit|pull\s+request)\b"
)
BUILD_ARTIFACT = re.compile(
    r"(?i)\b(create|build|add|write|implement|make|new|cri[ae]\w*|constru\w*|adicion\w*|escrev\w*|implement\w*|fa(z|ça|ca)\w*|nov[oa]s?)\b"
    r"[\s\S]{0,60}(?:"
    r"\b(?:component\w*|modal\w*|dialog\w*|di[áa]logo\w*|widget\w*|button\w*|bot[ãa]o\w*|card\w*|cart[ãa]o\w*|cart[õo]es|"
    r"screen\w*|tela\w*|helper\w*|script\w*|util\w*|ferramenta\w*|input\w*|search\w*|form\w*|toolbar\w*|"
    r"table\w*|list\w*|panel\w*|drawer\w*|dropdown\w*|select\w*|filter\w*|header\w*|sidebar\w*|tabs?|"
    r"menu\w*|popover\w*|hook\w*|field\w*|page\w*|view\w*|campo\w*|busca\w*|formul[áa]rio\w*|barra\w*|"
    r"painel\w*|pain[ée]is|aba\w*|filtro\w*|tabela\w*|lista\w*|p[áa]gina\w*)\b"
    r"|\w*(?:input|modal|dialog|card|toolbar|panel|drawer|dropdown|popover|sidebar|filter|form)\b)"
)
BUILD_TEST = re.compile(
    r"(?i)\b(create|build|add|write|implement|make|new|cri[ae]\w*|constru\w*|adicion\w*|escrev\w*|implement\w*|fa(z|ça|ca)\w*|nov[oa]s?)\b"
    r"[\s\S]{0,40}\b(tests?|specs?|testes?|su[íi]tes?)\b"
)
INFO_QUESTION = re.compile(
    r"(?i)^\s*(?:what|why|how|where|when|which|who|does|do|is|are|explain|describe|"
    r"o\s+que|por\s*que|como|onde|quando|qual|quais|quem|expli\w+|descrev\w+)\b"
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
        "Modular check (skill modular): before building a new component, modal, input, helper or script, "
        f"search existing shared locations ({where}) by role stem (*Input, *Modal, *Card, *Toolbar) first; "
        "reuse, extend via <=3 optional props/slots, or extract a shared shell instead of creating a near-duplicate."
    )


SHELL_SCRIPT_EXTS = {".py", ".ps1", ".sh", ".bat", ".cmd"}
JS_SCRIPT_EXTS = {".js", ".ts", ".mjs", ".cjs"}
SCRIPT_DIRS = {"scripts", "bin"}
JS_COMPAT_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
WEB_COMPAT_SUFFIXES = JS_COMPAT_SUFFIXES | {".vue", ".svelte"}


def is_script(rel, suffix):
    if suffix in SHELL_SCRIPT_EXTS:
        return True
    if suffix in JS_SCRIPT_EXTS:
        parts = {p.lower() for p in Path(rel).parts[:-1]}
        return bool(parts & SCRIPT_DIRS)
    return False


HOOK_DIRS = {"hooks", "composables"}
HOOK_EXPORT_RE = re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\s+|const\s+)use[A-Z]\w*", re.M)


def is_hook_file(rel, path):
    """A React hook / Vue composable module: near-duplicate hooks are as worth unifying as components."""
    if path.suffix.lower() not in (".ts", ".js", ".tsx", ".jsx", ".mjs"):
        return False
    if {p.lower() for p in Path(rel).parts[:-1]} & HOOK_DIRS:
        return True
    try:
        return bool(HOOK_EXPORT_RE.search(path.read_text(encoding="utf-8", errors="replace")[:200_000]))
    except OSError:
        return False


def _expand_suffixes(touched_suffixes):
    out = set(touched_suffixes)
    if out & {".vue", ".svelte"}:
        out |= WEB_COMPAT_SUFFIXES
    elif out & JS_COMPAT_SUFFIXES:
        out |= JS_COMPAT_SUFFIXES
    return out


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
    is_info_question = (
        prompt.endswith("?")
        and bool(INFO_QUESTION.search(prompt))
        and not (is_skill_invoke or is_debloat or is_tests_audit)
    )
    is_build_test = (not is_info_question) and bool(BUILD_TEST.search(prompt))
    is_refactor = ((not is_info_question) and bool(REFACTOR.search(prompt))) or is_debloat
    is_build = (not is_info_question) and bool(BUILD_ARTIFACT.search(prompt))
    _sync_mode_flag(design_flag, is_debloat, DESIGN_FOLLOWUP, EXIT_DESIGN, prompt)
    _sync_mode_flag(tests_flag, is_tests_audit or is_build_test, TESTS_FOLLOWUP, EXIT_TESTS, prompt)
    _sync_mode_flag(flag, is_refactor or is_build, REFACTOR_FOLLOWUP, EXIT_REFACTOR, prompt)
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


def _shown_key(finding):
    content = getattr(finding, "code", "") or ""
    raw = f"{finding.path}:{finding.rule}:{finding.subject}:{content}"
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


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
    edited_code = {}  # every edited code file: its changed import lines are checked against the boundaries
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
        edited_code[rel] = changed
        if arch.is_component(rel):
            touched[rel] = changed
            triggers.append(f"new component {rel}" if new else f"changed component {rel}")
            if new:
                created.append(rel)
        elif is_hook_file(rel, path):
            touched[rel] = changed
            triggers.append(f"new hook {rel}" if new else f"changed hook {rel}")
            if new:
                created.append(rel)
        elif new and is_script(rel, path.suffix.lower()):
            touched[rel] = changed
            triggers.append(f"new script {rel}")
            created.append(rel)
        elif refactor:
            touched[rel] = changed
            triggers.append(f"refactor in {rel}")
    if not edited_code:
        return None
    os.chdir(repo)
    if touched:
        suffixes = _expand_suffixes({Path(rel).suffix.lower() for rel in touched})
        found, reuse_candidates = patterns.findings_and_reuse(repo, arch, touched, suffixes, created=created,
                                                              boundary_touched=edited_code)
    else:
        found, reuse_candidates = patterns.boundary_findings(repo, arch, edited_code), []
        triggers.append("imports changed in " + ", ".join(list(edited_code)[:3]))
    combined = found + reuse_candidates
    before = set(shown.read_text(encoding="utf-8").splitlines()) if shown.is_file() else set()
    keyed = [(_shown_key(f), f) for f in combined]
    new_items = [(k, f) for k, f in keyed if k not in before]
    if not new_items:
        return None
    batch = new_items[:patterns.PER_ROUND]
    with shown.open("a", encoding="utf-8") as handle:
        entries = [k for k, _ in batch]
        handle.write("\n".join(entries) + "\n")
    trigger = "; ".join(triggers[:3]) + (f" (+{len(triggers) - 3})" if len(triggers) > 3 else "")
    return patterns.briefing([f for _, f in new_items], arch, trigger, created=created)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # a broken hook must never block a prompt
        print(f"modular_gate: {error}", file=sys.stderr)
        print("{}")
        sys.exit(0)
