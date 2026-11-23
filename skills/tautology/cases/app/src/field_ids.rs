use gpui::*;

fn render(p: Palette) -> Div {
    div()
        .child(self.field_title("keyword-channels-info", "Somente estes canais", None, p))
        .child(self.field_title("keyword-exclude-info", "Palavras ignoradas", None, p))
        .child(self.field_title("keyword-price-info", "Faixa de preço", None, p))
        .child(self.field_title("notify", "Alertas imediatos", None, p))
}
