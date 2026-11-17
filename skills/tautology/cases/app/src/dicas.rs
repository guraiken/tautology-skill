use gpui::*;

const AJUDA_LONGA: &str = "Quando a tarefa vence, o app abre o lembrete, toca o som e manda um e-mail. \
    Se o modo foco estiver ligado, nada disso acontece até você sair dele.";

fn render(p: Palette, n: usize) -> Div {
    let dica = format!("{n} tarefas novas esperando. Abra a lista para ver todas elas, uma por uma, com o histórico de mudanças.", n = n);
    div()
        .child(icon().tip("Copiar link", p))
        .child(icon().tip(AJUDA_LONGA, p))
        .child(icon().tip(dica, p))
        .child(kit::help_tip(
            "ajuda",
            "Cada filtro procura as palavras no título da tarefa e em todo o texto das anotações dela. Use vírgulas.",
            p,
        ))
        .child(icon().tip("Abre o lembrete de teste\nsem enviar nada", p))
}
