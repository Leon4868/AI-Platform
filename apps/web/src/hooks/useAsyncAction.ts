import { useCallback, useEffect, useRef, useState } from "react";

type AsyncState<TResult> = {
  status: "idle" | "pending" | "success" | "error";
  data?: TResult;
  error?: string;
};

export function useAsyncAction<TInput, TResult>(action: (input: TInput, signal: AbortSignal) => Promise<TResult>) {
  const [state, setState] = useState<AsyncState<TResult>>({ status: "idle" });
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const run = useCallback(async (input: TInput) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ status: "pending" });
    try {
      const data = await action(input, controller.signal);
      if (controller.signal.aborted) return undefined;
      setState({ status: "success", data });
      return data;
    } catch (error) {
      if (!controller.signal.aborted) {
        setState({ status: "error", error: error instanceof Error ? error.message : "操作失败" });
      }
      return undefined;
    }
  }, [action]);

  const reset = useCallback(() => {
    controllerRef.current?.abort();
    setState({ status: "idle" });
  }, []);

  return { ...state, run, reset, pending: state.status === "pending" };
}
