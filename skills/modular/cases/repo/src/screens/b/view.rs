use gpui::*;

fn moldura(p: Palette) -> Div { div().bg(p.surface).border_1().border_color(p.border).rounded(px(6.)) }

// cai: o estilo de caixa repetido em telas diferentes vira visual::caixa().
pub fn tela_b(p: Palette) -> Div {
    div()
        .flex_1()
        .min_w_0()
        .flex()
        .flex_col()
        .child(div().rounded(px(p.card_radius)).bg(p.box_fill()).border_1().border_color(p.glass_edge(p.border)).child("b"))
}

// cai: a leitura de texto do JSON repetida em dados e telas vira apoio na camada 1.
fn nome(v: &Value) -> String { v.get("nome").and_then(Value::as_str).unwrap_or("").to_owned() }
