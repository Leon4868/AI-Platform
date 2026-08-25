import { ChevronDown, Cloud, Focus, LoaderCircle, PanelLeft, PanelRight, Play, Save } from "lucide-react";

import { Glass } from "../ui/Glass";
import { IconButton } from "../ui/IconButton";
import { RunStatusPill } from "../ui/RunStatusPill";
import type { WorkflowRunViewStatus } from "../../features/workflow-run/types";

type Props = {
  focusMode: boolean;
  runStatus: WorkflowRunViewStatus;
  transportKind: "http" | "mock";
  agentName: string;
  workflowDefinitionId: string;
  canRun: boolean;
  workflowDirty: boolean;
  workflowSaving: boolean;
  onSaveWorkflow: () => void;
  onToggleLeft: () => void;
  onToggleRight: () => void;
  onToggleFocus: () => void;
  onRun: () => void;
};

export function TopBar({ focusMode, runStatus, transportKind, agentName, workflowDefinitionId, canRun, workflowDirty, workflowSaving, onSaveWorkflow, onToggleLeft, onToggleRight, onToggleFocus, onRun }: Props) {
  return (
    <Glass as="header" className="absolute top-0 right-0 left-rail z-35 flex h-topbar items-center justify-between border-x-0 border-t-0 bg-[#060f19]/72 px-3.5 shadow-none">
      <div className="flex items-center gap-2.5">
        <IconButton tooltipPlacement="bottom" label="节点库" icon={<PanelLeft size={17} />} onClick={onToggleLeft} />
        <div className="flex flex-col items-start gap-1">
          <div className="flex items-center gap-2 text-[13px]"><span className="text-muted max-sm:hidden">Agent 中心</span><i className="text-faint not-italic max-sm:hidden">/</i><strong className="font-semibold">{agentName}</strong></div>
          <p className="m-0 text-[10px] text-faint max-sm:hidden"><span className="mr-1.25 inline-block size-1.5 rounded-full bg-accent-amber shadow-[0_0_8px_var(--color-accent-amber)]" />画布预览 · 运行使用服务端已保存定义</p>
        </div>
      </div>
      <div className="flex items-center gap-1.25">
        <span className={transportKind === "mock" ? "hidden text-[8px] font-black tracking-[.12em] text-accent-amber lg:inline" : "hidden text-[8px] font-black tracking-[.12em] text-accent-green lg:inline"}>{transportKind.toUpperCase()}</span>
        <span className="hidden max-w-56 truncate rounded-lg border border-line bg-black/12 px-2.5 py-2 font-mono text-[8px] text-faint xl:block" title={workflowDefinitionId}>{workflowDefinitionId}</span>
        <RunStatusPill className="max-md:hidden" status={runStatus} />
        <button className="mr-1 hidden h-8.5 items-center gap-1.75 rounded-[9px] border border-line bg-[#569dcf]/6 px-2.75 text-muted lg:flex" type="button"><Cloud size={14} /><span>企业模型组</span><ChevronDown size={13} /></button>
        <IconButton className="max-sm:hidden" tooltipPlacement="bottom" label="聚焦模式" icon={<Focus size={17} />} active={focusMode} onClick={onToggleFocus} />
        <button className="subtle-action hidden items-center gap-1.5 lg:flex" type="button" onClick={onSaveWorkflow} disabled={!workflowDirty || workflowSaving}>
          {workflowSaving ? <LoaderCircle className="animate-[spin_.9s_linear_infinite]" size={14} /> : <Save size={14} />}
          {workflowSaving ? "保存中" : workflowDirty ? "保存画布" : "已保存"}
        </button>
        <button className="ml-1 flex h-8.5 items-center gap-1.75 rounded-[9px] bg-linear-to-br from-[#77e5ff] to-[#7890ff] px-2.75 font-bold text-[#02121b] shadow-[0_8px_24px_rgb(77_180_242_/_20%)] disabled:cursor-not-allowed disabled:opacity-45" type="button" onClick={onRun} disabled={!canRun || runStatus === "starting" || runStatus === "queued" || runStatus === "running" || runStatus === "waiting_human" || runStatus === "cancelling"} title={canRun ? "试运行已发布 Agent" : "保存并发布 Agent 后方可试运行"}><Play size={15} fill="currentColor" />试运行</button>
        <IconButton tooltipPlacement="left" label="属性面板" icon={<PanelRight size={17} />} onClick={onToggleRight} />
      </div>
    </Glass>
  );
}
