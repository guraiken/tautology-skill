---
name: tautology-audit-tests
description: Audits unit and integration tests for self-confirming assertions (tautologies, mock echoes, conditional silent passes, vacuous loops), undesirable/flaky patterns (sleeps, unjustified skips, .only, swallowed errors, live network), and repetitive copy-pasted test bodies/setups; refactors tests against independent oracles and table-driven helpers. Invoke with /tautology-audit-tests [scope or file].
---

# Audit tests

Audit test files or scopes to catch **meaningless, self-confirming, flaky, or bloated tests**, and rewrite them so every test genuinely could fail on a real regression. Keep all assertions anchored to **independent expected values** (literals, independent computation, or recorded fixtures), never to code under test echoing itself.

`<skill dir>` is the directory holding this file. `<tautology dir>` is `../tautology` and `<modular dir>` is `../modular`.

## 1. Locate & scan tests

1. Find the test files or `#[cfg(test)]` modules matching the target term or scope:
   ```
   python <skill dir>/test_audit.py --find "<module or term>"
   ```
2. Run the deep test audit alongside the standard test rules:
   ```
   python <skill dir>/test_audit.py --all <test files or dirs>
   ```
   Use `--diff` for changed lines/tests against `HEAD`, or `--only self-confirming,undesirable,repetitive` to narrow focus.

## 2. Evaluate findings across 3 axes

1. **Self-confirming (`self-confirming`)**:
   - Multi-line or inline expressions compared with themselves (`assert_eq!(f(x), f(x))`, `expect(a).toEqual(a)`), or expected variables computed by calling the same logic under test.
   - Conditional assertions that pass silently when the wrong path runs: `if let Some(v) = res { assert... }` without `else { panic!(...) }`, `match` with empty wildcard/error arms, `try/except` or `try/catch` lacking an unconditional fail before `catch`, or `assert!(x.len() >= 0)` (always true on unsigned lengths).
   - Vacuous loops (`for x in results { assert!(...); }`) with no non-empty length check before the loop.
   - Local literal/container echoes (`Struct { a: 1 } -> assert_eq!(s.a, 1)`, `vec.push(x) -> assert!(vec.contains(&x))`, `dict["k"] = v -> assert d["k"] == v`) without calling production logic in between.
   - Mocking or patching the subject named in the test itself, or asserting directly on a stub's own `return_value`.
2. **Undesirable (`undesirable`)**:
   - Wall-clock `sleep` / `setTimeout` (`>= 100ms`) outside explicit retry/timeout/backoff tests.
   - Focused `.only` / `fdescribe` / `fit`, or disabled tests (`skip`, bare `#[ignore]`) with no explanation or documented benchmark purpose.
   - Empty `except: pass` / `catch {}` swallowing test failures.
   - Live `http://` / `https://` requests in unit tests without localhost or a mock server.
3. **Repetitive (`repetitive`)**:
   - Multiple tests in the same file sharing 85%+ identical normalized structure: collapse them into `@pytest.mark.parametrize`, `test.each`, or a Rust `for (input, expected) in [...]` case table.
   - Multi-line inline fixture setup (`5+` identical lines) copied across `3+` tests: extract one shared fixture/builder helper.

## 3. Refactor & verify

- **Never delete coverage silently to clear a finding:** replace circular expectations with concrete expected outputs, convert vacuous loops/conditionals into explicit `assert!(!items.is_empty())` / `pytest.raises` / `let Ok(...) = res else { panic!(...) }`, and consolidate duplicated tests into clean case tables.
- Re-run `test_audit.py --all <edited files>`, `hunt.py --all <edited files>`, and the project's test runner (`cargo test`, `pytest`, `vitest`, or configured command) to confirm every rewritten test runs green and actually fails if mutated mentally.
- With the `tautology` plugin enabled, creating a new test file or adding a new test function (`#[test]`, `def test_*`, `it`/`test`, or requesting tests in the prompt) automatically triggers `test_audit` at `Stop`; `/tautology-audit-tests` keeps the guardrail active across iterative follow-ups until you switch tasks. Record deliberate exceptions with `--accept "<finding>" --reason "<why>"`.
