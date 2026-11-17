from PySide6.QtWidgets import QLabel, QPushButton
def montar(self):
    busca = QPushButton("Buscar tarefas")
    busca.setToolTip("Procura tarefas")
    salvar = QPushButton("Salvar regra")
    salvar.setToolTip("Grava a regra no disco para usar depois")
    # fecha a janela
    self.window.close()
    # o Qt reabre sozinho se fechar antes do sinal
    self.window.close()
    # incrementa o contador
    contador += 1
    log.info("Buscar tarefas agora mesmo na lista escolhida")
    vazio = QLabel("Nenhuma tarefa encontrada")
    dica = QLabel("Ajuste os filtros para ver mais resultados")
