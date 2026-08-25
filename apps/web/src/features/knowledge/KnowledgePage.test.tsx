import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FakeEnterpriseApi } from "../../test/FakeEnterpriseApi";
import { KnowledgePage } from "./KnowledgePage";

describe("KnowledgePage", () => {
  it("creates, uploads and searches through the real API boundary", async () => {
    const api = new FakeEnterpriseApi();
    render(<KnowledgePage api={api} />);
    await screen.findAllByText("制度库");

    fireEvent.change(screen.getByLabelText("检索内容"), { target: { value: "年假" } });
    fireEvent.click(screen.getByRole("button", { name: "检索" }));
    expect(await screen.findByText("员工年假为十天。")).toBeInTheDocument();
    expect(api.searchedQuery).toBe("年假");

    const file = new File(["# 年假制度"], "年假.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("知识文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "上传并索引" }));
    expect(await screen.findByText("年假.md")).toBeInTheDocument();
    expect(api.uploadedFile).toBe(file);

    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "新知识库" } });
    fireEvent.click(screen.getByRole("button", { name: "创建知识库" }));
    await waitFor(() => expect(api.createdKnowledge?.name).toBe("新知识库"));
  });
});
