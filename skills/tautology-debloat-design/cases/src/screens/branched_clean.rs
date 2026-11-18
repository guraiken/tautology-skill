use gpui::{div, prelude::*};
use crate::theme::Palette;

struct ModalScreen {
    mode: u8,
    loading: bool,
}

impl ModalScreen {
    fn info_hint(&self, text: &str, _p: Palette) -> impl IntoElement {
        div().child(text.to_string())
    }

    fn settings_card(&self, title: &str, hint: Option<&str>, p: Palette, body: impl IntoElement) -> impl IntoElement {
        let mut head = div().child(title.to_string());
        if let Some(tip) = hint {
            head = head.child(self.info_hint(tip, p));
        }
        div().bg(p.card).border_1().child(head).child(body)
    }

    pub fn render_modes(&self, p: Palette) -> impl IntoElement {
        if self.loading {
            return div()
                .child(self.settings_card("Sync", None, p, div().child("1")))
                .child(self.settings_card("Cache", None, p, div().child("2")))
                .child(self.settings_card("Auth", None, p, div().child("3")));
        }
        if self.mode == 0 {
            div()
                .child(self.settings_card(
                    "Origem",
                    None,
                    p,
                    div().child("A1"),
                ))
                .child(self.settings_card(
                    "Destino",
                    None,
                    p,
                    div().child("A2"),
                ))
                .child(self.settings_card(
                    "Filtro",
                    None,
                    p,
                    div().child("A3"),
                ))
        } else if self.mode == 1 {
            div()
                .child(self.settings_card("Conta", None, p, div().child("B1")))
                .child(self.settings_card("Alerta", None, p, div().child("B2")))
                .child(self.settings_card("Som", None, p, div().child("B3")))
        } else {
            div()
                .child(self.settings_card("Extra", None, p, div().child("C1")))
                .child(self.settings_card("Rede", None, p, div().child("C2")))
                .child(self.settings_card("Log", None, p, div().child("C3")))
        }
    }
}
