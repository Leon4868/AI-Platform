import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FakeEnterpriseApi } from "../../test/FakeEnterpriseApi";
import type { CreateDocumentTaskInput } from "../enterprise-api/types";
import { useDocumentTask } from "./useDocumentTask";

const input: CreateDocumentTaskInput = { title: "周报", workflowDefinitionId: "wf", knowledgeBaseIds: [], logicalModelCode: "model", instructions: "总结", sources: [], outputFormat: "markdown" };

describe("useDocumentTask", () => {
  it("stops polling at a failed terminal state", async () => {
    class FailedApi extends FakeEnterpriseApi {
      override async getDocumentTask() { this.taskReads += 1; return { taskId: "task-1", status: "failed" as const, draftAssetId: "partial-asset", workflowRunId: "run-1", traceId: "trace-1", citations: [], createdAt: "2026-08-25T00:00:00Z" }; }
    }
    const api = new FailedApi();
    const { result } = renderHook(() => useDocumentTask(api, 1));
    await act(async () => { await result.current.start(input); });
    expect(result.current.task?.status).toBe("failed");
    expect(api.taskReads).toBe(1);
    expect(api.assetReads).toBe(0);
  });

  it("aborts an in-flight create request on unmount", async () => {
    class PendingApi extends FakeEnterpriseApi {
      signal?: AbortSignal;
      override async createDocumentTask(_input: CreateDocumentTaskInput, signal?: AbortSignal) {
        this.signal = signal;
        return new Promise<never>((_resolve, reject) => signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true }));
      }
    }
    const api = new PendingApi();
    const { result, unmount } = renderHook(() => useDocumentTask(api, 1));
    act(() => { void result.current.start(input); });
    await waitFor(() => expect(api.signal).toBeDefined());
    unmount();
    expect(api.signal?.aborted).toBe(true);
  });
});
