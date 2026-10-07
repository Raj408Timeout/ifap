import type {
  GenerateResponse,
  LLMStatus,
  Question,
  Questionnaire,
  ReviseResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_IFAP_API_URL ?? "http://localhost:8000";
const PREFIX = `${API_URL}/api/v1`;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${PREFIX}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    const detail = typeof body.detail === "string" ? body.detail : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const api = {
  generate: (message: string, questionCount?: number) =>
    request<GenerateResponse>("/questionnaires/generate", {
      method: "POST",
      body: JSON.stringify({ message, question_count: questionCount ?? null }),
    }),

  revise: (id: string, title: string, description: string, questions: Question[]) =>
    request<ReviseResponse>(`/questionnaires/${id}`, {
      method: "PUT",
      body: JSON.stringify({ title, description, questions }),
    }),

  llmStatus: () => request<LLMStatus>("/llm"),

  setLlmEnabled: (enabled: boolean) =>
    request<LLMStatus>("/llm", { method: "PUT", body: JSON.stringify({ enabled }) }),

  publish: (id: string) =>
    request<Questionnaire>(`/questionnaires/${id}/publish`, { method: "POST" }),
};
