# Vision Audit Protocol

Read when screenshots exist or are captured during `/tautology-debloat-design`. Do not eyeball a screen and write "looks busy" — run objective telemetry first, then execute all 4 passes and cite the exact region and UI element for every defect.

## 0. Run Telemetry First

```
python <skill dir>/design_audit.py --check-image <shot>
python <skill dir>/design_audit.py --compare <before> <after>
```

Use the telemetry (`edge density`, `spatial std`, `empty tiles`, `accent islands`, and `before -> after` size/region diff) as anchors — they are non-blocking diagnostics, not pass/fail verdicts:
- High `edge density` -> suspect nested borders, card cages, dividers, or text/badge noise.
- High `spatial std` + many `empty tiles` -> suspect dead whitespace voids next to cramped clusters.
- Multiple `accent islands` -> suspect competing primary CTAs, rainbow pills, or neon AI-slop.

## Pass 1 — Objective Visual Defects

Scan top-left to bottom-right across the full viewport and every state from `--states`:
- **Clipping & truncation:** labels, button text, table headers, or numbers cut off mid-word or ellipsized (`...`) where space exists or wrapping/shortening fits.
- **Overlap & collision:** text colliding with icons, badges overlapping titles, sticky bars covering content, modals clipped at viewport edges.
- **Alignment & baselines:** icons off-center against adjacent text, uneven left edges across stacked sections, form labels/inputs misaligned.
- **Cramped hit targets:** clickable controls below `32px` height on desktop or `44px` on touch, or adjacent buttons crowded with `< 6px` separation.
- **Contrast failures:** muted body copy below ~4.5:1 against its surface, or borders/icons/placeholders effectively invisible (~< 3:1) in dark or light mode.
- **Orphan wrapping & dead voids:** headings or buttons wrapping a single orphan word onto line 2, or large empty zones next to compressed columns.

## Pass 2 — Indirect Visual Pollution & Focal Hierarchy

- **Nested surfaces:** boxes inside boxes (`> 2` levels of background/border/shadow nesting) — flatten inner containers to whitespace and typography.
- **Badge / pill soup:** decorative status chips, category tags, or version pills competing with real data — keep only chips the user acts on.
- **Icon-before-every-label:** repetitive leading icons on every heading, row, or menu item where the text already names it.
- **Divider spam:** horizontal rules separating items that spacing already groups.
- **Chrome-to-content ratio:** toolbars, banners, headers, and borders eating more area than the actual work surface.
- **1-second CTA test:** can you spot the single primary action immediately? Demote competing solid accents (active filters, view toggles, secondary buttons) to neutral/surface tokens.

## Pass 3 — AI-Slop & "Vícios de IA"

Check explicitly for generated-UI clichés:
- **Decorative gradients, glows & glassmorphism:** purple/indigo/cyan hero gradients, `backdrop-blur` cards over flat surfaces, heavy drop shadows, or gradient-filled text (`bg-clip-text`) outside a real brand surface.
- **Gratuitous KPI stat strips:** 3–4 uniform metric cards bolted above the real workflow when the user did not ask for a dashboard.
- **Icon-in-tinted-circle headers:** every card or section titled with a `rounded-full` tinted icon badge beside a subtitle.
- **Redundant prose & empty-state essays:** multi-sentence helper banners, subtitles restating the title, or verbose empty states where one short line + action suffices.
- **Fake live status dots:** pulsing or static colored dots (`● Active`, `animate-pulse`) beside labels — color only the status word with a semantic token.
- **Uniform card grids over simpler lists/tables:** equal-height SaaS feature cards used for dense records that scan faster as a compact list or table.

## Pass 4 — Intra-Screen & Cross-State Consistency

Compare sibling elements on the same screen and across states (`empty`, `loading`, `error`, tabs, modals):
- **Padding & gaps:** sibling cards, rows, and form groups share one spacing rhythm and consistent inner padding.
- **Corner radii & borders:** controls share one radius, containers share one radius, border weights/colors stay consistent (no mixing 4+ radii or arbitrary borders).
- **Elevation & shadows:** same-level surfaces use the same elevation token.
- **Typography scale:** clear 3–4 step hierarchy (page title -> section heading -> body -> muted metadata), no ad-hoc intermediate sizes.
- **Control heights:** sibling buttons, inputs, and filter triggers align to one height per toolbar/row.

## Reporting Format

For each finding, state:
1. **Pass & category** (e.g., `Pass 1 · Clipping`, `Pass 3 · AI-slop`)
2. **Exact region & element** (e.g., `top-right toolbar -> "Sort by" trigger`)
3. **Minimal theme-native fix** (what to remove, flatten, or snap to an existing token/component)

After editing, re-capture when authorized and run `--compare <before> <after>` to confirm the change reduced visual noise without introducing regressions.
