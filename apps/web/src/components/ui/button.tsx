// shadcn/ui Button (Radix Slot + CVA), restyled with the console's tokens: the variants map onto
// console.css's .btn rules instead of the stock zinc theme.
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

const buttonVariants = cva("btn disabled:opacity-60 disabled:cursor-not-allowed", {
  variants: {
    variant: { default: "", primary: "primary", quiet: "quiet" },
    size: { default: "", small: "small" },
  },
  defaultVariants: { variant: "default", size: "default" },
});

export interface ButtonProps extends ComponentProps<"button">, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
