---
name: tautology-debloat-design
description: Audits a named screen for visual hierarchy overload, unnecessary visual repetition, excessive padding/bloat, meaningless elements, and visual overwhelm; applies a minimalist redesign strictly using project theme tokens/variables (no hardcoded styling) while running tautology and modular hooks. Invoke with /tautology-debloat-design <screen>.
---

# Debloat design

Audit an indicated screen and, when overloaded or noisy, strip it back to a calm, minimalist layout while keeping the app's visual identity, theme variables, and functional parity. **Never hardcode styling** and **never build a new component without checking whether one already exists in the repository**.

`<skill dir>` is the directory holding this file. `<tautology dir>` is `../tautology` and `<modular dir>` is `../modular`.

## 1. Locate the screen & project rules

1. Resolve the screen named after `/tautology-debloat-design`:
   ```
   python <skill dir>/design_audit.py --find "<screen name or term>"
   ```
   Read the matched file and its sibling files in the same screen directory (`view`, `modals`, `dialogs`, `model`, child panels) plus the host/caller that opens it. List all mutually exclusive UI states (`empty`, `loading`, `error`, tabs/categories, open modals/dialogs, or repo state hooks/CLI switches when present) so the audit covers every rendered state, not only the default view. If no screen was named or matches are ambiguous, ask before editing.
2. Check project UI/parity constraints (`AGENTS.md`, `PARITY.md`, design docs, `.claude/modular/architecture.md`) so required actions, keyboard shortcuts, tooltips, and behavior are preserved rather than accidentally dropped.

## 2. Read the theme & shared components first

Before touching layout or styling:
- Read the project's theme, palette, typography/spacing/radius tokens, and CSS custom properties (`theme/`, `tokens`, `visual`, `styles/`, `tailwind.config`).
- Read the shared UI component library (`components/`, `kit/`, design-system wrappers) so every adjusted button, card, field, header, or hint reuses what already exists.

## 3. Run the structural & bloat audit

Run the static and state inventory passes on the target screen files:
```
python <skill dir>/design_audit.py --states <screen files>
python <skill dir>/design_audit.py --all <screen files>
python <tautology dir>/hunt.py --all <screen files>
python <modular dir>/patterns.py <screen files>
```
If the user supplied PNG captures (or authorized visual verification in the project), run `--check-image <shot.png>` or `--compare <before.png> <after.png>` to verify visual density and changed regions alongside the code.

Evaluate every section across 5 axes:
1. **Overloaded hierarchy:** cards inside cards, nested borders/fills, too many competing font weights or sizes.
2. **Unnecessary visual repetition:** stacked title + subtitle + explanatory card saying the same thing, repeated dividers, repeated decorative icons/chips, or inline style chains that belong in a shared component.
3. **Excessive padding & screen fill:** oversized blank spacers inside gap-spaced containers, heavy nested paddings, redundant explanatory boxes inflating height.
   **Spacing rhythm (`[spacing]` findings):** spacing must follow one clear, hierarchical scale. The audit learns the scale from the values the repository already uses and flags:
   - values off that scale (±1 px tolerance, for border compensation);
   - lopsided padding or margin, with one side 3× the other and 12 px apart;
   - a label farther from its control than the control is from the next item;
   - uneven gaps between siblings of the same kind;
   - three or more different spacer sizes in one stack;
   - a heading closer to what precedes it than to its own content;
   - an inner stack spaced looser than the stack that holds it.

   Proximity decides the asymmetric cases. More room above a heading than below it is correct, and so is a label tight to its input. Fix with the container's gap and one larger step between groups, snapped to the scale or a spacing token. Do not insert new spacers.
4. **Elements without clear purpose:** ornamental pills, labels/captions that restate what the control already shows, noisy secondary notes (remove them, or move genuinely helpful edge-case hints to the project's existing tooltip control — only if the project already uses tooltips).
5. **Visual overwhelm:** too many equal-weight groups fighting for attention at once instead of 1 clear primary flow — including **stacked/exposed filter rows** (secondary filter/sort dropdowns plus a permanent strip of status pills), **fixed-width filter/sort triggers** eating toolbar space, and **multiple competing solid primary accents** (save solid primary fill for the main CTA; keep selected filters and view toggles on a neutral/surface fill, and fold secondary filters into one `Filters` menu with active-only removable chips).

If the screen is already clean and balanced across all 5 axes, report what was checked and **change nothing**.

## 4. Apply a minimalist, theme-native design

When improvements are warranted:
- **Preserve identity, anchors & parity:** keep the project's visual language, dark/light theme support, accessibility labels, and required actions (record intentional UI simplifications in `PARITY.md` or equivalent when the repo uses one). Before deleting or flattening structural headings or wrappers, check index-coupled logic (`scroll_to_top_of_item`, `bounds_for_item`, `.nth(...)`, test assertions) so navigation and state tracking remain intact.
- **Zero hardcoded styling:** use only project theme variables (`Palette`, CSS vars `var(--*)`, typography/radius/spacing tokens) and shared UI components. Never introduce literal hex/RGB colors, magic font sizes, or magic corner radii outside theme definition files.
- **Cut before adding:** flatten wrappers, drop unnecessary borders/dividers/spacers so whitespace defines hierarchy naturally, tighten copy (`tautology`), and extract any repeated pattern into the proper shared layer (`modular`).
- **Visual verification:** if the user asks for a screenshot/capture or authorized visual verification exists in the repo (such as a project verify/capture harness covering screen states/modals without touching live user data), inspect before/after renders when layout balance or spacing cannot be confirmed from code alone. Never auto-launch GUI binaries when project rules forbid it.

## 5. Verify & hooks

1. Re-run after editing:
   ```
   python <skill dir>/design_audit.py --all <edited files>
   python <tautology dir>/hunt.py --all <edited files>
   python <modular dir>/patterns.py <edited files>
   ```
2. Run the project's build/typecheck/tests (or `verify:` from `.claude/modular/architecture.md`).
3. With the `tautology` plugin enabled, `PreToolUse` (`edit_gate.py`) and `Stop` (`stop_gate.py`, calling `hunt`, `modular_gate`, and `design_audit` automatically for this session) run as automatic guardrails on all edited lines and on your final answer. Record any deliberate exception with `--accept "<finding>" --reason "<why>"`.
