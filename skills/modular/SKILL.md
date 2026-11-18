---
name: modular
description: Finds the same code sequence copied across files (call chains and blocks) and says where to extract it, by the codebase's architecture. Runs by itself at Stop when the session creates or changes a component, or when the request is a refactor; run it also before creating a component. Needs the tautology skill next to it.
---

# Modularization

Calling the same function in many places is reuse. The defect is the same **sequence** copied, or a
new component/helper/script written when one already exists in the repository: it becomes one function
in the right layer, and each place calls it.

**First question before creating any component, modal, helper or script:** *does this already exist in
the current repository?* Search the shared layers (`components/`, `scripts/`, `bin/`, `lib/`, `utils/`)
first and reuse or extend what is already there instead of creating a local copy.

## What it finds

- **Chain:** 4+ equal calls in a row (texts aside), 4+ times in 3+ files. E.g.
  `.rounded(px(8.)).bg(theme.surface).border_1().border_color(theme.border)`.
  `.child`, `.on_click`, `.when` and the like cut the chain: their content is scanned on its own.
- **Block:** 6+ equal lines (texts and numbers aside) in 2+ places.
- **Duplicate UI helper:** a screen-local helper function (e.g. `fn form_card`, `fn hint_text`) that
  reimplements a public function in the design layer or is duplicated across 2+ screen files.
- Left out: framework idiom (`flex_1().min_w_0()...`, plus the `idioms:` list), Rust `#[cfg(test)]`
  modules, test files, and the `exclude:` folders of the map.

## Where it goes

The destination map is `.claude/modular/architecture.md` in the project. One key per line:
`layer N: path prefixes`, `target N: where to extract`, `exclude:`, `components:`, `idioms:`,
`verify:`, `source:`. The piece goes to the lowest layer every place can import (never upward): a
visual piece to the design layer (the lowest `target` from 3 up); a piece without UI down to
data/logic. A UI piece that also appears in the data layer is a **conflict**: do not extract, tell the user.

Without the map:
- if the codebase describes its architecture (ARCHITECTURE.md, a layers section in AGENTS/CLAUDE/README),
  read it and write the map citing the source;
- if not, ask with AskUserQuestion: shared layers (components/ + lib/), feature modules, or a local
  function. Write the choice to the map, then extract.

## The hook

- **UserPromptSubmit / PreInvocation** (`hooks/modular_gate.py`): a request that says
  refactor/extract/modularize/reorganize marks the session.
- **Stop** (the tautology skill's `hooks/stop_gate.py` calls `modular_gate.stop_report`): takes the edited
  files (noted by the tautology skill's `edit_gate.py`, which knows when a file is new). A new or changed
  component, or any file in a refactor, enters. A pattern touching those files sends the answer back
  once, with up to 3 patterns and the destination of each.
- The hook does not edit code: the agent extracts.

## How to extract without changing behaviour

1. `git status --porcelain -- <file>` for each place; skip files with changes that are not yours.
2. Same sequence, same order; only what varies between the places becomes a parameter.
3. Replace each place with the new call; register a new module where the language needs it.
4. Run the map's `verify:`.
5. Not a repetition to extract (coincidence)? `patterns.py --accept "<finding>" --reason "<why>"`
   (goes to `.claude/modular/accepted.txt`).

## Commands

```
python <skill>/patterns.py <files>     # patterns that touch these files
python <skill>/patterns.py --all
python <skill>/patterns.py --selftest  # cases/repo vs cases/expected.txt, and the hook with and without a map
```

## Install

Part of the `tautology` plugin, next to the `tautology` skill (it reuses its core and Stop hook); the
UserPromptSubmit hook is registered when the plugin is enabled. `<skill>` above is the base directory
shown when this skill loads.
