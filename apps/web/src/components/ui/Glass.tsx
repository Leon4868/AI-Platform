import type { ComponentPropsWithoutRef, ElementType, ReactNode } from "react";
import { cn } from "../../lib/cn";

type GlassProps<T extends ElementType> = {
  as?: T;
  children: ReactNode;
  className?: string;
  strength?: "soft" | "strong";
} & Omit<ComponentPropsWithoutRef<T>, "as" | "children" | "className">;

export function Glass<T extends ElementType = "div">({
  as,
  children,
  className = "",
  strength = "soft",
  ...props
}: GlassProps<T>) {
  const Component = as ?? "div";
  return (
    <Component
      className={cn("glass-panel", strength === "strong" && "glass-panel-strong", className)}
      {...props}
    >
      {children}
    </Component>
  );
}

export function PanelSection({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="border-line/65 py-4 first:pt-0 [&+&]:border-t">
      <header className="mb-2.5 flex items-center justify-between">
        <h3 className="text-[10px] font-semibold tracking-[.08em] text-muted uppercase">{title}</h3>
        {action}
      </header>
      {children}
    </section>
  );
}

export function PropertyRow({ label, value, accent }: { label: string; value: ReactNode; accent?: boolean }) {
  return (
    <div className="border-line/50 flex min-h-8.5 items-center justify-between border-b">
      <span className="text-[10px] text-muted">{label}</span>
      <strong className={cn("text-[10px] font-semibold text-ink", accent && "text-accent-cyan")}>{value}</strong>
    </div>
  );
}
