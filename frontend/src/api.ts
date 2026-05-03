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
  plugins: Record<string, { ok: boolean; healthy: boolean; mode: string; error?: string; mock_enabled?: boolean; last_fetch_error?: string }>;
  plugin_failures: Record<string, string>;
  redis_backed: boolean;
  llm_configured: boolean;
  api_auth_configured: boolean;
  environment?: string;
  auth_bypass_active?: boolean;
  chroma_ready?: boolean;
  telegram_webhook?: {
    mode: string;
    secret_configured: boolean;
    secret_source: string;
    polling_enabled: boolean;
    safe_for_webhook: boolean;
  };
};

export type FeedResponse = {
  events: ContextEvent[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  next_offset: number;
};

export type TasksResponse = {
  tasks: Array<{ id: string; title: string; body: string; occurred_at: string; importance: number; metadata: Record<string, unknown> }>;
  source: string;
  connected: boolean;
  error?: string;
  message?: string;
};

export type ScheduleResponse = {
  events: Array<{ id: string; title: string; body: string; occurred_at: string; participants: string[]; importance: number; metadata: Record<string, unknown> }>;
  source: string;
  connected: boolean;
  error?: string;
  message?: string;
};

export type BrainDumpResponse = {
  event: ContextEvent;
  intents: Array<{ type: string; plugin?: string; confidence?: number; fields?: Record<string, unknown> }>;
  approvals: ApprovalRequest[];
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
const API_KEY = import.meta.env.VITE_API_KEY ?? "";

async function friendlyError(response: Response): Promise<string> {
  const fallback = response.status === 401
    ? "You are not signed in to the API."
    : response.status === 403
      ? "The API key was rejected."
      : response.status === 429
        ? "Too many requests. Please wait a moment and try again."
        : response.status >= 500
          ? "The server hit an error. Please try again."
          : "Request failed.";
  try {
    const payload = await response.json();
    const detail = typeof payload?.detail === "string" ? payload.detail : "";
    if (!detail) return fallback;
    if (detail.includes("{") || detail.includes("Traceback") || detail.length > 180) return fallback;
    return detail;
  } catch {
    return fallback;
  }
}

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
    throw new Error(await friendlyError(response));
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthData>("/health"),
  plugins: () => request<{ plugins: Plugin[] }>("/plugins/list"),
  feed: (limit = 30, offset = 0, source = "all") => request<FeedResponse>(`/events/feed?limit=${limit}&offset=${offset}&source=${encodeURIComponent(source)}`),
  tasks: () => request<TasksResponse>("/tasks/pending"),
  schedule: () => request<ScheduleResponse>("/schedule/upcoming"),
  enablePlugin: (name: string) => request<{ name: string; enabled: boolean }>("/plugins/enable", {
    method: "POST",
    body: JSON.stringify({ name })
  }),
  disablePlugin: (name: string) => request<{ name: string; enabled: boolean }>("/plugins/disable", {
    method: "POST",
    body: JSON.stringify({ name })
  }),
  brainDump: (text: string) => request<BrainDumpResponse>("/brain-dump", {
    method: "POST",
    body: JSON.stringify({ text })
  }),
  retrieve: (query: string) => request<{ hits: Array<{ event: ContextEvent; score: number }>; search_mode: string; chroma_ready: boolean }>("/events/retrieve", {
    method: "POST",
    body: JSON.stringify({ query, limit: 8 })
  }),
  orchestrate: () => request<Record<string, unknown>>("/orchestrate", {
    method: "POST",
    body: JSON.stringify({})
  }),
  sync: () => request<{ ok: boolean; synced: number; events: ContextEvent[]; connector_errors: Record<string, string> }>("/events/sync", {
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
