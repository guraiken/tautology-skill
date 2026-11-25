import { Check } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export function PlanCard({ plan, current, onSelect }: { plan: Plan; current: boolean; onSelect: (id: string) => void }) {
  const price = new Intl.NumberFormat("en-US", { style: "currency", currency: plan.currency }).format(plan.price / 100);
  return (
    <Card className={current ? "border-primary" : undefined}>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          {plan.name}
          {plan.popular && <Badge>Popular</Badge>}
        </CardTitle>
        <CardDescription>{plan.tagline}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-3xl font-bold">{price}<span className="text-sm font-normal text-muted-foreground">/mo</span></p>
        <ul className="mt-4 space-y-2 text-sm">
          {plan.features.map((f) => (
            <li key={f} className="flex gap-2"><Check className="h-4 w-4 text-primary" />{f}</li>
          ))}
        </ul>
      </CardContent>
      <CardFooter>
        <Button className="w-full" disabled={current} onClick={() => onSelect(plan.id)}>
          {current ? "Current plan" : "Choose plan"}
        </Button>
      </CardFooter>
    </Card>
  );
}
