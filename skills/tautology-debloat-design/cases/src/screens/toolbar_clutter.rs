use gpui::{div, prelude::*, px};
use crate::theme::{Palette, visual};

fn toolbar(p: Palette) -> impl IntoElement {
    let type_trigger = visual::menu_trigger(p, "type-trigger", "Todos os tipos".into(), true)
        .w(px(175.))
        .h(px(35.));
    let sort_trigger = visual::menu_trigger(p, "sort-trigger", "Nome".into(), true)
        .w(px(199.))
        .h(px(35.));
    div()
        .flex()
        .items_center()
        .gap(px(9.))
        .child(type_trigger)
        .child(sort_trigger)
        .child(kit::primary_button("add-item", p, true).child("Adicionar"))
}

fn filters(p: Palette, statuses: &[&str], current: &str) -> impl IntoElement {
    let mut row = div().flex().items_center().gap(px(6.));
    for status in statuses {
        let mut chip = kit::filter_chip(*status, p).child(*status);
        if *status == current {
            chip = chip.bg(p.primary).border_color(p.primary);
        }
        row = row.child(chip);
    }
    let view_toggle = div()
        .flex()
        .items_center()
        .child(marker.pill(0, p.chip_radius, p.primary, true));
    div().flex().justify_between().child(row).child(view_toggle)
}

pub fn render_dirty_filter_toolbar(p: Palette, statuses: &[&str]) -> impl IntoElement {
    div()
        .flex()
        .flex_col()
        .gap(px(12.))
        .child(toolbar(p))
        .child(filters(p, statuses, "Todos"))
}
