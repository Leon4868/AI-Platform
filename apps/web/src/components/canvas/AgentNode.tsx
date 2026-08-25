import {
  Archive,
  Braces,
  Cpu,
  FileText,
  Layers3,
  PenLine,
  Route,
  ScanSearch,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  UserCheck,
} from "lucide-react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

import type { AgentFlowNode } from "../../lib/workflow";
import { cn } from "../../lib/cn";
import { toneVariableClass } from "../../styles/variants";

const icons = {
  archive: Archive, braces: Braces, cpu: Cpu, file: FileText, layers: Layers3,
  pen: PenLine, route: Route, scan: ScanSearch, search: Search, send: Send,
  shield: ShieldCheck, spark: Sparkles, usercheck: UserCheck,
};

const statusClasses = {
  ready: "bg-accent-green shadow-[0_0_8px_rgb(88_224_171_/_65%)]",
  active: "animate-[pulse-node_1.5s_ease-in-out_infinite] bg-accent-cyan shadow-[0_0_9px_var(--color-accent-cyan)]",
  idle: "bg-faint",
  succeeded: "bg-accent-green shadow-[0_0_8px_rgb(88_224_171_/_65%)]",
  failed: "bg-accent-red shadow-[0_0_9px_rgb(255_127_145_/_65%)]",
  cancelled: "bg-muted shadow-[0_0_7px_rgb(143_169_189_/_35%)]",
} as const;

export function AgentNode({ data, selected }: NodeProps<AgentFlowNode>) {
  const Icon = icons[data.icon as keyof typeof icons] ?? Sparkles;
  return (
    <article className={cn(
      "relative flex min-h-20.5 w-55.5 items-center rounded-[14px] border border-[#75aed6]/18 bg-linear-to-br from-[#13283d]/96 to-[#081625]/98 p-3.25 shadow-[0_16px_38px_rgb(0_0_0_/_28%),inset_0_1px_rgb(255_255_255_/_4%)] transition-[opacity,filter,transform,box-shadow] duration-200 before:absolute before:inset-y-0 before:left-0 before:w-0.75 before:rounded-l-[14px] before:bg-[var(--tone-color)] hover:-translate-y-0.5 hover:border-[color-mix(in_srgb,var(--tone-color)_48%,transparent)] hover:shadow-[0_18px_44px_rgb(0_0_0_/_35%),0_0_24px_color-mix(in_srgb,var(--tone-color)_18%,transparent)]",
      toneVariableClass[data.tone],
      selected && "-translate-y-0.5 border-[color-mix(in_srgb,var(--tone-color)_48%,transparent)] shadow-[0_18px_44px_rgb(0_0_0_/_35%),0_0_24px_color-mix(in_srgb,var(--tone-color)_18%,transparent)]",
    )}>
      <Handle type="target" position={Position.Left} />
      <div className="mr-2.75 grid size-8.5 shrink-0 place-items-center rounded-[9px] border border-[color-mix(in_srgb,var(--tone-color)_22%,transparent)] bg-[color-mix(in_srgb,var(--tone-color)_9%,transparent)] text-[var(--tone-color)]"><Icon size={17} /></div>
      <div className="flex min-w-0 flex-col gap-0.75">
        <span className="text-[9px] font-bold tracking-[.12em] text-[var(--tone-color)] uppercase">{data.category}</span>
        <strong className="text-[13px] font-bold">{data.label}</strong>
        <small className="overflow-hidden text-[10px] text-ellipsis whitespace-nowrap text-muted">{data.subtitle}</small>
      </div>
      <div className={cn("absolute top-3.25 right-3.25 size-1.75 rounded-full border border-white/35", statusClasses[data.status])} aria-label={data.status} />
      <Handle type="source" position={Position.Right} />
    </article>
  );
}
