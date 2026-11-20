// GPUI: the settings page ends in a muted note, and a status line explains a disabled state.
use gpui::{div, prelude::*, px};

#[cfg(test)]
mod tests;

fn render(p: Palette) -> gpui::Div {
    div()
        .flex_col()
        .child(scroll_area)
        .child(
            div()
                .text_size(px(visual::FOOTNOTE))
                .text_color(p.muted)
                .child("As alterações são salvas automaticamente."),
        )
}

fn status(enabled: bool, p: Palette) -> (String, gpui::Rgba) {
    if !enabled {
        return ("Atualização automática só no app instalado".into(), p.muted);
    }
    ("Atualizado".into(), p.success)
}

fn modal(p: Palette) -> gpui::Div {
    div()
        .child(
            div()
                .text_size(px(visual::FOOTNOTE))
                .text_color(p.muted)
                .child("A chave fica guardada no cofre do sistema."),
        )
        .child(kit::text_field("key", "Cole a chave"))
}
