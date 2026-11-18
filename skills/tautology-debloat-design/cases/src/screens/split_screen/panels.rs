use gpui::{div, prelude::*};
use crate::theme::Palette;
use super::view::SplitScreen;

impl SplitScreen {
    pub fn extra_panel_a(&self, p: Palette) -> impl IntoElement {
        div()
            .bg(p.surface)
            .border_1()
            .child(div().bg(p.card).border_1().child("1"))
            .child(div().bg(p.card).border_1().child("2"))
            .child(div().bg(p.card).border_1().child("3"))
    }

    pub fn extra_panel_b(&self, p: Palette) -> impl IntoElement {
        div()
            .bg(p.surface)
            .border_1()
            .child(div().bg(p.card).border_1().child("4"))
            .child(div().bg(p.card).border_1().child("5"))
            .child(div().bg(p.card).border_1().child("6"))
    }
}
