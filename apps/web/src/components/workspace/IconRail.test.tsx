import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { IconRail } from "./IconRail";

describe("IconRail", () => {
  it("switches among the employee and governance workspaces", () => {
    const onNavigate = vi.fn();
    render(<IconRail active="agent" onNavigate={onNavigate} />);
    expect(screen.getByRole("button", { name: "Agent 编排" })).toHaveAttribute("aria-current", "page");
    fireEvent.click(screen.getByRole("button", { name: "知识库" }));
    fireEvent.click(screen.getByRole("button", { name: "文档生产" }));
    fireEvent.click(screen.getByRole("button", { name: "企业资产" }));
    fireEvent.click(screen.getByRole("button", { name: "平台治理" }));
    expect(onNavigate.mock.calls.map(([value]) => value)).toEqual(["knowledge", "documents", "assets", "governance"]);
  });
});
