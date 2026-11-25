use gpui::{div, prelude::*, px};
use crate::theme::{Palette, visual};

fn toolbar(p: Palette, active: usize) -> impl IntoElement {
    let label = if active == 0 { "Filtros".into() } else { format!("Filtros ({active})") };
    let filters = visual::menu_trigger(p, "filters-trigger", label, false);
    let view_toggle = div()
        .flex()
        .items_center()
        .child(marker.pill(0, p.control_radius, p.selected, true));
    div()
        .flex()
        .items_center()
        .gap(px(8.))
        .child(filters)
        .child(view_toggle)
        .child(kit::primary_button("add-item", p, true).child("Adicionar"))
}

fn active_filters(p: Palette, active_status: Option<&str>) -> Option<impl IntoElement> {
    let label = active_status?;
    Some(
        div()
            .flex()
            .items_center()
            .gap(px(6.))
            .child(visual::active_filter_chip("status-chip", format!("{label} ×"), "Limpar", p)),
    )
}

pub fn render_clean_filter_toolbar(p: Palette, active_status: Option<&str>) -> impl IntoElement {
    div()
        .flex()
        .flex_col()
        .gap(px(8.))
        .child(toolbar(p, usize::from(active_status.is_some())))
        .children(active_filters(p, active_status))
}
