import type {
  Asset,
  CreateDocumentTaskInput,
  CreateKnowledgeBaseInput,
  DataScope,
  DocumentTask,
  EnterpriseApi,
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeSearchResult,
  SecurityLevel,
} from "./types";
import { normalizeProblemResponse } from "../../lib/problem-details";

export class EnterpriseApiError extends Error {
  readonly code: string;
  readonly status?: number;

  constructor(message: string, code = "api_error", status?: number) {
    super(message);
    this.name = "EnterpriseApiError";
    this.code = code;
    this.status = status;
  }
}

function idempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function inferredMimeType(file: File) {
  if (file.type) return file.type;
  const extension = file.name.toLowerCase().split(".").pop();
  if (extension === "md") return "text/markdown";
  if (extension === "html" || extension === "htm") return "text/html";
  return "text/plain";
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  const problem = await normalizeProblemResponse(response, `请求失败（${response.status}）`);
  throw new EnterpriseApiError(
    problem.message,
    problem.code,
    problem.status,
  );
}

export class HttpEnterpriseApi implements EnterpriseApi {
  readonly kind = "http" as const;
  private readonly baseUrl: string;

  constructor(baseUrl = "/api") {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, { credentials: "same-origin", ...init });
    return parseResponse<T>(response);
  }

  private async idempotentRequest<T>(path: string, init: RequestInit): Promise<T> {
    const key = idempotencyKey();
    const headers = { ...(init.headers as Record<string, string> | undefined), "Idempotency-Key": key };
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const response = await fetch(`${this.baseUrl}${path}`, { credentials: "same-origin", ...init, headers });
        return await parseResponse<T>(response);
      } catch (error) {
        const aborted = error instanceof DOMException && error.name === "AbortError";
        if (aborted || error instanceof EnterpriseApiError || !(error instanceof TypeError) || attempt === 1) throw error;
      }
    }
    throw new EnterpriseApiError("请求重试失败", "transport_retry_exhausted");
  }

  listKnowledgeBases(signal?: AbortSignal) {
    return this.request<KnowledgeBase[]>("/v1/knowledge-bases", { signal });
  }

  createKnowledgeBase(input: CreateKnowledgeBaseInput, signal?: AbortSignal) {
    return this.idempotentRequest<KnowledgeBase>("/v1/knowledge-bases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal,
    });
  }

  uploadKnowledgeDocument(knowledgeBaseId: string, file: File, dataScope: DataScope, securityLevel: SecurityLevel, signal?: AbortSignal, projectId?: string) {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("dataScope", dataScope);
    form.append("securityLevel", securityLevel);
    if (projectId) form.append("projectId", projectId);
    return this.idempotentRequest<KnowledgeDocument>(`/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents`, {
      method: "POST",
      headers: { "X-Filename": file.name },
      body: form,
      signal,
    }).then((document) => ({ ...document, mimeType: document.mimeType || inferredMimeType(file) }));
  }

  searchKnowledge(knowledgeBaseId: string, query: string, topK: number, signal?: AbortSignal) {
    return this.request<KnowledgeSearchResult>(`/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, topK, filters: {} }),
      signal,
    });
  }

  createDocumentTask(input: CreateDocumentTaskInput, signal?: AbortSignal) {
    return this.idempotentRequest<DocumentTask>("/v1/document-tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal,
    });
  }

  getDocumentTask(taskId: string, signal?: AbortSignal) {
    return this.request<DocumentTask>(`/v1/document-tasks/${encodeURIComponent(taskId)}`, { signal });
  }

  getAsset(assetId: string, signal?: AbortSignal) {
    return this.request<Asset>(`/v1/assets/${encodeURIComponent(assetId)}`, { signal });
  }
}
