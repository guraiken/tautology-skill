---
name: modular
description: Finds copied code sequences (call chains, blocks, duplicate UI helpers) and structurally near-duplicate parameterizable components across files, and says where to extract or unify them by the codebase's architecture; also flags imports that break module boundaries (upward layer imports, deep imports past a feature's index, module cycles). Runs by itself at Stop when the session creates or changes a component, or when the request is a refactor; run it also before creating a component. Needs the tautology skill next to it.
---

# Modularization

Calling the same function in many places is reuse. The defect is the same **sequence** copied, a
**near-duplicate component** differing only by 1-3 callbacks/props/slots (e.g., two `SearchInput`
variants or two `Modal` dialogs sharing the same chrome/hooks), or a new component/helper/script
written when an equivalent one already exists in the repository.

## Pre-Creation Sibling Scan

**First question before creating any component, modal, input, card, toolbar, helper, or script:**
*does an equivalent or parameterizable one already exist in the current repository?*

Before writing a new file (`*Input`, `*Modal`, `*Dialog`, `*Card`, `*Form`, `*Toolbar`, `*Panel`,
`*Drawer`, `*Filter`, `*Table`, `use*`):
1. Search the shared layers (`components/`, `ui/`, `scripts/`, `bin/`, `lib/`, `utils/`) by role stem.
2. Decide using the **Unification Decision Matrix** below: reuse as-is, extend with <=3 optional
   props/slots, extract a shared shell/hook, or justify why it must stay separate.

## What it finds

- **Chain:** 4+ equal calls in a row (strings aside; supports Rust turbofish, TS generics, `?.`, and
  intervening comments), 4+ times in 3+ files. E.g.
  `.rounded(px(8.)).bg(theme.surface).border_1().border_color(theme.border)`.
  `.child`, `.on_click`, `.when` and the like cut the chain: their content is scanned on its own.
- **Block:** 6+ equal lines (strings and numbers aside; Tailwind `className`/`class` preserved to avoid
  false matches between unrelated markup) in 2+ places.
- **Duplicate UI helper:** a screen-local UI helper function (Rust multi-line/generic/`impl IntoElement`,
  TSX/JSX, Python Qt/Tk) that reimplements an exported UI helper or is duplicated across 2+ screen files.
- **Near-duplicate component:** two UI components or hooks (`>=15` non-trivial lines) sharing >=72%
  structural similarity (small units of 8-14 lines need >=88% plus shared hooks/events, shell chrome
  or >=3 shared classes, so icon wrappers never match on shape alone) across normalized token shingles (local vars -> `ID`, callbacks -> `CB`),
  preserved style/chrome class tokens (`>=45%` when both use CSS classes), UI element skeleton, and
  hook/event behavior — with a parameterization summary of what is shared vs what varies. Components
  that already wrap the same shared shell (e.g., `<ModalShell>`) are discounted to avoid false nags.
- **Boundary** (`boundaries.py`, JS/TS/Vue/Svelte and Python imports; Rust is left to the compiler):
  - *layer*: a file imports a higher `layer N` of the map, or breaks a `forbid: <from> -> <to>` line.
    Without a map, only shared code (`lib/`, `utils/`, `shared/`, `core/`, `domain/`, `components/ui/`)
    importing feature/page code (`features/`, `pages/`, `screens/`, `views/`, `routes/`, `app/`).
  - *deep import*: code outside a feature imports a feature's inner file although the feature has an
    `index` (its public API).
  - *module cycle*: feature folders (or top folders under `src/`, `app/`, `lib/`) importing each other.
    Type-only, in-function and dynamic imports do not count; imports inside one folder are left out.
  - Fix the dependency direction (move the piece down, import through the index, pass a callback):
    never extract to "solve" a boundary finding. In the hook, only imports on the lines this session
    changed are reported, for every edited code file (not only components).
- Near-duplicates that span **two different features** carry a note: unify only if they change for the
  same reason; otherwise keep them apart and `--accept`.
- Left out: framework idioms (`flex_1().min_w_0()...`, plus the `idioms:` list), Rust `#[cfg(test)]`
  modules, TS `type`/`interface` declarations, test files, and the `exclude:` folders of the map.

## Unification Decision Matrix (Anti-Premature-Abstraction Guardrails)

