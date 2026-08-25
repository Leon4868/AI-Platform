import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { StatusBadge } from "../../components/ui/Workbench";

export const GOVERNANCE_API_STATUS = "API 待接入";

export function PreviewMetric({ icon: Icon, label, description }: { icon: LucideIcon; label: string; description: string }) {
  return (
    <article className="rounded-xl border border-line bg-black/10 p-3.5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <span className="grid size-8 place-items-center rounded-lg border border-accent-cyan/15 bg-accent-cyan/7 text-accent-cyan"><Icon size={15} /></span>
        <StatusBadge status="pending_api" />
      </div>
      <p className="text-[9px] font-semibold text-muted">{label}</p>
      <strong className="my-1.5 block font-mono text-xl font-semibold text-faint" aria-label={`${label}暂无数据`}>--</strong>
      <p className="text-[8px] leading-4 text-faint">{description}</p>
    </article>
  );
}

export function ApiPendingPanel({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line bg-white/2 p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div><strong className="text-[10px] text-ink">{title}</strong><p className="mt-1 text-[8px] leading-4 text-faint">{description}</p></div>
        <StatusBadge status="pending_api" />
      </div>
      {children ? <div className="mt-3 border-t border-line/60 pt-3">{children}</div> : null}
    </div>
  );
}

export function PreviewStatus() {
  return <div className="flex flex-wrap items-center justify-end gap-2"><StatusBadge status="preview" /><span className="text-[8px] text-faint">{GOVERNANCE_API_STATUS}</span></div>;
}
