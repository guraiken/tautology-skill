// cai: DOM com texto prolixo; passa: seletor e evento.
const box = document.querySelector(".task-box .title");
box.addEventListener("click", abrir);
box.textContent = "Por favor, aguarde enquanto as tarefas carregam.";
