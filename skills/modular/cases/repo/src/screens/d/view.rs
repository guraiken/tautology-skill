use gpui::*;

// cai: o estilo de caixa repetido em telas diferentes vira visual::caixa().
pub fn tela_d(p: Palette) -> Div {
    div()
        .flex_1()
        .min_w_0()
        .flex()
        .flex_col()
        .child(div().rounded(px(p.card_radius)).bg(p.box_fill()).border_1().border_color(p.glass_edge(p.border)).child("d"))
}
