import React from "react";

// cai: título com ponto, botão longo, tooltip massivo, óbvio, subtítulo que repete o título.
export function Painel() {
  return (
    <section className="flex gap-2 items-center">
      <h2>Lembretes salvos.</h2>
      <button onClick={salvar}>Salvar todas as alterações feitas agora</button>
      <span title="Quando a tarefa vence, o app abre o lembrete e toca o som escolhido. Desligue aqui para receber só por e-mail.">?</span>
      <p>Nesta tela você encontra todas as suas tarefas</p>
      <h3>Buscar no calendário</h3>
      <p>Buscar tarefa no calendário</p>
    </section>
  );
}

// passa: classe CSS, aria-label, alt e texto curto.
export function Limpo() {
  return (
    <div className="grid grid-cols-2 gap-4">
      <img alt="Logo da loja com o nome escrito em azul" src="logo.png" />
      <button aria-label="Fechar a janela de tarefas agora mesmo">
        Fechar
      </button>
    </div>
  );
}
