import { useId, useState, type CSSProperties, type ElementType, type HTMLAttributes, type ReactNode } from "react";

import { cn } from "../../lib/cn";

type MarqueeFrameProps = HTMLAttributes<HTMLDivElement> & {
  active?: boolean;
  activateOnFocusWithin?: boolean;
  children: ReactNode;
  duration?: string;
  radius?: number;
  segmentLength?: number;
  surfaceAs?: ElementType;
  surfaceClassName?: string;
  tone?: "accent" | "error";
};

export function MarqueeFrame({
  active = false,
  activateOnFocusWithin = false,
  children,
  className,
  duration = "4.8s",
  onBlurCapture,
  onFocusCapture,
  radius = 14,
  segmentLength = 42,
  style,
  surfaceAs,
  surfaceClassName,
  tone = "accent",
  ...props
}: MarqueeFrameProps) {
  const [focusWithin, setFocusWithin] = useState(false);
  const marqueeActive = active || (activateOnFocusWithin && focusWithin);
  const Surface = surfaceAs ?? "div";
  const glowLength = Math.min(Math.max(segmentLength, 1), 99);
  const coreLength = Math.max(glowLength - 12, 1);
  const marqueeId = useId().replaceAll(":", "");
  const baseGradientId = `marquee-base-${marqueeId}`;
  const highlightGradientId = `marquee-highlight-${marqueeId}`;

  return (
    <div
      className={cn(
        "isolate overflow-hidden bg-line-strong p-px",
        tone === "error"
          ? "[--marquee-end:var(--color-accent-violet)] [--marquee-start:var(--color-accent-red)]"
          : "[--marquee-end:var(--color-accent-violet)] [--marquee-start:var(--color-accent-cyan)]",
        className,
      )}
      style={{ ...style, "--marquee-duration": duration } as CSSProperties}
      data-duration={duration}
      data-marquee-active={marqueeActive}
      data-motion="clockwise"
      onFocusCapture={(event) => {
        if (activateOnFocusWithin) setFocusWithin(true);
        onFocusCapture?.(event);
      }}
      onBlurCapture={(event) => {
        if (activateOnFocusWithin && !event.currentTarget.contains(event.relatedTarget)) setFocusWithin(false);
        onBlurCapture?.(event);
      }}
      {...props}
    >
      <svg
        className="pointer-events-none absolute inset-0 z-0 size-full overflow-visible"
        aria-hidden="true"
        data-marquee-track="perimeter"
      >
        <defs>
          <linearGradient id={baseGradientId} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="var(--marquee-start)" stopOpacity="0.7" />
            <stop offset="0.5" stopColor="var(--marquee-end)" stopOpacity="0.45" />
            <stop offset="1" stopColor="var(--marquee-start)" stopOpacity="0.7" />
          </linearGradient>
          <linearGradient id={highlightGradientId} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="var(--marquee-start)" stopOpacity="0.3" />
            <stop offset="0.48" stopColor="var(--marquee-start)" />
            <stop offset="0.72" stopColor="var(--marquee-end)" />
            <stop offset="1" stopColor="var(--marquee-end)" stopOpacity="0.3" />
          </linearGradient>
        </defs>
        <rect
          className="marquee-base-track [height:calc(100%_-_2px)] [width:calc(100%_-_2px)]"
          x="1"
          y="1"
          rx={radius}
          pathLength="100"
          fill="none"
          stroke={`url(#${baseGradientId})`}
        />
        <rect
          className="marquee-orbit-track marquee-orbit-glow [height:calc(100%_-_2px)] [width:calc(100%_-_2px)]"
          x="1"
          y="1"
          rx={radius}
          pathLength="100"
          fill="none"
          stroke={`url(#${highlightGradientId})`}
          strokeDasharray={`${glowLength} ${100 - glowLength}`}
        />
        <rect
          className="marquee-orbit-track marquee-orbit-core [height:calc(100%_-_2px)] [width:calc(100%_-_2px)]"
          x="1"
          y="1"
          rx={radius}
          pathLength="100"
          fill="none"
          stroke={`url(#${highlightGradientId})`}
          strokeDasharray={`${coreLength} ${100 - coreLength}`}
        />
      </svg>
      <Surface className={cn("relative z-1", surfaceClassName)}>{children}</Surface>
    </div>
  );
}
