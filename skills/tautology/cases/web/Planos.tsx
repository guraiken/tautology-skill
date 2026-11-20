// AI-style screen: a tooltip on every toggle, badges on every row, a note under the card and at the page end.
export function Planos() {
  return (
    <main>
      <h1>Planos</h1>
      <Card>
        <div className="flex items-center gap-2">
          <span>Notificações</span>
          <Tooltip content="Receba alertas quando algo mudar" />
          <Badge>Novo</Badge>
        </div>
        <Switch checked={notify} />
        <div className="flex items-center gap-2">
          <span>Modo escuro</span>
          <Tooltip content="Deixa a interface escura à noite" />
          <span className="rounded-full bg-gradient-to-r from-violet-500 to-fuchsia-500 px-2 text-xs">Beta</span>
        </div>
        <Switch checked={dark} />
        <div className="flex items-center gap-2">
          <span>Backup</span>
          <Tooltip content="Copia seus dados toda noite" />
        </div>
        <Switch checked={backup} />
        <div className="flex gap-2">
          <Button>Restaurar</Button>
        </div>
        <p className="text-xs text-muted-foreground">Você pode mudar isso quando quiser.</p>
      </Card>
      <Button>Assinar</Button>
      <p className="text-xs text-gray-500">As alterações são salvas automaticamente.</p>
    </main>
  );
}
