import { Bot, Boxes, Database, FilePenLine, HelpCircle, Settings, Sparkles } from "lucide-react";

import { IconButton } from "../ui/IconButton";

export type AppSection = "agent" | "knowledge" | "documents" | "assets";

const primary: Array<{ id: AppSection; label: string; icon: typeof Bot }> = [
  { id: "agent", label: "Agent 编排", icon: Bot },
  { id: "knowledge", label: "知识库", icon: Database },
  { id: "documents", label: "文档生产", icon: FilePenLine },
  { id: "assets", label: "企业资产", icon: Boxes },
];

export function IconRail({ active, onNavigate }: { active: AppSection; onNavigate: (section: AppSection) => void }) {
  return (
    <nav className="absolute inset-y-0 left-0 z-50 flex w-rail flex-col items-center border-r border-line bg-canvas/92 px-2 py-3 backdrop-blur-lg" aria-label="主导航">
      <div className="mb-5 grid size-10 place-items-center rounded-[13px] bg-linear-to-br from-[#8beaff] to-[#6684ff] text-[#03111b] shadow-[0_0_25px_rgb(86_217_255_/_30%)]" aria-label="Nebula AI"><Sparkles size={20} /></div>
      <div className="flex w-full flex-col gap-1.75">
        {primary.map(({ id, label, icon: Icon }) => (
          <IconButton key={id} label={label} icon={<Icon size={19} />} active={active === id} onClick={() => onNavigate(id)} aria-current={active === id ? "page" : undefined} />
        ))}
      </div>
      <div className="mt-auto flex w-full flex-col gap-1.75">
        <IconButton label="帮助" icon={<HelpCircle size={19} />} />
        <IconButton label="系统设置" icon={<Settings size={19} />} />
        <button className="mt-2 size-8.5 rounded-full border border-accent-cyan/30 bg-linear-to-br from-[#17445e] to-[#222e59] text-[10px] font-bold text-[#bfeeff]" type="button" aria-label="账户：林舟">LZ</button>
      </div>
    </nav>
  );
}
