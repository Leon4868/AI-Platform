import { useCallback, useEffect } from "react";

import { useAsyncAction } from "../../hooks/useAsyncAction";
import type { EnterpriseApi } from "./types";

export function useKnowledgeBases(api: EnterpriseApi) {
  const load = useCallback((_input: void, signal: AbortSignal) => api.listKnowledgeBases(signal), [api]);
  const resource = useAsyncAction(load);

  useEffect(() => { void resource.run(undefined); }, [resource.run]);
  return { ...resource, refresh: () => resource.run(undefined), items: resource.data ?? [] };
}
