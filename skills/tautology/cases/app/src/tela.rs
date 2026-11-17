use gpui::*;

fn render(p: Palette) -> Div {
    div()
        .child(kit::section("Lembretes salvos."))
        .child(kit::primary_button("salvar", p, true).child("Salvar todas as alterações feitas agora"))
        .child("Nesta tela você encontra todas as suas tarefas cadastradas no aplicativo.")
        .child(option_row("Som", "Toca um som curto quando uma tarefa vence no aplicativo."))
        .child(option_row("Volume", "Define o volume do som que toca quando uma tarefa vence."))
        .child(option_row("Lembrete", "Abre uma janela pequena com a tarefa quando ela vence."))
        .child(option_row("E-mail", "Envia a tarefa para o seu e-mail quando ela estiver perto de vencer."))
        .child(option_row("Resumo", "Manda um resumo de todas as tarefas do dia no fim da tarde."))
        .child(div().tip("Clique aqui para abrir as configurações avançadas das listas de tarefas", p))
        .child(kit::secondary_button("ok", p).child("Fechar"))
        .child(option_row("Tema", "Claro ou escuro."))
}
