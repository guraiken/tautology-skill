use super::*;

/// Manual benchmark run only when profiling rendering cost.
#[test]
#[ignore]
fn documented_bench() {
    run_heavy_probe();
}

#[test]
fn checks_table_with_nonempty_guard() {
    let cases = [("10", 10), ("20", 20)];
    assert!(!cases.is_empty());
    for (raw, expected) in cases {
        assert_eq!(parse_int(raw), expected);
    }
}

#[test]
fn passes_local_label_into_sut() {
    let expected = "Ready";
    let state = ViewState { active: true, label: expected.into() };
    let actual = format_state(&state);
    assert_eq!(actual, expected);
}

#[test]
fn uses_let_else_instead_of_silent_if_let() {
    let Ok(parsed) = parse_strict("42") else {
        panic!("expected valid number");
    };
    assert_eq!(parsed, 42);
}

#[test]
fn guards_is_ok_before_if_let() {
    let parsed = parse_strict("42");
    assert_eq!(parsed.is_ok(), true);
    if let Ok(val) = parsed {
        assert_eq!(val, 42);
    }
}

#[test]
fn single_line_helper_verify(){crate::verify_cases(&[1, 2]);}

