import { cn } from "../../lib/cn";
import type { AgentLifecycleStatus } from "./types";

const lifecyclePresentation: Record<AgentLifecycleStatus, { label: string; className: string }> = {
  active: {
    label: "启用中",
    className: "border-accent-green/25 bg-accent-green/9 text-accent-green",
  },
  archived: {
    label: "已归档",
    className: "border-line bg-white/3 text-muted",
  },
};

export function AgentLifecycleBadge({ status }: { status: AgentLifecycleStatus }) {
  const presentation = lifecyclePresentation[status];

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.75 text-[8px] font-bold",
        presentation.className,
      )}
    >
      {presentation.label}
    </span>
  );
}
