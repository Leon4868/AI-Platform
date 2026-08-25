import { useCallback, useEffect, useRef, useState } from "react";

import type { Asset, CreateDocumentTaskInput, DocumentTask, EnterpriseApi } from "../enterprise-api/types";

const terminal = new Set(["succeeded", "failed", "cancelled"]);

type State = { status: "idle" | "creating" | "polling" | "complete" | "error"; task?: DocumentTask; asset?: Asset; error?: string; assetError?: string };

export function useDocumentTask(api: EnterpriseApi, pollIntervalMs = 900) {
  const [state, setState] = useState<State>({ status: "idle" });
  const controllerRef = useRef<AbortController | null>(null);
  useEffect(() => () => controllerRef.current?.abort(), []);

  const wait = useCallback((signal: AbortSignal) => new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, pollIntervalMs);
    signal.addEventListener("abort", () => { window.clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
  }), [pollIntervalMs]);

  const start = useCallback(async (input: CreateDocumentTaskInput) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ status: "creating" });
    try {
      let task = await api.createDocumentTask(input, controller.signal);
      setState({ status: terminal.has(task.status) ? "complete" : "polling", task });
      let attempts = 0;
      while (!terminal.has(task.status) && attempts < 180) {
        await wait(controller.signal);
        task = await api.getDocumentTask(task.taskId, controller.signal);
        attempts += 1;
        setState({ status: terminal.has(task.status) ? "complete" : "polling", task });
      }
      if (!terminal.has(task.status)) throw new Error("文档任务等待超时，请稍后通过任务 ID 查询");
      if (task.status === "succeeded" && task.draftAssetId) {
        try {
          const asset = await api.getAsset(task.draftAssetId, controller.signal);
          setState({ status: "complete", task, asset });
        } catch (error) {
          if (!controller.signal.aborted) setState({ status: "complete", task, assetError: error instanceof Error ? error.message : "草稿资产读取失败" });
        }
      }
      return task;
    } catch (error) {
      if (!controller.signal.aborted) setState((current) => ({ ...current, status: "error", error: error instanceof Error ? error.message : "文档任务失败" }));
      return undefined;
    }
  }, [api, wait]);

  const reset = useCallback(() => { controllerRef.current?.abort(); setState({ status: "idle" }); }, []);
  return { ...state, start, reset, pending: state.status === "creating" || state.status === "polling" };
}
