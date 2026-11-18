use gpui::{Rgba, div, prelude::*, px, rgba};
use crate::theme::{Palette, visual};

pub fn render_clean_modal(p: Palette, items: &[&str]) -> impl IntoElement {
    let mut rows = div().flex().flex_col().gap(px(6.));
    for item in items {
        rows = rows.child(
            div()
                .flex()
                .items_center()
                .gap(px(6.))
                .child(visual::tag((*item).to_owned(), p.text, p.surface))
                .child(visual::tag("v2", p.text, p.surface))
                .child(visual::icon("check", p.primary, 14.)),
        );
    }
    div()
        .bg(p.card)
        .border_1()
        .border_color(p.border)
        .rounded(px(p.card_radius))
        .p(px(20.))
        .gap(px(12.))
        .child(
            div()
                .flex()
                .items_center()
                .gap(px(10.))
                .child(
                    div()
                        .size(px(36.))
                        .rounded(px(18.))
                        .bg(rgba(0x0000_0000))
                        .child(visual::icon("settings", p.primary, 16.)),
                )
                .child(
                    div()
                        .text_size(px(visual::HEADLINE))
                        .text_color(p.strong)
                        .child("Account settings"),
                ),
        )
        .child(
            div()
                .bg(Rgba { a: 0.08, ..p.primary })
                .rounded(px(p.control_radius))
                .text_size(px(visual::BODY))
                .text_color(p.text)
                .child("Saved in credential vault."),
        )
        .child(rows)
}