| Situation | Action | Rule |
|---|---|---|
| Same UI role, shared chrome/hooks, differences fit in **1-3 cohesive props/callbacks** (e.g. `UserSearchInput` vs `OrderSearchInput` differing in `onSubmit?` / `onClear?`) | **Unify into one component** | Add optional callbacks/props (`onEnter?`, `onClear?`, `variant?`). Never copy-paste the whole input. |
| Shared outer chrome or interaction state around distinct domain bodies (e.g. `DeleteModal` vs `RenameModal` sharing backdrop + Escape + header/footer) | **Extract shared shell or hook (composition)** | Build `<ModalShell title footer onClose>{children}</ModalShell>` and/or `useEscapeKey` / `useDebouncedValue`. **Never** build a mega-component with >3 boolean mode flags (`isAdmin`, `hideFooter`, `showExtraRow`). |
| Coincidental visual shape across unrelated domains with independent lifecycles (e.g. `PricingCard` vs `ProfileCard`) | **Keep separate (`--accept`)** | Do not couple unrelated domains just because both use bordered boxes. Accept the finding with a short reason. |

## Where it goes

The destination map is `.claude/modular/architecture.md` (or `.agents/modular/architecture.md`) in the
project. One key per line:
`layer N: path prefixes`, `target N: where to extract`, `ui_layer: N` (optional explicit UI target layer),
`exclude:`, `components:`, `idioms:`, `verify:`, `source:`, `features:` (feature roots, e.g. `src/features/`;
default: any `features/`, `modules/` or `domains/` folder), `forbid: <from prefixes> -> <to prefixes>`.
Lower layer numbers are more basic: layer N may import layers <= N only.

- Visual pieces go to `ui_layer` (if specified), else the lowest `target N` whose path or layer overlaps
  `components:` / `components/` / `ui/` / `widgets/` (falling back to lowest `target >= 3`).
- Non-UI pieces go to the highest non-UI `target` at or below the lowest layer that uses them (never upward).
- A UI piece that also appears below the UI layer is a **conflict**: do not extract, tell the user.

Without the map:
- if the codebase describes its architecture (`ARCHITECTURE.md`, or a layers section in `AGENTS`/`CLAUDE`/`README`),
  follow that structure (and write `.claude/modular/architecture.md` citing `source:` when helpful);
- otherwise follow the existing folder structure (`components/` + `lib/` or `utils/`, feature `shared/`,
  or a local function inside the same file) — or run `python <skill>/boundaries.py --suggest-map` to
  draft `.claude/modular/architecture.md` from the current import graph.

## The hook

- **UserPromptSubmit / PreInvocation** (`hooks/modular_gate.py`): marks `.refactor` (persisted across
  short approvals like `"yes, go ahead"` / `"pode aplicar"`) and injects the pre-creation reuse check
  on refactor or component-creation requests (including polite requests ending with `?`, excluding pure
  informational questions like `"How does ... work?"`).
- **Stop** (the tautology skill's `hooks/stop_gate.py` calls `modular_gate.stop_report`): inspects edited
  and newly created files (grouping compatible extensions like `.ts`/`.tsx`/`.js`/`.jsx`), reporting up
  to 3 new patterns or smart reuse matches (when a new component structurally matches >=65% or duplicates
  raw shell chrome of an existing shared component).
- The hook does not edit code: the agent extracts or unifies.

## How to extract without changing behaviour

1. `git status --porcelain -- <file>` for each place; skip files with changes that are not yours.
2. Keep the same behaviour and order; parameterize only what varies (<=3 optional props/slots; prefer
   composition over boolean mode flags).
3. Replace each place with the unified call or composed shell; register a new module where needed.
4. Run the map's `verify:` command (or build/tests).
5. Not a repetition to extract (coincidental shape)? Accept it silently into `.claude/modular/accepted.txt`:
   - POSIX: `python <skill>/patterns.py --accept "<finding>" --reason "<why>"`
   - Windows (PowerShell — pipe via stdin so inner double quotes are preserved):
     `'<finding>' | py -3 <skill>/patterns.py --accept - --reason "<why>"`

## Commands

```
python <skill>/patterns.py <files_or_dirs>   # patterns that touch these files or folders
python <skill>/patterns.py --all
python <skill>/patterns.py --selftest        # cases/repo vs cases/expected.txt, hooks, Wave 1/2 and boundary tests
python <skill>/boundaries.py --all           # only the module-boundary findings (also part of patterns.py)
python <skill>/boundaries.py --suggest-map   # draft .claude/modular/architecture.md from the current import graph
```

## Install

Part of the `tautology` plugin, next to the `tautology` skill (it reuses its core and Stop hook); the
UserPromptSubmit / PreInvocation hook is registered when the plugin is enabled. `<skill>` above is the
base directory shown when this skill loads.
