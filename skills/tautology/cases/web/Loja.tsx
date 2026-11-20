// Clean on purpose: a store footer (links, legal, contact) is a site footer, not a note.
// The icon button keeps its tooltip (it is its only name); one status chip drives an action.
export function Loja() {
  return (
    <div>
      <header>
        <Tooltip content="Carrinho">
          <IconButton aria-label="Carrinho"><CartIcon /></IconButton>
        </Tooltip>
      </header>
      <section>
        <h2>Pedido 1042</h2>
        <Badge variant="warning">Aguardando pagamento</Badge>
        <Button>Pagar agora</Button>
      </section>
      <footer className="border-t py-8">
        <nav>
          <a href="/ajuda">Central de ajuda</a>
          <a href="/trocas">Trocas e devoluções</a>
        </nav>
        <p className="text-xs text-gray-500">Atendimento de segunda a sexta, das 9h às 18h.</p>
        <p className="text-xs text-gray-500">© 2026 Loja Exemplo Ltda. Todos os direitos reservados.</p>
      </footer>
    </div>
  );
}
