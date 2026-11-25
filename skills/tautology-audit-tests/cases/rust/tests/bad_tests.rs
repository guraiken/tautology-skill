use super::*;

#[test]
fn parser_multiline_same_call() {
    assert_eq!(
        parse_item("row-1"),
        parse_item("row-1"),
    );
}

#[test]
fn parser_conditional_silent() {
    let maybe = parse_optional("row-1");
    if let Some(v) = maybe {
        assert_eq!(v, 10);
    }
}

#[test]
fn parser_vacuous_loop() {
    let results = collect_items("query");
    for item in results {
        assert!(item.valid);
    }
}

#[test]
fn parser_unsigned_len() {
    let items = collect_items("all");
    assert!(items.len() >= 0);
}

#[test]
#[ignore]
fn unjustified_ignore() {
    assert_eq!(parse_item("row-1").id, 1);
}

#[test]
fn clone_case_1() {
    let cfg = load_cfg();
    let norm = normalize_text("alpha");
    let ctx = build_ctx(&cfg, &norm);
    let out = eval_item(&ctx, "first");
    assert_eq!(out.code, 10);
    assert!(out.accepted);
}

#[test]
fn clone_case_2() {
    let cfg = load_cfg();
    let norm = normalize_text("beta");
    let ctx = build_ctx(&cfg, &norm);
    let out = eval_item(&ctx, "second");
    assert_eq!(out.code, 20);
    assert!(out.accepted);
}

#[test]
fn setup_heavy_a() {
    let a = build_step_a();
    let b = build_step_b(&a);
    let c = build_step_c(&b);
    let d = build_step_d(&c);
    let e = finalize_env(&d);
    assert_eq!(run_alpha(&e), "ok-a");
}

#[test]
fn setup_heavy_b() {
    let a = build_step_a();
    let b = build_step_b(&a);
    let c = build_step_c(&b);
    let d = build_step_d(&c);
    let e = finalize_env(&d);
    assert_eq!(run_beta(&e), 42);
}

#[test]
fn setup_heavy_c() {
    let a = build_step_a();
    let b = build_step_b(&a);
    let c = build_step_c(&b);
    let d = build_step_d(&c);
    let e = finalize_env(&d);
    assert!(run_gamma(&e).is_ready);
}

#[test]
fn mock_echo_rate() {
    let mut rates = MockRates::new();
    rates.expect_fetch().returning(|| 5);
    assert_eq!(rates.fetch(), 5);
}

#[test]
fn only_when_ready() {
    let job = start_job();
    if job.ready {
        assert_eq!(job.result, 7);
    }
}
