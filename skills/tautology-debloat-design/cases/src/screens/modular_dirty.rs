use gpui::{div, prelude::*, px};
use crate::theme::{Palette, visual};
use crate::components::{kit, visual as cv};

struct ModularModal {
    palette: Palette,
}

impl ModularModal {
    fn card(&self) -> gpui::Div {
        let p = self.palette;
        div().bg(p.surface).border_1().border_color(p.border).rounded(px(p.card_radius))
    }

    fn label_with_help(&self, id: &'static str, text: &'static str, help: &'static str) -> gpui::Div {
        div()
            .flex()
            .items_center()
            .gap(px(6.))
            .child(div().text_size(px(visual::BODY)).child(text))
            .child(kit::info_hint(id, help, self.palette))
    }

    fn section_a(&self) -> gpui::Div {
        self.card()
            .child(self.label_with_help("a1", "Alpha option", "Explains alpha behavior concisely."))
            .child(self.label_with_help("a2", "Beta option", "Explains beta behavior concisely."))
            .child(self.label_with_help("a3", "Gamma option", "Explains gamma behavior concisely."))
    }

    fn section_b(&self) -> gpui::Div {
        self.card()
            .child(self.label_with_help("b1", "Delta option", "Explains delta behavior concisely."))
            .child(self.label_with_help("b2", "Epsilon option", "Explains epsilon behavior concisely."))
    }

    fn row_box(&self) -> gpui::Div {
        let p = self.palette;
        div()
            .bg(p.surface)
            .border_1()
            .border_color(p.border)
            .child(div().bg(p.bg).border_1().child("Inner item"))
    }

    fn section_preview(&self) -> gpui::Div {
        self.card()
            .child(self.label_with_help("p1", "Preview list", "Explains preview rule behavior."))
            .child(self.row_box())
    }

    fn section_extra(&self) -> gpui::Div {
        self.card()
            .child(self.card().child("Card A"))
            .child(self.card().child("Card B"))
            .child(self.card().child("Card C"))
    }

    fn render(&self) -> impl IntoElement {
        let p = self.palette;
        div()
            .bg(p.card)
            .border_1()
            .child(self.label_with_help("top", "Global mode", "Explains top switch cleanly."))
            .child(self.section_a())
            .child(self.section_b())
            .child(self.section_preview())
            .child(self.section_extra())
    }
}
