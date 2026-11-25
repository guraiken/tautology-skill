# Vision Audit Protocol

Read when screenshots exist or are captured during `/tautology-debloat-design`. Do not eyeball a screen and write "looks busy" — run objective telemetry first, then execute all 4 passes and cite the exact region and UI element for every defect.

## 0. Run Telemetry First

```
python <skill dir>/design_audit.py --check-image <shot> [--dpr 2] [--viewport-height 900]
python <skill dir>/design_audit.py --compare <before> <after> [--dpr 2]
```

Use the telemetry as anchors — non-blocking diagnostics, not pass/fail verdicts. `FAIL ... no UI structure` means the capture is blank, a bare gradient or flat colour bands: recapture before auditing. Every `WARN` line (skeleton/loading capture, JPEG, transparent background, DPR mismatch) limits what the numbers mean. Pass `--dpr 2` on Retina/2x screenshots so pixel-size thresholds (hit targets, drift, nesting) scale to CSS pixels.

Metrics are computed on a box-filtered copy about 480 px wide, with perceptual (CIE L*) thresholds, so light and dark themes read alike:
- `edge density` / `spatial std` / `empty tiles`: clutter and its balance. `busiest tiles` give pixel boxes to cite. On long full-page captures (`height > 1.5x` nominal viewport height, or `--viewport-height`), `viewport bands` report per-fold telemetry and fold-scoped busiest tiles.
- `accent islands (N hues)`: solid chromatic blobs, split by 30° hue bucket; `accent blobs` list their boxes. Subpixel text fringes no longer count.
- `solid controls (max M in one row)`: flat filled shapes sized like buttons (`26–72` CSS px tall), of any colour including neutral black/white primaries. `M >= 2` in one row suggests competing filled CTAs.
- `gradient/halo %`: smooth luminance or isoluminant hue ramps away from any edge (gradient heroes, glows, soft shadows).
- `low-contrast edges % (<3:1)`: WCAG ratio measured on full-resolution pixels around each edge. Hairline borders count too, so dividers and nested cards raise it. Faded text shows as a high value with a *low* edge density.
- `surface nesting depth`: box-inside-box container nesting (`>= 80x56` CSS px, both bordered and borderless tonal surfaces). Depth `> 2` prints the `deepest nested surface` box.
- `left rails` / `left-edge drift`: stacked vertically disjoint blocks drifting `3–14` CSS px off a shared left rail (deliberate `>= 16px` indentation starts a new rail; centered stacks are excluded).
- `hit targets`: exact full-resolution check for solid controls `< 32` CSS px tall (`small targets`) and adjacent same-row targets separated by `< 6` CSS px (`crowded targets`). Solid pill badges `< 32px` also count here — Pass 1 verifies whether they are clickable controls or decorative chips.

Calibration (reproducible via `python <skill dir>/visual_fixtures.py --calibrate`; synthetic 1440x900 light + dark set; noise floor: ignore |Δ edge| < 0.5 pp and |Δ %| < 3 pp; deltas are valid only for the same viewport, DPR, theme and PNG):

| capture | edge % | isl/hues | ctrl row | grad % | <3:1 % | fg % | nest | drift | small/crowd |
|---|---|---|---|---|---|---|---|---|---|
| clean | 9.8 | 0/0 | 1 | 0.0 | 6.9 | 7.7 | 0 | 0 | 0/0 |
| competing_ctas_hued | 10.2 | 3/3 | 3 | 0.0 | 6.6 | 8.6 | 0 | 0 | 0/0 |
| competing_ctas_neutral | 10.2 | 0/0 | 3 | 0.0 | 6.6 | 8.6 | 0 | 0 | 0/0 |
| outline_badge_soup | 10.9 | 0/0 | 1 | 0.0 | 6.1 | 8.4 | 0 | 0 | 0/0 |
| solid_badge_soup | 10.9 | 7/3 | 1 | 0.0 | 6.1–8.6 | 9.2 | 0 | 0 | 7/0 |
| gradient_hero | 9.8 | 2–3/2–3 | 1 | 16.7–17.5 | 8.1–18.5 | 31.9–32.0 | 1 | 0 | 0/0 |
| kpi_stat_strip | 12.5 | 0/0 | 1 | 0.3 | 15.0 | 19.3 | 1 | 0 | 0/0 |
| ai_slop_dashboard | 16.8–17.6 | 14/6 | 3 | 7.4 | 50.3–55.9 | 44.8–44.9 | 3 | 0 | 10/0 |
| divider_spam_nested_cards | 19.4 | 0/0 | 1 | 0.0 | 61.4 | 11.8 | 3 | 0 | 0/0 |
| tonal_nesting | 3.9–4.3 | 0/0 | 1 | 7.5–10.4 | 57.2–60.7 | 51.5–52.0 | 3 | 0 | 0/0 |
| faded_low_contrast_text | 7.7–8.6 | 0/0 | 1 | 0.0 | 95.5–96.0 | 6.8 | 0 | 0 | 0/0 |
| dead_void | 0.8 | 0/0 | 1 | 0.0 | 0.0 | 0.9 | 0 | 0 | 0/0 |
| cramped_hit_targets | 10.1 | 2/1 | 2 | 0.0 | 6.6 | 8.4 | 0 | 0 | 2/2 |
| misaligned_left_edges | 7.7 | 0/0 | 1 | 0.0 | 8.7 | 6.3 | 0 | 6 (10px) | 0/0 |
| fullpage_long (1440x2700) | 8.8 | 5–6/3–4 | 3 | 11.9–15.0 | 23.5–28.5 | 27.9–28.0 | 1 | 0 | 0/0 |

Edge, islands, controls, fg, nesting, drift and hit-target counts agree tightly between light and dark themes. `<3:1 %` and `gradient %` still vary by theme palette, so compare them only within one theme.

Clipping, text/icon overlap and icon-to-text baseline misalignment leave the numbers almost unchanged: only Pass 1 below finds them. In `--compare`, read the `WARN` lines first: "edge density fell but low-contrast edges rose" means the screen looks calmer because the text faded, not because the clutter went; nesting, hit-target and left-edge regressions warn automatically too. When removing or inserting a section shifts lower content vertically, `--compare` aligns horizontal reflow bands (`reflow bands: y=... dy=...`) so unchanged shifted sections are not reported as changed regions. Captures differing by an integer DPR are normalised (`normalised 2x -> 1x`).

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
