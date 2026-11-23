import type { CaseDetail, CaseSummary, ChatResponse } from "../types";

const BASE = import.meta.env.VITE_API_BASE ?? "";
export const WORKSPACE_USER = import.meta.env.VITE_DEMO_USER_ID || "u_demo";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}/api/v1${path}`, init);
  if (!response.ok) {
    let message = `请求未完成（${response.status}）`;
    try {
      const body = await response.json() as { message?: string };
      if (body.message) message = body.message;
    } catch { /* The status remains available for non-JSON responses. */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const listCases = () => request<CaseSummary[]>(`/cases?user_id=${encodeURIComponent(WORKSPACE_USER)}`);
export const getCase = (id: string) => request<CaseDetail>(`/cases/${encodeURIComponent(id)}?user_id=${encodeURIComponent(WORKSPACE_USER)}`);

export function caseCommand<T = ChatResponse>(id: string, command: "confirm" | "dismiss") {
  return request<T>(`/cases/${encodeURIComponent(id)}/${command}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: WORKSPACE_USER }),
  });
}
