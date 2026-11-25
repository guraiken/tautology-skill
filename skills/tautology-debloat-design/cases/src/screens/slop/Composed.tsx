import { cn } from "@/lib/utils";
import { cva } from "class-variance-authority";

const hoisted = "rounded-2xl p-8 bg-gradient-to-br from-primary to-accent backdrop-blur-xl shadow-2xl";

export function SplitHero() {
  const hero = cn(
    "rounded-2xl p-8",
    "bg-gradient-to-br from-primary to-accent",
    "backdrop-blur-xl shadow-2xl",
  );
  return <section className={hero}>Hi</section>;
}

export function CvaDot({ live }) {
  const dot = cva("inline-block rounded-full size-2", {
    variants: { state: { live: "bg-primary animate-pulse", idle: "bg-muted" } },
  });
  return <span className={dot({ state: live ? "live" : "idle" })} />;
}

export function HoistedHero() {
  return <section className={hoisted}>Hi</section>;
}
