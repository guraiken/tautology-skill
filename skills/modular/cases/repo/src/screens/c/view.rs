use gpui::*;

fn moldura(p: Palette) -> Div { div().bg(p.surface).border_1().border_color(p.border).rounded(px(6.)) }

// cai: o estilo de caixa repetido em telas diferentes vira visual::caixa().
pub fn tela_c(p: Palette) -> Div {
    div()
        .flex_1()
        .min_w_0()
        .flex()
        .flex_col()
        .child(div().rounded(px(p.card_radius)).bg(p.box_fill()).border_1().border_color(p.glass_edge(p.border)).child("c"))
}

fn titulo(v: &Value) -> String { v.get("titulo").and_then(Value::as_str).unwrap_or("-").to_owned() }

#[cfg(test)]
mod tests {
    // passa: teste repete de propósito.
    fn t() { let _ = v.get("x").and_then(Value::as_str).unwrap_or("").to_owned(); }
}
