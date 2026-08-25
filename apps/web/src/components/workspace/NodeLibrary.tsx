import { Search } from "lucide-react";

import { paletteItems } from "../../lib/workflow";
import { Glass, PanelSection } from "../ui/Glass";
import { TextAction } from "../ui/TextAction";
import { toneVariableClass } from "../../styles/variants";

export function NodeLibrary() {
  return (
    <>
      <label className="flex h-9.5 items-center rounded-[10px] border border-line bg-canvas/30 px-2.5 text-muted">
        <Search size={15} />
        <input className="ml-2 min-w-0 flex-1 border-0 bg-transparent outline-none placeholder:text-faint" aria-label="搜索节点" placeholder="搜索节点或工具" />
        <kbd className="rounded-[5px] border border-line bg-white/2.5 px-1.25 py-0.5 text-[9px] text-faint">/</kbd>
      </label>
      <PanelSection title="常用节点" action={<TextAction>全部</TextAction>}>
        <div className="grid grid-cols-2 gap-2">
          {paletteItems.map((item) => (
            <button className={`grid grid-cols-[31px_1fr] grid-rows-2 gap-x-2 rounded-[10px] border border-line bg-[#559ac9]/3.5 p-2.5 text-left hover:border-[var(--tone-color)] hover:bg-[color-mix(in_srgb,var(--tone-color)_7%,transparent)] ${toneVariableClass[item.tone]}`} type="button" key={item.label}>
              <span className="row-span-2 grid size-7.75 place-items-center rounded-lg bg-[color-mix(in_srgb,var(--tone-color)_10%,transparent)] text-[11px] font-extrabold text-[var(--tone-color)]">{item.label.slice(0, 1)}</span>
              <strong className="text-[11px]">{item.label}</strong>
              <small className="mt-0.5 text-[9px] text-faint">拖入画布</small>
            </button>
          ))}
        </div>
      </PanelSection>
      <PanelSection title="运行资源">
        <Glass className="rounded-[10px] bg-canvas/20 p-2.75 shadow-none">
          <div className="flex justify-between"><span className="text-[10px] text-muted">模型调用额度</span><strong className="text-[11px]">72%</strong></div>
          <div className="my-2.25 h-1 w-full overflow-hidden rounded-sm bg-[#75b6e1]/12"><i className="block h-full w-[72%] rounded-[inherit] bg-linear-to-r from-accent-cyan to-accent-blue" /></div>
          <small className="text-[10px] text-muted">本月剩余 1.42M Tokens</small>
        </Glass>
        <Glass className="mt-2 rounded-[10px] bg-canvas/20 p-2.75 shadow-none">
          <div className="flex justify-between"><span className="text-[10px] text-muted">知识文档</span><strong className="text-[11px]">2,486</strong></div>
          <small className="text-[10px] text-muted">最近同步于 10:24</small>
        </Glass>
      </PanelSection>
    </>
  );
}
