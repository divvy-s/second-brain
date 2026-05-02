export type Plugin = {
  name: string;
  version: string;
  auth_type: string;
  location: string;
  enabled: boolean;
};

export type ContextEvent = {
  id: string;
  source: string;
  kind: string;
  title: string;
  body: string;
  occurred_at: string;
  importance: number;
  participants: string[];
};

export type ApprovalRequest = {
  id: string;
  risk: string;
  status: string;
  created_at: string;
  action: Record<string, unknown>;
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
const API_TOKEN_STORAGE_KEY = "second-brain-api-token";

function currentToken(): string {
  const configured = import.meta.env.VITE_API_TOKEN ?? "";
  if (typeof window === "undefined") {
    return configured;
  }
  return window.localStorage.getItem(API_TOKEN_STORAGE_KEY) ?? configured;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = currentToken();
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {})
    }
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

export const api = {
  getToken: () => currentToken(),
  setToken: (token: string) => {
    if (typeof window === "undefined") return;
    if (token.trim()) {
      window.localStorage.setItem(API_TOKEN_STORAGE_KEY, token.trim());
      return;
    }
    window.localStorage.removeItem(API_TOKEN_STORAGE_KEY);
  },
  health: () => request<Record<string, unknown>>("/health"),
  plugins: () => request<{ plugins: Plugin[] }>("/plugins/list"),
  enablePlugin: (name: string) => request<{ name: string; enabled: boolean }>("/plugins/enable", {
    method: "POST",
    body: JSON.stringify({ name })
  }),
  disablePlugin: (name: string) => request<{ name: string; enabled: boolean }>("/plugins/disable", {
    method: "POST",
    body: JSON.stringify({ name })
  }),
  brainDump: (text: string) => request<{ event: ContextEvent }>("/brain-dump", {
    method: "POST",
    body: JSON.stringify({ text })
  }),
  retrieve: (query: string) => request<{ hits: Array<{ event: ContextEvent; score: number }> }>("/events/retrieve", {
    method: "POST",
    body: JSON.stringify({ query, limit: 8 })
  }),
  orchestrate: () => request<Record<string, unknown>>("/orchestrate", {
    method: "POST",
    body: JSON.stringify({})
  }),
  approvals: () => request<{ approvals: ApprovalRequest[] }>("/approvals/list?status=pending"),
  approve: (request_id: string) => request<Record<string, unknown>>("/approvals/approve", {
    method: "POST",
    body: JSON.stringify({ request_id })
  }),
  reject: (request_id: string) => request<Record<string, unknown>>("/approvals/reject", {
    method: "POST",
    body: JSON.stringify({ request_id })
  })
};

