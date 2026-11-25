export function OrdersScreen({ orders }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-3 gap-4">
        <div className="rounded-lg border p-4"><span className="text-xs">Orders</span><span className="text-3xl">128</span></div>
        <div className="rounded-lg border p-6"><span className="text-xs">Revenue</span><span className="text-3xl">{revenue}</span></div>
        <div className="rounded-lg border p-2"><span className="text-xs">Refunds</span><span className="text-3xl">4%</span></div>
      </div>
      <OrderTable orders={orders} />
    </div>
  );
}

export function TeamScreen({ stats, members }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-4 gap-4">
        {stats.map((s) => (
          <div key={s.id} className="rounded-lg border p-4">
            <span className="text-xs uppercase">{s.label}</span>
            <span className="text-2xl">{s.value}</span>
          </div>
        ))}
      </div>
      <MemberList members={members} />
    </div>
  );
}
