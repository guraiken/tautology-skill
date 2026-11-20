import { Card, Divider } from "./ui";

export function Perfil() {
  return (
    <Card>
      <h2>Profile Settings</h2>
      <p>Manage your profile settings below.</p>
      <Card>
        <Card>
          <h3>✨ Contact</h3>
          <label>Email</label>
          <input placeholder="Enter your email address" />
          <small>We use this address for receipts.</small>
          <label>Telefone</label>
          <input placeholder="Digite seu telefone" />
          <small>Usado só para confirmar compras grandes.</small>
          <label>City</label>
          <input placeholder="City" />
          <small>Shown on invoices next to your name.</small>
        </Card>
      </Card>
      <p>💡 Tip: changes are saved as you type.</p>
      <button>🚀 Publish</button>
    </Card>
  );
}
