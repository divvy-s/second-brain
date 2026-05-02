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
  metadata?: Record<string, unknown>;
  semantic_summary?: string;
};

export type ApprovalRequest = {
  id: string;
  risk: string;
  status: string;
  created_at: string;
  action: Record<string, unknown>;
};

export type HealthData = {
  ok: boolean;
  plugins: Record<string, { ok: boolean; healthy: boolean; mode: string; error?: string }>;
  plugin_failures: Record<string, string>;
  redis_backed: boolean;
  llm_configured: boolean;
  api_auth_configured: boolean;
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
const API_KEY = import.meta.env.VITE_API_KEY ?? "";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> ?? {})
  };
  // Attach auth header when API key is configured (production mode)
  if (API_KEY) {
    headers["Authorization"] = `Bearer ${API_KEY}`;
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthData>("/health"),
  plugins: () => request<{ plugins: Plugin[] }>("/plugins/list"),
  feed: (limit = 30) => request<{ events: ContextEvent[]; total: number }>(`/events/feed?limit=${limit}`),
  tasks: () => request<{ tasks: Array<{ id: string; title: string; body: string; occurred_at: string; importance: number; metadata: Record<string, unknown> }>; source: string }>("/tasks/pending"),
  schedule: () => request<{ events: Array<{ id: string; title: string; body: string; occurred_at: string; participants: string[]; importance: number; metadata: Record<string, unknown> }>; source: string }>("/schedule/upcoming"),
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
