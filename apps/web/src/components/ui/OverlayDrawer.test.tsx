import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OverlayDrawer } from "./OverlayDrawer";

describe("OverlayDrawer", () => {
  it("keeps a closed drawer outside the accessibility interaction tree", () => {
    const { container } = render(
      <OverlayDrawer side="left" open={false} title="节点库" eyebrow="LIBRARY" onClose={vi.fn()}>
        节点内容
      </OverlayDrawer>,
    );
    const drawer = container.querySelector("aside");
    expect(drawer).toHaveAttribute("aria-hidden", "true");
    expect(drawer).toHaveClass("pointer-events-none");
    expect(drawer).not.toHaveClass("pointer-events-auto");
    expect(drawer).toHaveClass("-translate-x-[calc(100%+22px)]");
    expect(drawer).not.toHaveClass("translate-x-0");
  });

  it("captures pointer input while open instead of passing it to the canvas", () => {
    const onWorkspacePointerDown = vi.fn();
    const { container } = render(
      <div onPointerDown={onWorkspacePointerDown}>
        <OverlayDrawer side="left" open title="节点库" eyebrow="LIBRARY" onClose={vi.fn()}>
          <button type="button">节点内容</button>
        </OverlayDrawer>
      </div>,
    );
    const drawer = container.querySelector("aside");
    expect(drawer).toHaveClass("pointer-events-auto");
    expect(drawer).not.toHaveClass("pointer-events-none");
    expect(drawer).toHaveClass("translate-x-0");
    expect(drawer).not.toHaveClass("-translate-x-[calc(100%+22px)]");

    fireEvent.pointerDown(screen.getByRole("button", { name: "节点内容" }));
    expect(onWorkspacePointerDown).not.toHaveBeenCalled();
  });

  it("provides an explicit close action", () => {
    const onClose = vi.fn();
    render(
      <OverlayDrawer side="right" open title="节点属性" eyebrow="INSPECTOR" onClose={onClose}>
        属性内容
      </OverlayDrawer>,
    );
    const drawer = screen.getByRole("complementary");
    expect(drawer).toHaveClass("translate-x-0");
    expect(drawer).not.toHaveClass("translate-x-[calc(100%+22px)]");
    fireEvent.click(screen.getByRole("button", { name: "关闭节点属性" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
