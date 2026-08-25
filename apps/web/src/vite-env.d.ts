/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_WORKFLOW_TRANSPORT?: "http" | "mock";
  readonly VITE_API_TRANSPORT?: "http" | "mock";
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DEFAULT_WORKFLOW_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
