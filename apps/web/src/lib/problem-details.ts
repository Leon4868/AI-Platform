export type NormalizedProblem = {
  code: string;
  message: string;
  status: number;
};

export async function normalizeProblemResponse(
  response: Response,
  fallbackMessage: string,
): Promise<NormalizedProblem> {
  let body: Record<string, unknown> = {};
  try {
    body = await response.json() as Record<string, unknown>;
  } catch {
    // Gateways may return HTML or plain text; preserve a safe HTTP fallback.
  }

  return {
    code: problemCode(body, response.status),
    message: typeof body.detail === "string"
      ? body.detail
      : typeof body.title === "string"
        ? body.title
        : fallbackMessage,
    status: response.status,
  };
}

function problemCode(body: Record<string, unknown>, status: number): string {
  if (typeof body.error_code === "string") return body.error_code;
  if (typeof body.code === "string") return body.code;
  if (typeof body.type === "string") {
    const suffix = body.type.split(":").pop();
    if (suffix) return suffix;
  }
  return `http_${status}`;
}
