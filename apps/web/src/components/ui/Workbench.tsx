import type { ButtonHTMLAttributes, ReactNode } from "react";
import { AlertCircle, LoaderCircle, Wifi, WifiOff } from "lucide-react";

import { cn } from "../../lib/cn";
import { Glass } from "./Glass";

export function TransportBadge({ kind }: { kind: "http" | "mock" }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[8px] font-black tracking-[.12em]", kind === "http" ? "border-accent-green/25 bg-accent-green/9 text-accent-green" : "border-accent-amber/25 bg-accent-amber/9 text-accent-amber")}>
      {kind === "http" ? <Wifi size={10} /> : <WifiOff size={10} />}{kind.toUpperCase()}
    </span>
  );
}

export function PageShell({ eyebrow, title, description, transportKind, actions, children }: { eyebrow: string; title: string; description: string; transportKind?: "http" | "mock"; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="absolute inset-y-0 right-0 left-rail overflow-y-auto bg-[radial-gradient(circle_at_70%_0%,rgb(62_121_176_/_16%),transparent_34%)] [scrollbar-color:rgb(121_175_214_/_26%)_transparent] [scrollbar-width:thin]">
      <Glass as="header" className="sticky top-0 z-20 flex min-h-20 items-center justify-between gap-4 rounded-none border-x-0 border-t-0 bg-[#06111d]/86 px-5 py-3 shadow-none backdrop-blur-2xl max-sm:items-start max-sm:px-3.5">
        <div className="min-w-0">
          <div className="mb-1.5 flex items-center gap-2"><p className="text-[8px] font-black tracking-[.16em] text-accent-cyan">{eyebrow}</p>{transportKind ? <TransportBadge kind={transportKind} /> : null}</div>
          <h1 className="m-0 text-xl font-semibold tracking-[-.02em] max-sm:text-base">{title}</h1>
          <p className="mt-1 max-w-2xl text-[10px] leading-5 text-muted max-sm:hidden">{description}</p>
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </Glass>
      <div className="mx-auto grid w-full max-w-[1500px] gap-4 p-5 max-sm:p-3.5">{children}</div>
    </section>
  );
}

export function SectionCard({ title, description, action, children, className }: { title: string; description?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <Glass className={cn("rounded-panel p-4 shadow-[0_18px_55px_rgb(0_0_0_/_24%)]", className)}>
      <header className="mb-3.5 flex items-start justify-between gap-3 border-b border-line pb-3">
        <div><h2 className="m-0 text-sm font-semibold">{title}</h2>{description ? <p className="mt-1 text-[9px] leading-4 text-muted">{description}</p> : null}</div>
        {action}
      </header>
      {children}
    </Glass>
  );
}

export function FormField({ label, htmlFor, hint, children, className }: { label: string; htmlFor?: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      {htmlFor
        ? <label className="text-[9px] font-bold tracking-[.04em] text-muted" htmlFor={htmlFor}>{label}</label>
        : <span className="text-[9px] font-bold tracking-[.04em] text-muted">{label}</span>}
      {children}
      {hint ? <small className="text-[8px] leading-4 text-faint">{hint}</small> : null}
    </div>
  );
}

export function ActionButton({ variant = "primary", className, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  return <button className={cn("action-button", variant === "primary" ? "action-button-primary" : variant === "danger" ? "action-button-danger" : "action-button-secondary", className)} type="button" {...props}>{children}</button>;
}

const statusTone: Record<string, string> = {
  indexed: "border-accent-green/25 bg-accent-green/9 text-accent-green",
  succeeded: "border-accent-green/25 bg-accent-green/9 text-accent-green",
  running: "border-accent-cyan/25 bg-accent-cyan/9 text-accent-cyan",
  queued: "border-accent-blue/25 bg-accent-blue/9 text-accent-blue",
  waiting_human: "border-accent-amber/25 bg-accent-amber/9 text-accent-amber",
  preview: "border-accent-amber/25 bg-accent-amber/9 text-accent-amber",
  pending_api: "border-accent-blue/25 bg-accent-blue/9 text-accent-blue",
  failed: "border-accent-red/25 bg-accent-red/9 text-accent-red",
  cancelled: "border-line bg-white/3 text-muted",
  draft: "border-accent-violet/25 bg-accent-violet/9 text-accent-violet",
};

const statusLabel: Record<string, string> = {
  preview: "预览",
  pending_api: "API 待接入",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={cn("inline-flex items-center rounded-full border px-2 py-0.75 text-[8px] font-bold", statusTone[status] ?? "border-line bg-white/3 text-muted")}>{statusLabel[status] ?? status}</span>;
}

export function AsyncNotice({ pending, error, empty, emptyText = "暂无数据" }: { pending?: boolean; error?: string; empty?: boolean; emptyText?: string }) {
  if (pending) return <div className="notice-box text-accent-cyan"><LoaderCircle className="animate-[spin_.9s_linear_infinite]" size={15} />正在加载…</div>;
  if (error) return <div className="notice-box border-accent-red/20 bg-accent-red/6 text-[#ffcbd2]" role="alert"><AlertCircle size={15} />{error}</div>;
  if (empty) return <div className="notice-box text-faint">{emptyText}</div>;
  return null;
}

export function KeyValue({ label, value, mono = false }: { label: string; value?: ReactNode; mono?: boolean }) {
  return <div className="flex min-w-0 items-start justify-between gap-4 border-b border-line/55 py-2 last:border-0"><span className="shrink-0 text-[9px] text-muted">{label}</span><strong className={cn("min-w-0 break-all text-right text-[9px] font-semibold text-ink", mono && "font-mono text-accent-cyan")}>{value ?? "--"}</strong></div>;
}
