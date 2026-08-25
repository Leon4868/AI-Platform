import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/cn";

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  icon: ReactNode;
  active?: boolean;
  badge?: string;
  tooltipPlacement?: "top" | "right" | "bottom" | "left";
};

const tooltipPlacementClass = {
  top: "bottom-[calc(100%+8px)] left-1/2 -translate-x-1/2 translate-y-1 group-hover:translate-y-0 group-focus-visible:translate-y-0",
  right: "top-1/2 left-[calc(100%+8px)] -translate-y-1/2 -translate-x-1 group-hover:translate-x-0 group-focus-visible:translate-x-0",
  bottom: "top-[calc(100%+8px)] left-1/2 -translate-x-1/2 -translate-y-1 group-hover:translate-y-0 group-focus-visible:translate-y-0",
  left: "top-1/2 right-[calc(100%+8px)] -translate-y-1/2 translate-x-1 group-hover:translate-x-0 group-focus-visible:translate-x-0",
} as const;

export function IconButton({ label, icon, active, badge, tooltipPlacement = "right", className = "", ...props }: IconButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        "subtle-action group relative shrink-0 hover:-translate-y-px hover:border-line hover:bg-accent-cyan/8 hover:text-ink",
        active && "border-accent-cyan/20 bg-accent-cyan/12 text-accent-cyan shadow-[inset_3px_0_var(--color-accent-cyan)]",
        className,
      )}
      aria-label={label}
      title={label}
      {...props}
    >
      {icon}
      {badge ? <span className="absolute -top-0.75 -right-0.75 min-w-3.75 rounded-lg bg-accent-red px-1 py-0.5 text-[9px] text-white">{badge}</span> : null}
      <span
        className={cn(
          "pointer-events-none absolute z-100 w-max rounded-[7px] border border-line bg-[#0b1928] px-2 py-1.5 text-ink opacity-0 shadow-[0_8px_24px_rgb(0_0_0_/_35%)] transition-[opacity,translate] group-hover:opacity-100 group-focus-visible:opacity-100",
          tooltipPlacementClass[tooltipPlacement],
        )}
        role="tooltip"
        data-placement={tooltipPlacement}
      >
        {label}
      </span>
    </button>
  );
}
