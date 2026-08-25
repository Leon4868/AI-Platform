import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FakeEnterpriseApi } from "../../test/FakeEnterpriseApi";
import { DocumentPage } from "./DocumentPage";

describe("DocumentPage", () => {
  it("creates, polls to terminal and displays the draft asset boundary", async () => {
    const api = new FakeEnterpriseApi();
    render(<DocumentPage api={api} pollIntervalMs={1} />);
    await screen.findByText("制度库");

    fireEvent.change(screen.getByLabelText("Workflow Definition ID"), { target: { value: "workflow-1" } });
    fireEvent.change(screen.getByLabelText("文档标题"), { target: { value: "产品周报" } });
    fireEvent.change(screen.getByLabelText("生成指令"), { target: { value: "总结本周进展" } });
    fireEvent.click(screen.getByLabelText("制度库"));
    fireEvent.click(screen.getByRole("button", { name: "生成 Markdown 草稿" }));

    expect(await screen.findByText("产品周报.md")).toBeInTheDocument();
    expect(api.createdTask).toMatchObject({ workflowDefinitionId: "workflow-1", knowledgeBaseIds: ["kb-1"], outputFormat: "markdown" });
    expect(api.taskReads).toBe(2);
    expect(screen.getByText("当前存储后端不支持浏览器下载")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("succeeded").length).toBeGreaterThan(0));
  });
});
