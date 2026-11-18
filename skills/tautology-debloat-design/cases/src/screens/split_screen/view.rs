use gpui::{div, prelude::*};
use crate::theme::Palette;

pub struct SplitScreen;

impl SplitScreen {
    pub fn view(&self, p: Palette) -> impl IntoElement {
        div()
            .bg(p.bg)
            .child(self.extra_panel_a(p))
            .child(self.extra_panel_b(p))
    }
}
