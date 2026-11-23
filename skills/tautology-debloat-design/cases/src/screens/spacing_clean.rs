use gpui::{div, prelude::*, px};
use crate::theme::{Palette, kit};

pub fn render_settings_form(p: Palette, wide: bool) -> impl IntoElement {
    div()
        .flex()
        .flex_col()
        .gap(px(8.))
        .pt(px(16.))
        .pb(px(16.))
        .child(kit::section_title("Account", p))
        .child(kit::field_label("Name", p))
        .child(kit::text_field("name", p))
        .child(div().h(px(12.)))
        .child(kit::field_label("Email", p))
        .child(kit::text_field("email", p))
        .child(div().h(px(12.)))
        .child(kit::section_title("Security", p))
        .child(kit::switch("two-factor", p))
        // The 1 px border sits inside the box: 11 + 1 lines up with the 12 px column.
        .child(div().pl(px(11.)).pr(px(12.)).border_1().child(kit::switch("backup", p)))
        .when(wide, |form| form.pt(px(40.)).pb(px(4.)))
}

// Spacing inside a multi-parameter closure is conditional, and `if let Some(row)` binds `row`, not
// `Some`: neither leaks into the stack below. An `h_flex()` row is not a vertical stack.
pub fn render_optional_row(p: Palette, note: Option<String>) -> impl IntoElement {
    if let Some(row) = note.clone() {
        let _ = row;
    }
    div()
        .flex()
        .flex_col()
        .gap(px(8.))
        .when_some(note, |this, text| this.pt(px(40.)).pb(px(4.)).child(text))
        .child(h_flex().child(kit::field_label("Mode", p)).child(div().w(px(24.))).child(kit::switch("mode", p)).child(kit::switch("auto", p)))
        .child(div().pt(px(24.)).p(px(8.)).child(kit::switch("sync", p)))
}

// Brackets in char literals and spacing in block comments are not code; a `let` bound to an `if`
// binds the branch value, not its condition.
pub fn render_trimmed(p: Palette, wide: bool, name: &str) -> impl IntoElement {
    let body = if wide { div().gap(px(8.)) } else { div() };
    /* .child(div().pt(px(26.)).pb(px(2.))) */
    div()
        .flex()
        .flex_col()
        .gap(px(8.))
        .child(kit::field_label(name.trim_end_matches(')'), p))
        .child(body)
        .child(kit::text_field("name", p))
}
