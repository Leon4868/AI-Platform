import { CheckCircle2, ChevronDown, Code2, Copy, MoreHorizontal } from "lucide-react";

import type { AgentNodeData } from "../../lib/workflow";
import { Glass, PanelSection, PropertyRow } from "../ui/Glass";
import { IconButton } from "../ui/IconButton";
import { cn } from "../../lib/cn";
import { toneVariableClass } from "../../styles/variants";
import { TextAction } from "../ui/TextAction";

const toggleTrack = "relative h-4.25 w-7.5 rounded-[10px] bg-[#75b6e1]/16 transition before:absolute before:top-0.75 before:left-0.75 before:size-2.75 before:rounded-full before:bg-muted before:transition peer-checked:bg-accent-cyan/28 peer-checked:before:left-4 peer-checked:before:bg-accent-cyan";

export function Inspector({ data }: { data?: AgentNodeData }) {
  if (!data) return <p className="text-center text-muted">选择节点以查看属性</p>;
  return (
    <>
      <Glass className={cn("rounded-xl p-3.25 shadow-none", toneVariableClass[data.tone])}>
        <div className="flex items-center justify-between">
          <span className="text-[9px] font-extrabold tracking-[.1em] text-[var(--tone-color)]">{data.category}</span>
          <IconButton tooltipPlacement="left" label="更多操作" icon={<MoreHorizontal size={16} />} />
        </div>
        <h3 className="mt-1.5 mb-1 text-base font-semibold">{data.label}</h3>
        <p className="m-0 text-[11px] text-muted">{data.subtitle}</p>
        <div className="mt-3.25 flex items-center gap-1.5 border-t border-line pt-2.5 text-[10px] text-accent-green"><CheckCircle2 size={14} />配置有效，可以运行</div>
      </Glass>
      <PanelSection title="基础配置">
        <PropertyRow label="逻辑模型" value={data.model ?? "系统默认"} accent />
        <PropertyRow label="超时时间" value="30 秒" />
        <PropertyRow label="失败重试" value="2 次" />
      </PanelSection>
      <PanelSection title="输入映射" action={<TextAction>编辑</TextAction>}>
        <div className="flex h-9 items-center gap-1.75 rounded-[9px] border border-line bg-canvas/30 pl-2.25 text-accent-violet">
          <Code2 size={14} />
          <code className="min-w-0 flex-1 overflow-hidden text-[9px] text-ellipsis text-[#c8d8e5]">{"{{ steps.prompt.output }}"}</code>
          <IconButton className="size-7.5" tooltipPlacement="left" label="复制映射" icon={<Copy size={13} />} />
        </div>
      </PanelSection>
      <PanelSection title="高级设置">
        <button className="flex min-h-10.5 w-full items-center justify-between border-0 border-b border-line/50 bg-transparent p-0 text-left" type="button"><span className="text-[10px] text-muted">输出格式</span><strong className="ml-auto text-[10px]">结构化 JSON</strong><ChevronDown className="ml-1.5 text-faint" size={14} /></button>
        <label className="flex min-h-10.5 w-full items-center justify-between border-b border-line/50"><span className="flex flex-col gap-0.5"><strong className="text-[10px] text-muted">流式输出</strong><small className="text-[9px] text-faint">逐段返回生成结果</small></span><input className="peer absolute opacity-0" type="checkbox" defaultChecked /><i className={toggleTrack} /></label>
        <label className="flex min-h-10.5 w-full items-center justify-between border-b border-line/50"><span className="flex flex-col gap-0.5"><strong className="text-[10px] text-muted">记录完整 Trace</strong><small className="text-[9px] text-faint">用于审计与问题定位</small></span><input className="peer absolute opacity-0" type="checkbox" defaultChecked /><i className={toggleTrack} /></label>
      </PanelSection>
    </>
  );
}
