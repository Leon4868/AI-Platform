import { Activity, Check, CircleDashed, CircleX, LoaderCircle, Timer } from "lucide-react";

import type { NodeRunStatus, WorkflowRunSnapshot, WorkflowRunViewStatus } from "../../features/workflow-run/types";
import { cn } from "../../lib/cn";
import { Glass } from "./Glass";
import { RunStatusPill } from "./RunStatusPill";

type Props = {
  snapshot?: WorkflowRunSnapshot;
  status: WorkflowRunViewStatus;
  nodeLabels: Record<string, string>;
};

function durationLabel(snapshot?: WorkflowRunSnapshot): string {
  if (!snapshot?.startedAt) return "--";
  const end = snapshot.finishedAt ? Date.parse(snapshot.finishedAt) : Date.now();
  const duration = Math.max(0, end - Date.parse(snapshot.startedAt));
  if (duration < 1000) return `${duration}ms`;
  return `${(duration / 1000).toFixed(2)}s`;
}

function StatusIcon({ status }: { status: NodeRunStatus }) {
  if (status === "succeeded" || status === "skipped") return <Check size={11} />;
  if (status === "running") return <LoaderCircle className="animate-[spin_.9s_linear_infinite]" size={11} />;
  if (status === "failed" || status === "cancelled") return <CircleX size={11} />;
  return <CircleDashed size={11} />;
}

function visibleSteps(snapshot?: WorkflowRunSnapshot) {
  const steps = snapshot?.nodeRuns ?? [];
  if (steps.length <= 5) return steps;
  const activeIndex = steps.findIndex((step) => step.status === "running" || step.status === "waiting_human" || step.status === "failed");
  const pivot = activeIndex < 0 ? steps.findIndex((step) => step.status === "pending") : activeIndex;
  const start = Math.max(0, Math.min(steps.length - 5, pivot - 2));
  return steps.slice(start, start + 5);
}

export function TraceBar({ snapshot, status, nodeLabels }: Props) {
  const steps = visibleSteps(snapshot);
  return (
    <Glass className="absolute right-4.5 bottom-16.5 left-[calc(var(--spacing-rail)+18px)] z-30 flex h-trace items-center rounded-[14px] bg-[#081421]/82 px-3.5 shadow-[0_10px_40px_rgb(0_0_0_/_35%)] max-sm:hidden" aria-label="运行链路">
      <div className="flex w-39 shrink-0 flex-col gap-0.75">
        <span className="flex items-center gap-1.25 text-[8px] font-extrabold tracking-[.12em] text-accent-green"><Activity size={14} /> TRACE</span>
        <strong className="overflow-hidden font-mono text-[10px] text-ellipsis whitespace-nowrap">{snapshot?.traceId ?? "尚未生成"}</strong>
      </div>
      <div className="flex min-w-0 flex-1 items-center justify-center">
        {steps.length === 0 ? (
          <span className="text-[9px] text-faint">运行后将在这里展示真实节点链路</span>
        ) : steps.map((step, index) => (
          <div className="flex min-w-0 items-center text-[9px] text-muted" key={step.nodeId}>
            <span className={cn(
              "mr-1.25 grid size-4.5 shrink-0 place-items-center rounded-full border",
              step.status === "succeeded" || step.status === "skipped" ? "border-transparent bg-accent-green text-[#032418]" :
                step.status === "running" ? "border-accent-cyan/30 bg-accent-cyan/10 text-accent-cyan" :
                  step.status === "failed" ? "border-accent-red/30 bg-accent-red/10 text-accent-red" :
                    step.status === "cancelled" ? "border-line bg-white/3 text-muted" : "border-line text-faint",
            )}><StatusIcon status={step.status} /></span>
            <span className="max-w-20 overflow-hidden text-ellipsis whitespace-nowrap">{nodeLabels[step.nodeId] ?? step.nodeId}</span>
            {index < steps.length - 1 ? <i className="mx-2 h-px w-[clamp(8px,2vw,34px)] shrink-0 bg-linear-to-r from-accent-green/50 to-line max-lg:w-2" /> : null}
          </div>
        ))}
      </div>
      <div className="flex w-39 shrink-0 items-center justify-end gap-1.5 text-[9px] text-faint">
        <RunStatusPill status={status} />
        <Timer size={13} />
        <strong className="font-mono text-[10px] text-accent-cyan">{durationLabel(snapshot)}</strong>
      </div>
    </Glass>
  );
}
