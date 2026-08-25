import { LoaderCircle } from "lucide-react";

import type { WorkflowRunViewStatus } from "../../features/workflow-run/types";
import { activeRunStatuses } from "../../features/workflow-run/types";
import { cn } from "../../lib/cn";
import { runStatusClass } from "../../styles/variants";

const labels: Record<WorkflowRunViewStatus, string> = {
  idle: "待运行",
  starting: "启动中",
  queued: "排队中",
  running: "运行中",
  waiting_human: "等待人工",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已停止",
  cancelling: "停止中",
};

export function RunStatusPill({ status, className }: { status: WorkflowRunViewStatus; className?: string }) {
  const active = activeRunStatuses.has(status);
  return (
    <span className={cn("inline-flex h-5 items-center gap-1 rounded-full border px-1.75 text-[8px] font-bold tracking-[.08em]", runStatusClass[status], className)}>
      {active ? <LoaderCircle className="animate-[spin_.9s_linear_infinite]" size={10} /> : <i className="size-1.25 rounded-full bg-current not-italic opacity-75" />}
      {labels[status]}
    </span>
  );
}
