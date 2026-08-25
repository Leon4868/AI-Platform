import { HttpEnterpriseApi } from "./client";
import { MockEnterpriseApi } from "./mock";

export function createConfiguredEnterpriseApi() {
  const mode = import.meta.env.VITE_API_TRANSPORT ?? "http";
  if (mode === "http") return new HttpEnterpriseApi(import.meta.env.VITE_API_BASE_URL ?? "/api");
  if (mode === "mock") return new MockEnterpriseApi();
  throw new Error(`VITE_API_TRANSPORT 必须是 http 或 mock，当前为 ${mode}`);
}
