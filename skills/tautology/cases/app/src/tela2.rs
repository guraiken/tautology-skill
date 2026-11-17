use gpui::*;

fn render(p: Palette) -> Div {
    div()
        .child(
            kit::primary_button("salvar", p, true)
                .child("Salvar todas as alterações feitas agora"),
        )
        .child(
            "Texto comum dentro de um child aberto em outra linha.",
        )
}
