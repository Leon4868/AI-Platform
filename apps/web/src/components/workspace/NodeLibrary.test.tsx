import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NodeLibrary } from "./NodeLibrary";

describe("NodeLibrary", () => {
  it("filters nodes and clears the query with the all action", () => {
    render(<NodeLibrary searchInputRef={createRef()} />);

    fireEvent.change(screen.getByLabelText("搜索节点"), { target: { value: "知识" } });
    expect(screen.getByRole("button", { name: /知识检索/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /触发器/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "全部" }));
    expect(screen.getByLabelText("搜索节点")).toHaveValue("");
    expect(screen.getByRole("button", { name: /触发器/ })).toBeInTheDocument();
    expect(screen.getByLabelText("搜索节点")).toHaveFocus();
  });

  it("shows an explicit empty state for an unmatched query", () => {
    render(<NodeLibrary searchInputRef={createRef()} />);

    fireEvent.change(screen.getByLabelText("搜索节点"), { target: { value: "不存在的节点" } });
    expect(screen.getByText("未找到匹配节点")).toBeInTheDocument();
  });
});
