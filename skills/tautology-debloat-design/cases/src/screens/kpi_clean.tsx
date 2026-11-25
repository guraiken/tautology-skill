export function InvoiceScreen({ invoice }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-lg border p-4"><span className="text-xs">Total due</span><span className="text-3xl">{invoice.total}</span></div>
      <div className="rounded-lg border p-4"><span className="text-sm">{invoice.customer}</span></div>
      <h1 className="text-2xl">{invoice.title}</h1>
      <LineItems items={invoice.items} />
    </div>
  );
}
