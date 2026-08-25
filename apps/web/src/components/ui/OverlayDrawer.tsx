import type { ReactNode } from "react";
import { X } from "lucide-react";

import { Glass } from "./Glass";
import { IconButton } from "./IconButton";
import { cn } from "../../lib/cn";

type OverlayDrawerProps = {
  side: "left" | "right";
  open: boolean;
  title: string;
  eyebrow: string;
  children: ReactNode;
  onClose: () => void;
};

export function OverlayDrawer({ side, open, title, eyebrow, children, onClose }: OverlayDrawerProps) {
  return (
    <Glass
      as="aside"
      strength="strong"
      className={cn(
        "absolute top-[calc(var(--spacing-topbar)+12px)] bottom-31 z-25 flex w-[clamp(300px,24vw,356px)] flex-col overflow-hidden rounded-panel opacity-0 pointer-events-none transition-[transform,opacity] duration-200 ease-fluid max-sm:right-2.5 max-sm:bottom-16.5 max-sm:left-[calc(var(--spacing-rail)+10px)] max-sm:w-auto",
        side === "left"
          ? "left-[calc(var(--spacing-rail)+12px)] -translate-x-[calc(100%+22px)]"
          : "right-3 w-[clamp(320px,27vw,398px)] translate-x-[calc(100%+22px)]",
        open && "translate-x-0 opacity-100 pointer-events-auto",
      )}
      aria-hidden={!open}
      inert={!open ? true : undefined}
    >
      <header className="flex items-center justify-between border-b border-line px-3.75 pt-3.75 pb-3.25">
        <div>
          <p className="mb-1 text-[8px] font-extrabold tracking-[.16em] text-accent-cyan">{eyebrow}</p>
          <h2 className="m-0 text-sm font-semibold">{title}</h2>
        </div>
        <IconButton label={`关闭${title}`} icon={<X size={16} />} onClick={onClose} />
      </header>
      <div className="overflow-y-auto p-3.5 [scrollbar-color:rgb(121_175_214_/_26%)_transparent] [scrollbar-width:thin]">{children}</div>
    </Glass>
  );
}
