// GPUI: status dot beside status text is a decorative tic; series legend, separator, check dot and switch thumb stay.
use gpui::{div, prelude::*, px};

fn monitor_bar(p: Palette) -> gpui::Div {
    div()
        .flex()
        .items_center()
        .child(div().size(px(8.)).flex_shrink_0().rounded_full().bg(p.success))
        .child(div().text_color(p.success).child("Monitorando"))
}

fn status_chip(p: Palette) -> gpui::Div {
    div()
        .flex()
        .items_center()
        .child(
            div()
                .size(px(6.))
                .rounded_full()
                .bg(p.accent),
        )
        .child(div().text_color(p.accent).child("Pausado"))
}

fn store_legend(store: &str, p: Palette) -> gpui::Div {
    let dot = store_dot(store).map(|c| div().size(px(6.)).rounded_full().bg(c));
    div().children(dot).child(store.to_owned())
}

fn menu_row(checked: bool, glyph: gpui::Rgba) -> gpui::Div {
    div()
        .child("Item")
        .when(checked, |row| row.child(div().size(px(7.)).rounded_full().bg(glyph)))
}

fn pill_separator(p: Palette) -> gpui::Div {
    div().child("Loja").child(div().size(px(3.)).rounded_full().bg(p.muted))
}

fn history_legend(p: Palette) -> gpui::Div {
    div()
        .child(div().size(px(8.)).rounded_full().bg(p.success))
        .child("Menor preço")
}

fn switch_thumb(p: Palette) -> gpui::Div {
    div().size(px(16.)).rounded_full().bg(p.surface)
}
