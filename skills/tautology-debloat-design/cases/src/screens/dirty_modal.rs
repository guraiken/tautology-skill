use gpui::{div, prelude::*, px, rgb};
use crate::theme::{Palette, visual};

pub fn render_dirty_modal(p: Palette) -> impl IntoElement {
    div()
        .bg(p.card)
        .border_1()
        .border_color(p.border)
        .rounded(px(14.))
        .gap(px(12.))
        .child(
            div()
                .bg(p.surface)
                .border_1()
                .child(
                    div()
                        .bg(p.bg)
                        .border_1()
                        .child(
                            div()
                                .bg(p.card)
                                .border_1()
                                .child(
                                    div()
                                        .text_size(px(15.))
                                        .text_color(rgb(0xff00aa))
                                        .child("Title")
                                )
                        )
                )
        )
        .child(div().h(px(1.)))
        .child(div().h(px(1.)))
        .child(div().h(px(1.)))
        .child(div().h(px(1.)))
        .child(div().h(px(1.)))
        .child(div().h(px(24.)))
        .child(div().h(px(24.)))
}
