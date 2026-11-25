export function PricingTile({ plan, price, features }: { plan: string; price: string; features: string[] }) {
  return (
    <a href="#pricing" className="block rounded-xl border bg-card/80 p-6">
      <h3 className="text-lg font-semibold">{plan}</h3>
      <p className="mt-2 text-3xl font-bold">{price}</p>
      <small className="text-xs text-muted-foreground">per month, billed yearly</small>
      <ul className="mt-4 space-y-2 text-sm text-muted-foreground">
        {features.map((f) => (
          <li key={f} className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-primary" />
            {f}
          </li>
        ))}
      </ul>
      <span className="mt-6 inline-flex rounded-md bg-primary px-4 py-2 text-primary-foreground">Choose</span>
    </a>
  );
}
