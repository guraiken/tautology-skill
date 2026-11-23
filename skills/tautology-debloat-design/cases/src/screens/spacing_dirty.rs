use gpui::{div, prelude::*, px};
use crate::theme::{Palette, kit};

pub fn render_profile_form(p: Palette) -> impl IntoElement {
    div()
        .flex()
        .flex_col()
        .gap(px(8.))
        .child(kit::field_label("Name", p))
        .child(div().h(px(24.)))
        .child(kit::text_field("name", p))
        .child(kit::text_field("email", p))
        .child(div().h(px(16.)))
        .child(kit::text_field("phone", p))
        .child(div().h(px(6.)))
        .child(div().pt(px(24.)).pb(px(8.)).px(px(26.)).child(kit::switch("news", p)))
}

pub fn render_security(p: Palette) -> impl IntoElement {
    div()
        .flex()
        .flex_col()
        .child(kit::text_field("recovery", p))
        .child(div().h(px(4.)))
        .child(kit::section_title("Security", p))
        .child(div().h(px(20.)))
        .child(kit::switch("two-factor", p))
}

pub fn render_groups(p: Palette) -> impl IntoElement {
    div()
        .flex()
        .flex_col()
        .gap(px(8.))
        .child(div().flex().flex_col().gap(px(24.)).child(kit::switch("alerts", p)).child(kit::switch("sounds", p)))
        .child(kit::switch("badges", p))
}
