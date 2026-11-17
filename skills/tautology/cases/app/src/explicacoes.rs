use gpui::*;

// Cada bloco é um caso. Os que devem passar estão marcados "passa"; os que devem cair, "cai".

// passa: o passo diz onde fazer ("Chaves de API"), não repete o título.
fn guia(p: Palette) -> Div {
    page(
        p,
        "Gere a sua chave",
        &[
            ("Em “Chaves de API”, gere a chave".into(), String::new()),
        ],
    )
}

// passa: um texto nega o outro.
fn variantes(p: Palette) -> Div {
    div()
        .child(div().child("Abrir o lembrete da tarefa"))
        .child(div().child("Nunca abrir o lembrete da tarefa"))
}

// passa: campos irmãos (mesma chamada), não rótulo e dica.
fn campos(p: Palette) -> Div {
    div()
        .child(self.group("ID do cliente · Client id", campo(1)))
        .child(self.group("Chave do cliente · Client key", campo(2)))
}

// passa: o mesmo estado gravado em dois modelos.
fn carregar(&mut self) {
    self.message = "Carregando tarefa…".into();
    self.detail.set_message("Carregando tarefa…".into());
}

// passa: rótulo de leitor de tela não aparece ao lado do rótulo visível.
fn produto(p: Palette) -> Div {
    div()
        .child(div().child("Código do produto (opcional)"))
        .child(input().aria_label("Código do produto"))
}

// passa: confirmação destrutiva tem pergunta, consequência e aviso (3 frases).
fn confirmar(p: Palette) -> Div {
    div().child(div().child("Excluir esta tarefa? As anotações dela somem. Não dá para desfazer."))
}

// cai: 3 frases sem ser confirmação.
fn explicar(p: Palette) -> Div {
    div().child(div().child("O app lê as listas. Ele guarda as tarefas novas. Ele avisa no celular."))
}

// cai: prolixo, não óbvio.
fn falha(p: Palette) -> Div {
    div().child(div().child("Você pode tentar de novo ou voltar."))
}

// cai: o subtítulo repete o título e só acrescenta "tarefa".
fn busca(p: Palette) -> Div {
    div()
        .child(div().child("Buscar no calendário"))
        .child(div().child("Buscar tarefa no calendário"))
}

// passa: botão com tooltip na mesma linha; o tooltip não é o botão.
fn selecao(p: Palette) -> Div {
    div().child(self.button("sel-off", "Desselecionar").tip("Sair do modo de seleção agora", p))
}

// passa: tooltip escolhido por if/else; cada braço cabe no balão.
fn tarefas(p: Palette, todos: bool) -> Div {
    let info = if todos {
        "Tarefas das listas pessoais e dos projetos, dos últimos 2 dias."
    } else {
        "Tarefas das listas pessoais, dos últimos 2 dias."
    };
    div().child(kit::help_tip("tarefas-info", info, p))
}
