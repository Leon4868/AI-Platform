import type { RefObject } from "react";
import { AlertTriangle, CornerDownLeft, LoaderCircle, RotateCcw, Sparkles, Square } from "lucide-react";

import { cn } from "../../lib/cn";
import { MarqueeFrame } from "./MarqueeFrame";

export type CommandBarMode = "idle" | "focused" | "loading" | "error";

type Props = {
  mode: CommandBarMode;
  inputRef: RefObject<HTMLInputElement | null>;
  value?: string;
  currentNodeLabel?: string;
  stepLabel?: string;
  errorMessage?: string;
  transportKind?: "http" | "mock";
  onValueChange?: (value: string) => void;
  onModeChange: (mode: "idle" | "focused") => void;
  onRun: () => void;
  onStop: () => void;
  onRetry?: () => void;
};

export function MarqueeCommandBar({
  mode,
  inputRef,
  value,
  currentNodeLabel = "准备运行",
  stepLabel = "等待任务",
  errorMessage = "运行失败，请检查服务后重试",
  transportKind = "mock",
  onValueChange,
  onModeChange,
  onRun,
  onStop,
  onRetry,
}: Props) {
  const duration = mode === "loading" ? "3.6s" : "4.8s";
  return (
    <MarqueeFrame
      active={mode !== "idle"}
      duration={duration}
      tone={mode === "error" ? "error" : "accent"}
      className={cn(
        "absolute bottom-2.5 left-[calc(50%+var(--spacing-rail)/2)] z-40 w-[min(660px,calc(100vw-var(--spacing-rail)-40px))] -translate-x-1/2 rounded-[15px] shadow-[0_18px_50px_rgb(0_0_0_/_45%),var(--shadow-glow)]",
        mode === "loading" && "shadow-[0_18px_50px_rgb(0_0_0_/_45%),0_0_30px_rgb(86_217_255_/_22%)]",
        mode === "error" && "bg-accent-red/50 shadow-[0_18px_50px_rgb(0_0_0_/_45%),0_0_26px_rgb(255_127_145_/_16%)]",
      )}
      surfaceClassName="flex h-12 items-center rounded-[14px] bg-[#07131e]/96 py-0 pr-2 pl-3.5"
      aria-busy={mode === "loading"}
    >
        {mode === "loading" ? <LoaderCircle className="shrink-0 animate-[spin_.9s_linear_infinite] text-accent-cyan" size={18} /> : mode === "error" ? <AlertTriangle className="shrink-0 text-accent-red" size={18} /> : <Sparkles className="shrink-0 text-accent-cyan" size={18} />}
        <span className={cn("mr-1 rounded-full border px-1.5 py-0.5 text-[7px] font-black tracking-[.12em]", transportKind === "mock" ? "border-accent-amber/25 bg-accent-amber/9 text-accent-amber" : "border-accent-green/25 bg-accent-green/9 text-accent-green")} aria-label={`运行通道 ${transportKind}`}>
          {transportKind.toUpperCase()}
        </span>
        {mode === "loading" ? (
          <div className="flex min-w-0 flex-1 flex-col gap-0.5 px-3" aria-live="polite">
            <strong className="overflow-hidden text-[11px] text-ellipsis whitespace-nowrap">正在运行 · {currentNodeLabel}</strong>
            <small className="overflow-hidden text-[9px] text-ellipsis whitespace-nowrap text-muted">{stepLabel}</small>
          </div>
        ) : mode === "error" ? (
          <div className="flex min-w-0 flex-1 flex-col gap-0.5 px-3" aria-live="assertive">
            <strong className="text-[11px] text-[#ffd7dc]">运行中断</strong>
            <small className="overflow-hidden text-[9px] text-ellipsis whitespace-nowrap text-muted">{errorMessage}</small>
          </div>
        ) : (
          <input
            className="h-full min-w-0 flex-1 border-0 bg-transparent px-3 outline-0 placeholder:text-faint focus-visible:shadow-none"
            ref={inputRef}
            value={value}
            aria-label="Agent 指令"
            placeholder="描述下一步，或输入 / 查找节点…"
            onChange={(event) => onValueChange?.(event.target.value)}
            onFocus={() => onModeChange("focused")}
            onBlur={() => onModeChange("idle")}
            onKeyDown={(event) => {
              if (event.key === "Enter") onRun();
            }}
          />
        )}
        {mode === "loading" ? (
          <button className="liquid-button border border-accent-red/20 bg-accent-red/9 text-[#ffcbd2] [background-image:none]" type="button" onClick={onStop} aria-label="停止运行">
            <Square size={12} fill="currentColor" />
            <span>停止</span>
          </button>
        ) : mode === "error" ? (
          <button className="liquid-button text-[#07131e]" type="button" onClick={onRetry} aria-label="重试运行">
            <RotateCcw size={13} />
            <span>重试</span>
          </button>
        ) : (
          <button className="liquid-button" type="button" onMouseDown={(event) => event.preventDefault()} onClick={onRun} aria-label="运行指令">
            <span>运行</span>
            <CornerDownLeft size={14} />
          </button>
        )}
      <span className="sr-only" role="status">
        {mode === "loading" ? "Agent 正在运行" : mode === "error" ? `Agent 运行失败：${errorMessage}` : mode === "focused" ? "指令栏已聚焦" : "指令栏空闲"}
      </span>
    </MarqueeFrame>
  );
}
