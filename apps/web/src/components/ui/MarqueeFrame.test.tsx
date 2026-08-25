import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarqueeFrame } from "./MarqueeFrame";

describe("MarqueeFrame", () => {
  it("uses a clockwise perimeter motion contract without forcing positioning", () => {
    const { container } = render(<MarqueeFrame active duration="1.2s">运行中</MarqueeFrame>);
    const frame = container.firstChild;

    expect(frame).toHaveAttribute("data-motion", "clockwise");
    expect(frame).toHaveAttribute("data-duration", "1.2s");
    expect(frame).toHaveAttribute("data-marquee-active", "true");
    expect(frame).not.toHaveClass("relative");
    expect(container.querySelector('[data-marquee-track="perimeter"]')).toBeInTheDocument();
    expect(container.querySelector(".marquee-base-track")).toHaveAttribute("pathLength", "100");
    expect(container.querySelector(".marquee-orbit-glow")).toHaveAttribute("stroke-dasharray", "42 58");
    expect(container.querySelector(".marquee-orbit-core")).toHaveAttribute("stroke-dasharray", "30 70");
  });

  it("activates for the complete composite field while focus remains inside", () => {
    const { container } = render(
      <MarqueeFrame activateOnFocusWithin surfaceAs="label">
        <input aria-label="搜索" />
        <button type="button">清除</button>
      </MarqueeFrame>,
    );
    const frame = container.firstChild;

    fireEvent.focus(screen.getByLabelText("搜索"));
    expect(frame).toHaveAttribute("data-marquee-active", "true");
    fireEvent.blur(screen.getByLabelText("搜索"), { relatedTarget: screen.getByRole("button", { name: "清除" }) });
    expect(frame).toHaveAttribute("data-marquee-active", "true");
    fireEvent.blur(screen.getByRole("button", { name: "清除" }));
    expect(frame).toHaveAttribute("data-marquee-active", "false");
  });
});
