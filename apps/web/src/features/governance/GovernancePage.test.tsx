import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GovernancePage } from "./GovernancePage";

describe("GovernancePage", () => {
  it("marks unavailable governance capabilities as preview without fake data", () => {
    render(<GovernancePage />);

    expect(screen.getByRole("heading", { name: "平台治理" })).toBeInTheDocument();
    expect(screen.getAllByText("API 待接入").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("成员席位暂无数据")).toHaveTextContent("--");
    expect(screen.getByLabelText("模型调用暂无数据")).toHaveTextContent("--");
    expect(screen.getByRole("button", { name: "配置预算（待接入）" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "创建灰度计划（待接入）" })).toBeDisabled();
    expect(screen.queryByText("HTTP")).not.toBeInTheDocument();
  });
});
