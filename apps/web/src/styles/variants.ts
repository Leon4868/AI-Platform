export const toneVariableClass = {
  cyan: "[--tone-color:var(--color-accent-cyan)]",
  blue: "[--tone-color:var(--color-accent-blue)]",
  violet: "[--tone-color:var(--color-accent-violet)]",
  green: "[--tone-color:var(--color-accent-green)]",
  amber: "[--tone-color:var(--color-accent-amber)]",
} as const;

export const runStatusClass = {
  idle: "border-line bg-white/3 text-faint",
  starting: "border-accent-cyan/25 bg-accent-cyan/9 text-accent-cyan",
  queued: "border-accent-blue/25 bg-accent-blue/9 text-accent-blue",
  running: "border-accent-cyan/25 bg-accent-cyan/9 text-accent-cyan",
  waiting_human: "border-accent-amber/25 bg-accent-amber/9 text-accent-amber",
  succeeded: "border-accent-green/25 bg-accent-green/9 text-accent-green",
  failed: "border-accent-red/25 bg-accent-red/9 text-accent-red",
  cancelled: "border-line bg-white/3 text-muted",
  cancelling: "border-accent-amber/25 bg-accent-amber/9 text-accent-amber",
} as const;
