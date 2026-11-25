import { cva } from "class-variance-authority";

export const buttonVariants = cva("inline-flex items-center rounded-md text-sm font-medium", {
  variants: {
    variant: {
      default: "bg-primary text-primary-foreground hover:bg-primary/90",
      outline: "border border-input bg-background",
      ghost: "hover:bg-accent",
    },
  },
  defaultVariants: { variant: "default" },
});
