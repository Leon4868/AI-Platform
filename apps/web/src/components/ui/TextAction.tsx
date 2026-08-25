import type { ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export function TextAction({ className, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={cn("border-0 bg-transparent p-0 text-[10px] text-accent-cyan transition-colors hover:text-ink", className)}
      type="button"
      {...props}
    />
  );
}
