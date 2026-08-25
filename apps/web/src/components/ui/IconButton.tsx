import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/cn";

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  icon: ReactNode;
  active?: boolean;
  badge?: string;
};

export function IconButton({ label, icon, active, badge, className = "", ...props }: IconButtonProps) {
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
        className="pointer-events-none absolute left-11.5 z-100 w-max -translate-x-1 rounded-[7px] border border-line bg-[#0b1928] px-2 py-1.5 text-ink opacity-0 transition group-hover:translate-x-0 group-hover:opacity-100 group-focus-visible:translate-x-0 group-focus-visible:opacity-100"
        role="tooltip"
      >
        {label}
      </span>
    </button>
  );
}
