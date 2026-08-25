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
    expect(container.querySelector("aside")).toHaveAttribute("aria-hidden", "true");
  });

  it("provides an explicit close action", () => {
    const onClose = vi.fn();
    render(
      <OverlayDrawer side="right" open title="节点属性" eyebrow="INSPECTOR" onClose={onClose}>
        属性内容
      </OverlayDrawer>,
    );
    fireEvent.click(screen.getByRole("button", { name: "关闭节点属性" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
