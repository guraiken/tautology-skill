import { Card } from "./ui";

// Known false positives: one helper among three fields, a list of cards inside one section card,
// a label and a placeholder that add an example, and no emoji.
export function PerfilLimpo({ orders }) {
  return (
    <Card>
      <h2>Profile</h2>
      <label>Email</label>
      <input placeholder="name@company.com" />
      <label>Phone</label>
      <input placeholder="+55 11 99999-0000" />
      <small>Only used to confirm large purchases.</small>
      <label>City</label>
      <input />
      <section className="border rounded">
        {orders.map((order) => (
          <Card key={order.id}>
            <span>{order.total}</span>
          </Card>
        ))}
      </section>
      <button>Save</button>
    </Card>
  );
}
