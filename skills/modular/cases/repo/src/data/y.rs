pub fn lista_y(registros: &[Registro]) -> Vec<Linha> {
    let mut linhas = Vec::new();
    for registro in registros.iter().filter(|r| r.ativo) {
        let rotulo = format!("{} · {}", registro.loja, registro.preco);
        linhas.push(Linha { rotulo, tom: tom_do(registro.estado), alvo: registro.id });
    }
    linhas.sort_by(|a, b| a.rotulo.cmp(&b.rotulo));
    linhas.dedup_by(|a, b| a.alvo == b.alvo);
    linhas
}

fn nome(v: &Value) -> String { v.get("nome").and_then(Value::as_str).unwrap_or("").to_owned() }

// cai com conflito: estilo de interface também aparece na camada de dados.
fn borda(p: Palette) -> Div { div().bg(p.surface).border_1().border_color(p.border).rounded(px(6.)) }
