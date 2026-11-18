# tautology-skill

A Claude Code and Google Antigravity plugin that catches **tautology** before the agent hands you its
work: UI text that repeats its neighbour, tests that confirm themselves, comments that restate the code,
and answers that say the same thing twice. A second skill, **modular**, finds code sequences copied
across files and says where to extract them.

It runs by itself through hooks. You do not have to remember to call it.

## What it helps with

| Area | What it flags | Example |
|---|---|---|
| UI copy | a tooltip equal to the label, a subtitle that rewrites the title, pleonasms, doubled words | `Search tasks` with tooltip `Find tasks` |
| Visual bloat | text over its kind's size limit, "click here", "this page shows", "you can", too many explanations on one screen | an 8-word button |
| Tests | a value compared with itself, a value the test wrote, a check that recomputes the expected value, a test without asserts | `r = f(10)` ... `assert r == f(10)` |
| Code | a function that only forwards its arguments, a comment that restates the line, a stuttering name, a copied line | `# close the window` above `window.close()` |
| Answers | the same sentence or idea in two paragraphs, a closing summary that retells the body | |
| Copied code (modular) | 4+ chained calls repeated in 3+ files, 6+ equal lines in 2+ places, with a destination layer | the same card style chain in five screens |

It reads Python, Rust, JS/TS and JSX/TSX. English is the default language; Portuguese is built in, and a
project can enable both. Synonyms cross languages, so "Search" and "Find" count as the same idea.

The hooks only point at candidates. Claude decides what is a defect, fixes it, or keeps it with a
one-line reason.

## How it works

1. **Before each edit** (`PreToolUse`), `edit_gate.py` keeps a copy of the file as it was before the
   session first touched it. Only the lines the session changes are judged later, so old code and
   other people's edits never block you.
2. **When Claude finishes an answer** (`Stop`), `stop_gate.py` scans those changed lines and the answer
   itself in one Python process. A new finding sends the answer back **once**, with a list of findings
   and a fix for each. A finding that was already shown never blocks again, and `stop_hook_active`
   always lets the answer through, so Claude cannot get stuck in a loop.
3. **When you send a request** (`UserPromptSubmit`), `modular_gate.py` notes whether it asks for a refactor.
   At `Stop`, a new or changed component, or any file touched during a refactor, is checked for copied
   sequences. If the project has no architecture map, Claude asks you which layout to adopt before it
   extracts anything.

Session state lives in `.git/tautology/` of your project and is never committed.

## Cost

Measured with `claude plugin details` and on the bundled test cases.

| What | Tokens |
|---|---|
| Always in context (the two skill descriptions) | ~226 per session |
| Loading the `tautology` skill (when Claude or you invoke it) | ~3.5k |
| Loading the `modular` skill | ~1.2k |
| Hooks themselves | 0: they run outside the model |
| A Stop report when something is found | ~750 for a screen with 8 findings, plus the turn Claude spends fixing them |

An answer with nothing new to report costs nothing extra. Each hook takes about 0.7 s of local
Python time (around 1 s when `bin/py.sh` has to look for the interpreter; set `TAUTOLOGY_PYTHON` to skip
the search).

## Install

### Claude Code

```
/plugin marketplace add guraiken/tautology-skill
/plugin install tautology@tautology-skill
```

The skills are then available as `/tautology:tautology` (add `copy`, `code`, `tests` or `answer` to
narrow the scan) and `/tautology:modular`.

### Google Antigravity

Clone into your global plugins folder (or into `.agents/plugins/tautology` inside a workspace):

```
git clone https://github.com/guraiken/tautology-skill ~/.gemini/config/plugins/tautology
```

Antigravity discovers `plugin.json`, `hooks.json`, and `skills/` automatically (`PreToolUse`,
`PreInvocation`, and `Stop` hooks run on both Unix via `bin/py.sh` and Windows via `bin/py.cmd`).

It needs Python 3.10 or newer, standard library only. `bin/py.sh` and `bin/py.cmd` look for `python3`,
`python` and `py -3`; set `TAUTOLOGY_PYTHON` to choose one. Without Python the hooks do nothing and
never block.

## Per-project settings (optional)

Settings live under `.claude/` (or `.agents/` when present):

- `.claude/tautology/config.json`: languages, UI folders, the names of your own components (a tooltip
  helper, a settings row), synonyms from your domain, size limits. See
  `skills/tautology/config.example.json`.
- `.claude/tautology/accepted.txt` and `.claude/modular/accepted.txt`: judged false positives. Claude
  writes them with `--accept "<finding>" --reason "<why>"`; a finding comes back if its text changes.
- `.claude/modular/architecture.md`: where extracted pieces go, by layer (format in
  `skills/modular/SKILL.md`).
- Mark a deliberate quotation with `tautology: example` on the line, or wrap a block in
  `tautology: off` / `tautology: on`.

## Running it by hand

```
python skills/tautology/hunt.py --diff               # changes against HEAD
python skills/tautology/hunt.py --all path/to/file   # a whole file
python skills/modular/patterns.py --all              # copied sequences in the repository
```

## Self-tests

```
python skills/tautology/hunt.py --selftest
python skills/modular/patterns.py --selftest
```

Both compare the hunt with the expected findings in `cases/` and run the hooks end to end in a
temporary repository.

## License

MIT
