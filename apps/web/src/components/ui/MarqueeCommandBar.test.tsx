import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MarqueeCommandBar } from "./MarqueeCommandBar";

describe("MarqueeCommandBar", () => {
  it("uses the 2.8 second focused marquee", () => {
    const { container } = render(
      <MarqueeCommandBar mode="focused" inputRef={createRef()} onModeChange={vi.fn()} onRun={vi.fn()} onStop={vi.fn()} />,
    );
    expect(container.firstChild).toHaveAttribute("data-duration", "2.8s");
    expect(container.firstChild).toHaveAttribute("aria-busy", "false");
  });

  it("uses the 1.2 second loading marquee and exposes busy state", () => {
    const onStop = vi.fn();
    const { container } = render(
      <MarqueeCommandBar mode="loading" inputRef={createRef()} onModeChange={vi.fn()} onRun={vi.fn()} onStop={onStop} />,
    );
    expect(container.firstChild).toHaveAttribute("data-duration", "1.2s");
    expect(container.firstChild).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Agent 正在运行");
    expect(screen.getByText("正在运行 · 准备运行")).toBeInTheDocument();
    expect(screen.getByLabelText("运行通道 mock")).toHaveTextContent("MOCK");
    expect(screen.getByRole("button", { name: "停止运行" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停止运行" }));
    expect(onStop).toHaveBeenCalledOnce();
  });

  it("runs the command with Enter", () => {
    const onRun = vi.fn();
    render(<MarqueeCommandBar mode="focused" inputRef={createRef()} onModeChange={vi.fn()} onRun={onRun} onStop={vi.fn()} />);
    fireEvent.keyDown(screen.getByLabelText("Agent 指令"), { key: "Enter" });
    expect(onRun).toHaveBeenCalledOnce();
  });

  it("offers an explicit retry after a run error", () => {
    const onRetry = vi.fn();
    render(
      <MarqueeCommandBar
        mode="error"
        errorMessage="模型服务超时"
        inputRef={createRef()}
        onModeChange={vi.fn()}
        onRun={vi.fn()}
        onStop={vi.fn()}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("模型服务超时");
    fireEvent.click(screen.getByRole("button", { name: "重试运行" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
