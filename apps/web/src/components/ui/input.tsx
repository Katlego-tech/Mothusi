// shadcn/ui Input and Textarea, carrying console.css's .input and .goal.
import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn("input", className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn("goal", className)} {...props} />;
}
