use gpui::{div, linear_gradient, linear_color_stop, prelude::*, IntoElement};
use crate::theme::Palette;

pub fn welcome_hero(p: &Palette) -> impl IntoElement {
    div()
        .flex()
        .p_8()
        .bg(linear_gradient(
            135.,
            linear_color_stop(p.accent, 0.),
            linear_color_stop(p.primary, 1.),
        ))
        .shadow_2xl()
        .child(div().child("Welcome back"))
}
