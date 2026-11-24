---
name: tautology
description: Hunts tautology in UI copy, tests, code, scripts and the agent's own answer - text that repeats its neighbour, tests that confirm themselves, comments that restate the code, oversized UI text. Runs by itself before each answer (Stop hook); run it also when reviewing a change. Tautology here is a defect, like a layout bug.
---

# Tautology hunt

Tautology is saying again what is already said, or checking what is already true by construction.
Here it is **a defect in any form**: UI text, test, component, script or the agent's answer. Found it, fix it.

## When it runs

- **By itself, before the answer:** `hooks/edit_gate.py` (PreToolUse) keeps a copy of each file before the
  session's first edit; at the end of the answer `hooks/stop_gate.py` scans only the lines the session
  changed (exiting immediately when no files were edited). By default, the automatic Stop hook reports
  objective tautologies (repeats, pleonasm, doubled words, self-confirming tests, comments restating
  code) and, when a UI file changed, the AI visual tics: a helper text under (almost) every field,
  placeholder or subtitle that only wraps the label or title in filler ("Enter your…", "Manage your…
  below"), decorative emoji, "Tip:"/"Note:" lines (a warning such as "cannot be undone" stays), a
  tooltip or info icon on (almost) every toggle and field (icon-only buttons, links and shortcuts keep
  theirs), decorative badges (hype word, emoji, status dot, gradient, pulse), footnotes (small muted
  note after a component's or page's content; a site `<footer>`, legal text and state messages such
  as empty or loading stay), cards nested in cards (JSX; other toolkits only past
  `max_container_depth`) and too many dividers. Each
  screen-level tic is one line, and only when the session's own lines add to it, so an edit elsewhere
  on a cluttered screen does not block. When a tic is reported, the hook points to `TICS.md` (what to
  use instead of each tic) and lists the tokens and components the screen already uses. The fix must
  be built from those, not from a new default look. After the fix, the changed files get one style
  check: a hardcoded color, size or radius, or a stock Tailwind color in a themed project, sends the
  answer back once more. Chat-answer scanning, length/wordiness/density and per-string
  `New UI texts` lists stay off unless enabled via `/tautology-debloat-design` or project settings
  (`hook_scan_answer`, `hook_bloat`, `hook_review_new_ui_texts`). A new finding sends the answer back
  once; a finding already shown never blocks again.
- **By hand:** `python <skill dir>/hunt.py --diff` (`--all <file>` looks at the whole file; `--message <file>`
  looks at a text).
- **By competency:** `/tautology:tautology copy` (or `code`, `tests`, `answer`, several with commas) runs
  `hunt.py --diff --only <competencies>` (finding kinds `[copy]`, `[code]`, `[test]`, `[answer]`).
  Without an argument it runs everything. `<skill dir>` is the base directory shown when this skill loads.

`hunt.py` points at candidates; you decide, with the rules below. What it already understands:

- **Programming languages:** Python, Rust, JS/TS and JSX/TSX. In JSX, text between tags counts as screen
  text and `title=` as a tooltip; CSS classes, selectors and CSS in strings stay out. Jest/Vitest/node:test
  tests (`*.test.ts`, `__tests__/`) follow the test rules.
- **Human languages:** English and Portuguese, both on by default. Narrow them in the project settings
  (`"languages": ["en"]`). Synonyms cross languages: "Search" = "Find" = "Buscar" = `search`;
  the comment "close the window" matches `window.close()`.
- **UI:** a file that imports Qt widgets, Tk, wx, Kivy, GPUI, egui, iced or Slint counts as a screen, as
  does JSX/TSX and DOM-building JS; `ui_paths` adds folders. What the user does not see stays out: logs,
  `expect`, `panic!`, screen-reader labels, texts used in comparisons, different `match`/`if` arms,
  table rows.
- **Another explanation is not a repeat:** a longer text containing the shorter one only fails when the
  addition is filler. Negation, condition ("if", "when", "after"), number, quoted or proper name count
  as new information.
- **Siblings and sinks:** the same function called on neighbouring lines (two `group(...)`) are different
  fields; the same text written to two models is one state.
- **Paraphrase:** the hook lists each new UI text with its neighbours, and you check whether one repeats
  the other in different words. That part is yours: the comparison only sees words and synonyms.
- **Tests:** also catches an expected value that is the same call as the result under another name
  (`r = f(10)` ... `assert r == f(10)`).
- **Answer:** catches the same idea said in different paragraphs, even in other words.

Still yours: an expected value recomputed by another path through the same code, and a synonym not in
the table yet (add it to `synonyms` in the settings).

## What is tautology

**Copy (UI text, messages, docs):**
- Text that repeats its neighbour: tooltip equal to the label, subtitle that rewrites the title, hint that
  repeats the field name, empty state that repeats the screen title.
- The same idea twice in one text, or in the texts stacked on one surface.
- Pleonasm ("return back", "past history") and doubled words. <!-- tautology: example -->
- Long text: hint, subtitle or tooltip over ~90 characters; description over two short sentences. Text
  the control already explains goes entirely.
- The same sentence in different dialogs, kept on purpose for consistency, does **not** count.

**Visual bloat (too much text on screen):**
- Explaining the obvious (cut it): "click here to", "this page shows". <!-- tautology: example -->
- Wordy (rewrite as an instruction): "you can", "allows you to". The information stays, the detour goes:
  "Try again or go back". <!-- tautology: example -->
- Text over its kind's limit (`limits` in the settings; defaults):

  | Kind | Words | Characters | Sentences |
  |---|---|---|---|
  | tooltip | — | 2 balloon lines (~92) | 1 |
  | button, menu item | 4 | 28 | 1 |
  | title, label, section, chip | 6 | 48 | 1, no final period |
  | hint, description, placeholder | 16 | 90 | 2 |
  | notice, status, error | 22 | 140 | 2 |
  | loose paragraph | 26 | 160 | 2 |

- Massive tooltip: measured whole, even from a constant, `let`, `format!` or a literal split over lines,
  in balloon lines of `tooltip_line_chars` (46). Over 2 lines or one sentence: **shorten to the minimum**,
  a single-line sentence with what the control does not show.
- A confirmation of a destructive action may take 3 sentences: question, consequence, warning. Payment or
  legal copy in `sensitive_paths` gets no length finding: it is listed with the new texts, to shorten
  without losing the guarantee.
- The same text in several places of a file is code organisation (`[code]`, becomes a constant) and
  shows only with `--all`, not in the hook.
- Density: more than `density` (5) explanatory texts (8+ words) in one screen function. List the
  elements that clutter the area, then **eliminate** them (or shorten to a label), or **move them to
  a tooltip** if the project already uses tooltips.
- The kind comes from the call that receives the text (`button(`, `.tooltip(`, `placeholder`, `<h2>`...);
  project components are added in `kinds`. When unsure it assumes a paragraph, the loosest limit.

**Tests:**
- Compares an expression with itself (`assert f() == f()`, `assert_eq!(a, a)`).
- Checks the value the test itself wrote (`x = 5` ... `assert x == 5`), or the return value it set on a mock.
- Recomputes the expected value with the code under test: the expected value comes from outside (a
  literal, a fixture, a known case).
- Checks nothing: no assert, or `assert True`.

**Code and scripts:**
- Before creating any new component, helper or script, ask yourself whether an equivalent one already
  exists in the repository; search and reuse or extend it instead of writing a second copy.
- A function that only forwards the same arguments to another: call the original.
- A comment or docstring that says what the name or the line already says. A good comment says **why**.
- A stuttering name (`config_config`).
- A copied line or block; the same literal message scattered instead of one constant.

**The agent's answer:**
- The same sentence or passage twice, a closing summary that retells the body, "as said above". Say each
  thing once, where it is read first.
- After a hook finding, never print internal plugin reasoning, hook details, or false-positive
  justifications in the chat. In Antigravity (where the previous draft is collapsed), deliver the complete
  final answer incorporating any fixes; in Claude Code (where the previous message stays visible), report
  only concrete changes made.

## How to fix

1. Cut first, rewrite second: text that only repeats goes entirely.
2. High-bloat area: list every element cluttering the surface, eliminate what is redundant, or move
   secondary explanations into a tooltip (only if the project already uses tooltips).
3. Use the short, common term the user already sees in the app ("Save", not "Save the changes made").
4. One idea per text. Safety text (payment, terms) gets shorter without losing the guarantee.
5. Tautological test: rewrite it to check what the code **produces**, with the expected value from
   outside. Do not delete a test to silence the finding without telling the user.

## False positives

1. A quotation on purpose (an example, a list of what is searched for): `tautology: example` on the
   line, or the block between `tautology: off` and `tautology: on`.
2. A finding that is not tautology: keep it and record it silently (do not explain the false positive in chat):

   ```
   python <skill dir>/hunt.py --accept "<finding line>" --reason "<why>"
   ```

   It goes to `.claude/tautology/accepted.txt` in the project (path, rule, quoted text, reason, date).
   The hook and `--all` stop showing it for every session and agent; if the text changes, it returns.
3. A false positive of a **rule** (not of one text): fix the rule module, and the case becomes a test.

## Project settings

`.claude/tautology/config.json` (or `.agents/tautology/config.json`) in the project (all keys optional;
`config.example.json` lists them):
`languages`, `ui_paths`, `test_paths`, `skip_dirs`, `sensitive_paths`, `synonyms`, `kinds`,
`tooltip_calls`, `tooltip_second_arg`, `placement_calls`, `limits`, `tooltip_line_chars`,
`tooltip_lines`, `density`, `descriptive_words`, `max_emojis`, `design` (`max_card_nesting`, `max_dividers`...).
