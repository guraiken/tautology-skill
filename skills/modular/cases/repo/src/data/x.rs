// cai: o mesmo bloco de montagem em dois módulos de dados.
pub fn lista_x(registros: &[Registro]) -> Vec<Linha> {
    let mut linhas = Vec::new();
    for registro in registros.iter().filter(|r| r.ativo) {
        let rotulo = format!("{} · {}", registro.loja, registro.preco);
        linhas.push(Linha { rotulo, tom: tom_do(registro.estado), alvo: registro.id });
    }
    linhas.sort_by(|a, b| a.rotulo.cmp(&b.rotulo));
    linhas.dedup_by(|a, b| a.alvo == b.alvo);
    linhas
}

fn loja(v: &Value) -> String { v.get("loja").and_then(Value::as_str).unwrap_or("").to_owned() }
