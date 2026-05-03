import { useEffect, useMemo, useState, useCallback } from "react";
import {
  AlertTriangle,
  Bell,
  Brain,
  CalendarDays,
  Check,
  CircleOff,
  Loader2,
  RefreshCw,
  Search,
  Send,
  Wifi,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import { ApprovalRequest, ContextEvent, HealthData, Plugin, ScheduleResponse, TasksResponse, api } from "./api";
import { ActionCenter } from "./components/ActionCenter";
import { AgendaMenu } from "./components/AgendaMenu";
import { useWebSocket } from "./hooks/useWebSocket";
import { useNotifications } from "./hooks/useNotifications";
import { useToast } from "./context/ToastContext";
import { ToastContainer } from "./components/Toast";

type LoadState = "idle" | "loading" | "error";
type SearchState = "idle" | "loading" | "done" | "error";
type FeedFilter = "all" | "email" | "telegram" | "whatsapp" | "calendar" | "slack" | "brain_dump";

const SOURCE_ICONS: Record<string, string> = {
  mcp_gmail: "EM",
  mcp_telegram: "TG",
  mcp_whatsapp: "WA",
  mcp_calendar: "CA",
  mcp_slack: "SL",
  mcp_todoist: "TD",
  system: "SB",
};

const SOURCE_LABELS: Record<string, string> = {
  mcp_gmail: "email",
  mcp_telegram: "telegram",
  mcp_whatsapp: "whatsapp",
  mcp_calendar: "calendar",
  mcp_slack: "slack",
  mcp_todoist: "todoist",
  system: "brain_dump",
};

function relativeTime(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString();
}

function FeedSkeleton({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, index) => (
        <div className="feed-item skeleton-row" key={index}>
          <div className="feed-source-icon skeleton-block" />
          <div className="feed-body">
            <div className="skeleton-line" />
            <div className="skeleton-line medium" />
            <div className="skeleton-line tiny" />
          </div>
        </div>
      ))}
    </>
  );
}

function sourceMark(source: string): string {
  return SOURCE_LABELS[source]?.slice(0, 2).toUpperCase() || source.slice(0, 2).toUpperCase();
}

function humanizeError(message?: string): string {
  if (!message) return "Not connected yet.";
  if (message.includes("{") || message.includes("Traceback")) return "The connector returned an internal error.";
  return message.replace(/^RuntimeError:\s*/i, "").slice(0, 180);
}

function connectorTone(status?: { healthy?: boolean; mode?: string; error?: string; mock_enabled?: boolean }): "good" | "warn" | "bad" | "muted" {
  if (!status) return "muted";
  if (status.healthy) return "good";
  if (status.mock_enabled || status.mode === "mock") return "warn";
  return "bad";
}

function connectorLabel(status?: { healthy?: boolean; mode?: string; error?: string; mock_enabled?: boolean }): string {
  if (!status) return "Unknown";
  if (status.healthy && status.mode === "webhook") return "Webhook ready";
  if (status.healthy) return "Connected";
  if (status.mock_enabled || status.mode === "mock") return "Mock data";
  if (status.mode === "unconfigured") return "Setup needed";
  if (status.mode === "error") return "Error";
  return "Disconnected";
}

function intentSummary(intents: Array<{ type: string; plugin?: string }>): string {
  const actions = intents
    .map((intent) => [intent.type?.replace(/_/g, " "), intent.plugin].filter(Boolean).join(" via "))
    .filter(Boolean);
  if (actions.length === 0) return "No actions were generated.";
  return actions.join(", ");
}

export function App() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [feedEvents, setFeedEvents] = useState<ContextEvent[]>([]);
  const [tasks, setTasks] = useState<any[]>([]);
  const [schedule, setSchedule] = useState<any[]>([]);
  const [tasksState, setTasksState] = useState<TasksResponse>({ tasks: [], source: "idle", connected: true });
  const [scheduleState, setScheduleState] = useState<ScheduleResponse>({ events: [], source: "idle", connected: true });
  const [feedFilter, setFeedFilter] = useState<FeedFilter>("all");
  const [feedTotal, setFeedTotal] = useState(0);
  const [feedOffset, setFeedOffset] = useState(0);
  const [feedHasMore, setFeedHasMore] = useState(false);
  const [hits, setHits] = useState<Array<{ event: ContextEvent; score: number }>>([]);
  const [searchState, setSearchState] = useState<SearchState>("idle");
  const [searchMode, setSearchMode] = useState<"semantic" | "keyword_fallback">("keyword_fallback");
  const [searchError, setSearchError] = useState("");
  const [captureText, setCaptureText] = useState("");
  const [query, setQuery] = useState("");
  const [lastRun, setLastRun] = useState<{ title: string; body: string; issues?: Record<string, string> } | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState("");
  const [isActionCenterOpen, setActionCenterOpen] = useState(false);
  const [isAgendaOpen, setAgendaOpen] = useState(false);

  const { notify, permission, requestPermission } = useNotifications();
  const { pushToast } = useToast();

  const enabledCount = useMemo(() => plugins.filter((p) => p.enabled).length, [plugins]);
  const visibleConnectors = useMemo(
    () => ["telegram", "whatsapp", "gmail", "calendar", "slack", "todoist"],
    []
  );
  const connectorIssues = useMemo(() => {
    if (!health?.plugins) return [];
    return visibleConnectors
      .map((name) => ({ name, status: health.plugins[name] }))
      .filter(({ status }) => status && !status.healthy && status.mode !== "mock");
  }, [health, visibleConnectors]);

  const filteredFeed = useMemo(() => {
    if (feedFilter === "all") return feedEvents;
    return feedEvents.filter((e) => {
      const label = SOURCE_LABELS[e.source] ?? e.source;
      return label === feedFilter;
    });
  }, [feedEvents, feedFilter]);

  function replaceFeed(events: ContextEvent[]) {
    setFeedEvents(events);
  }

  function appendFeed(events: ContextEvent[]) {
    setFeedEvents((prev) => {
      const seen = new Set(prev.map((event) => event.id));
      return [...prev, ...events.filter((event) => !seen.has(event.id))];
    });
  }

  const agendaTaskLabel = tasks.length > 0 ? `${tasks.length} tasks` : "";
  const agendaEventLabel = schedule.length > 0 ? `${schedule.length} events` : "";

  async function refresh() {
    setLoadState("loading");
    setError("");
    try {
      const [pluginResult, healthResult, approvalResult, feedResult, tasksResult, scheduleResult] = await Promise.all([
        api.plugins(),
        api.health(),
        api.approvals(),
        api.feed(50, 0),
        api.tasks(),
        api.schedule()
      ]);
      setPlugins(pluginResult.plugins);
      setHealth(healthResult);
      setApprovals(approvalResult.approvals);
      replaceFeed(feedResult.events);
      setFeedTotal(feedResult.total);
      setFeedOffset(feedResult.next_offset);
      setFeedHasMore(feedResult.has_more);
      setTasks(tasksResult.tasks || []);
      setTasksState(tasksResult);
      setSchedule(scheduleResult.events || []);
      setScheduleState(scheduleResult);
      setLoadState("idle");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
      setLoadState("error");
    }
  }

  async function refreshApprovals() {
    const [approvalResult, feedResult] = await Promise.all([
      api.approvals(),
      api.feed(50, 0),
    ]);
    setApprovals(approvalResult.approvals);
    replaceFeed(feedResult.events);
    setFeedTotal(feedResult.total);
    setFeedOffset(feedResult.next_offset);
    setFeedHasMore(feedResult.has_more);
  }

  async function loadMoreFeed() {
    try {
      const feedResult = await api.feed(50, feedOffset);
      appendFeed(feedResult.events);
      setFeedTotal(feedResult.total);
      setFeedOffset(feedResult.next_offset);
      setFeedHasMore(feedResult.has_more);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load older events.");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function togglePlugin(plugin: Plugin) {
    if (plugin.enabled) {
      await api.disablePlugin(plugin.name);
    } else {
      await api.enablePlugin(plugin.name);
    }
    await refresh();
  }

  async function capture() {
    if (!captureText.trim()) return;
    setCapturing(true);
    try {
      const result = await api.brainDump(captureText);
      setCaptureText("");
      pushToast({
        title: "Capture saved",
        body: `${result.approvals?.length || 0} approvals generated. ${intentSummary(result.intents || [])}`,
        type: "info"
      });
      await refreshApprovals();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Capture failed");
    } finally {
      setCapturing(false);
    }
  }

  async function retrieve() {
    if (!query.trim()) return;
    setSearchState("loading");
    setSearchError("");
    try {
      const result = await api.retrieve(query);
      setHits(result.hits);
      setSearchMode(result.search_mode === "semantic" ? "semantic" : "keyword_fallback");
      setSearchState("done");
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : "Search failed.");
      setSearchState("error");
    }
  }

  async function runBrain() {
    setLoadState("loading");
    try {
      const result = await api.orchestrate();
      const results = Array.isArray(result.results) ? result.results.length : 0;
      setLastRun({ title: "Thinking complete", body: `${results} orchestration results are ready.` });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setLoadState("idle");
    }
  }

  async function syncConnectors() {
    setLoadState("loading");
    setError("");
    try {
      const result = await api.sync();
      setLastRun({
        title: "Connector sync complete",
        body: `${result.synced} new events indexed.`,
        issues: result.connector_errors
      });
      pushToast({
        title: "Sync complete",
        body: `${result.synced} new events indexed.`,
        type: "info"
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setLoadState("idle");
    }
  }

  async function decide(request: ApprovalRequest, approved: boolean) {
    if (approved) {
      await api.approve(request.id);
    } else {
      await api.reject(request.id);
    }
    await refreshApprovals();
  }

  function getPluginHealth(name: string): "healthy" | "unhealthy" | "unknown" {
    if (!health?.plugins) return "unknown";
    const p = health.plugins[name];
    if (!p) return "unknown";
    if (p.healthy) return "healthy";
    return "unhealthy";
  }

  function getApprovalInfo(request: ApprovalRequest) {
    const action = request.action;
    const type = String(action.type ?? "action").replace(/_/g, " ");
    const plugin = String(action.plugin ?? "");
    const title = String(action.title ?? action.text ?? action.subject ?? "");
    let detail = "";
    if (action.start_time) {
      try {
        const d = new Date(action.start_time as string);
        detail = d.toLocaleString();
      } catch {
        detail = String(action.start_time);
      }
    }
    if (action.recipient) {
      detail = `To: ${action.recipient}`;
    }
    return { type, plugin, title, detail };
  }

  const handleWebSocketMessage = useCallback((msg: any) => {
    if (msg.type === "new_event") {
      const event = msg.payload;
      const isUrgentWhatsApp = event?.source === "mcp_whatsapp" && 
        (event?.title?.toLowerCase().includes("urgent") || event?.body?.toLowerCase().includes("urgent"));

      if (isUrgentWhatsApp) {
        notify("Urgent WhatsApp Message", event.body || event.title);
        pushToast({
          title: "Urgent WhatsApp Message",
          body: event.body || event.title,
          type: "warning"
        });
      } else {
        pushToast({
          title: "New Event Indexed",
          body: event?.title || "An event was added to your second brain",
          type: "info"
        });
      }
      refresh();
    } else if (msg.type === "approval_request" || msg.type === "new_approval") {
      pushToast({
        title: "Action Required",
        body: `New approval request for ${msg.payload?.action?.type || "an action"}`,
        type: "warning"
      });
      notify("Action Required", "New approval request requires your attention.");
      refreshApprovals();
    } else if (msg.type === "urgent_alert") {
      notify(msg.payload?.title || "Urgent Alert", msg.payload?.body || "");
      pushToast({
        title: msg.payload?.title || "Urgent Alert",
        body: msg.payload?.body || "",
        type: "warning"
      });
    }
  }, [pushToast, notify]);

  const WS_URL = (import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000").replace(/^http/, "ws") + "/ws/events";
  useWebSocket(WS_URL, handleWebSocketMessage);

  return (
    <main className="app-shell">
      {/* ============ Top Bar ============ */}
      <header className="topbar">
        <div className="brand">
          <div className="brand-icon">
            <Brain size={22} />
          </div>
          <div>
            <h1>Second Brain</h1>
            <span className="brand-subtitle">{enabledCount} plugins - {feedEvents.length} events indexed</span>
          </div>
        </div>
        <div className="topbar-actions">
          <button className="btn" onClick={() => setAgendaOpen(true)} title="Agenda">
            <CalendarDays size={15} />
            Agenda
            {agendaTaskLabel && (
              <span className="nav-badge pulse" title="Pending tasks">{agendaTaskLabel}</span>
            )}
            {agendaEventLabel && (
              <span className="nav-badge neutral" title="Calendar events">{agendaEventLabel}</span>
            )}
          </button>
          <button className="btn" onClick={() => setActionCenterOpen(true)} title="Action Center">
            <Bell size={15} />
            Action Center
            {approvals.length > 0 && (
              <span className="nav-badge pulse">{approvals.length}</span>
            )}
          </button>
          <button className="btn" onClick={syncConnectors} title="Sync connectors">
            <RefreshCw size={15} />
            Sync
          </button>
          <button className="btn" onClick={runBrain} title="Run orchestration and action planning">
            <Zap size={15} />
            Think
          </button>
          <button className="btn btn-icon" onClick={refresh} title="Refresh">
            {loadState === "loading" ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
          </button>
        </div>
      </header>

      {error && <div className="notice error">{error}</div>}
      {permission !== "granted" && (
        <div className={`notice notification ${permission === "denied" ? "warning" : ""}`}>
          <div>
            <strong>Urgent notifications are {permission === "denied" ? "blocked" : "off"}.</strong>
            <span> Enable browser notifications to receive urgent WhatsApp and approval alerts.</span>
          </div>
          {permission === "default" ? (
            <button className="btn btn-primary" onClick={requestPermission}>
              <Bell size={15} />
              Enable
            </button>
          ) : (
            <span className="notice-hint">Use your browser site settings to allow notifications.</span>
          )}
        </div>
      )}
      {connectorIssues.length > 0 && (
        <div className="notice connector-warning">
          <AlertTriangle size={16} />
          <div>
            <strong>{connectorIssues.length} connector{connectorIssues.length === 1 ? "" : "s"} need attention.</strong>
            <span> {connectorIssues.map(({ name, status }) => `${name}: ${connectorLabel(status)}`).join(", ")}</span>
          </div>
        </div>
      )}

      <ActionCenter
        isOpen={isActionCenterOpen}
        onClose={() => setActionCenterOpen(false)}
        approvals={approvals}
        onApprove={(id) => decide({ id } as any, true)}
        onReject={(id) => decide({ id } as any, false)}
      />

      <ToastContainer />

      {/* ============ 3-Column Dashboard ============ */}
      <div className="dashboard">

        {/* ---- Left Sidebar ---- */}
        <aside className="sidebar">
          <div className="sidebar-section">
            <h3>Plugins</h3>
            {plugins.map((plugin) => (
              <div className="plugin-card" key={plugin.name} onClick={() => togglePlugin(plugin)}>
                <div className={`plugin-dot ${getPluginHealth(plugin.name)}`} />
                <div className="plugin-info">
                  <div className="plugin-name">{plugin.name}</div>
                  <div className="plugin-meta">
                    {plugin.enabled ? connectorLabel(health?.plugins?.[plugin.name]) : "Disabled"} - v{plugin.version}
                  </div>
                </div>
                {plugin.enabled && connectorTone(health?.plugins?.[plugin.name]) === "good" ? (
                  <Wifi size={14} style={{ color: "var(--accent-emerald)" }} />
                ) : plugin.enabled ? (
                  <WifiOff size={14} style={{ color: "var(--accent-amber)" }} />
                ) : (
                  <CircleOff size={14} style={{ color: "var(--text-muted)" }} />
                )}
              </div>
            ))}
          </div>

          <div className="sidebar-section">
            <h3>System Status</h3>
            <div className="status-row">
              <span className="status-label">LLM</span>
              <span className={`status-value ${health?.llm_configured ? "good" : "bad"}`}>
                {health?.llm_configured ? "Active" : "Missing"}
              </span>
            </div>
            <div className="status-row">
              <span className="status-label">Event Bus</span>
              <span className={`status-value ${health?.redis_backed ? "good" : "warn"}`}>
                {health?.redis_backed ? "Redis" : "Local"}
              </span>
            </div>
            <div className="status-row">
              <span className="status-label">Events</span>
              <span className="status-value good">{feedEvents.length}</span>
            </div>
            <div className="status-row">
              <span className="status-label">Memory Search</span>
              <span className={`status-value ${health?.chroma_ready ? "good" : "warn"}`}>
                {health?.chroma_ready ? "Semantic" : "Keyword fallback"}
              </span>
            </div>
            <div className="status-row">
              <span className="status-label">Telegram Webhook</span>
              <span className={`status-value ${health?.telegram_webhook?.safe_for_webhook ? "good" : "warn"}`}>
                {health?.telegram_webhook?.safe_for_webhook ? "Ready" : "Setup needed"}
              </span>
            </div>
          </div>

          <div className="sidebar-section">
            <h3>Connector Health</h3>
            <div className="connector-grid">
              {visibleConnectors.map((name) => {
                const status = health?.plugins?.[name];
                const tone = connectorTone(status);
                return (
                  <div className={`connector-pill ${tone}`} key={name}>
                    <span>{name}</span>
                    <strong>{connectorLabel(status)}</strong>
                  </div>
                );
              })}
            </div>
          </div>
        </aside>

        {/* ---- Main Content ---- */}
        <div className="main-content">

          {/* Capture Panel */}
          <div className="glass-panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <div className="panel-icon capture"><Send size={16} /></div>
                <span className="panel-title">Capture</span>
              </div>
            </div>
            <textarea
              className="capture-textarea"
              value={captureText}
              onChange={(e) => setCaptureText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && e.ctrlKey) capture(); }}
              placeholder="Type a task, message, reminder, or note... (Ctrl+Enter to send)"
            />
            <div className="capture-actions">
              <span className="capture-hint">Generated actions appear in Approvals for review.</span>
              <button
                className="btn btn-primary"
                onClick={capture}
                disabled={capturing || !captureText.trim()}
              >
                {capturing ? <Loader2 className="spin" size={15} /> : <Send size={15} />}
                {capturing ? "Generating..." : "Capture"}
              </button>
            </div>
          </div>

          {/* Activity Feed Panel */}
          <div className="glass-panel">
            <div className="panel-header">
              <div className="panel-header-left">
                <div className="panel-icon feed"><Zap size={16} /></div>
                <span className="panel-title">Activity Feed</span>
                <span className="badge" style={{ marginLeft: 4 }}>{filteredFeed.length}</span>
              </div>
            </div>

            <div className="feed-filters">
              {(["all", "email", "telegram", "whatsapp", "calendar", "slack", "brain_dump"] as FeedFilter[]).map((f) => (
                <button
                  key={f}
                  className={`feed-filter ${feedFilter === f ? "active" : ""}`}
                  onClick={() => setFeedFilter(f)}
                >
                  {f === "all" ? "All" : f === "brain_dump" ? "Notes" : f.charAt(0).toUpperCase() + f.slice(1)}
                </button>
              ))}
            </div>

            <div className="feed-list">
              {loadState === "loading" && feedEvents.length === 0 && <FeedSkeleton />}
              {loadState !== "loading" && filteredFeed.length === 0 && (
                <div className="approval-empty">
                  {feedFilter === "all"
                    ? "No activity yet. Sync connectors or capture a note to start filling the feed."
                    : `No ${feedFilter === "brain_dump" ? "notes" : feedFilter} events loaded yet.`}
                </div>
              )}
              {filteredFeed.map((event) => {
                const sourceLabel = SOURCE_LABELS[event.source] ?? event.source;
                const icon = sourceMark(event.source);
                return (
                  <div className="feed-item" key={event.id}>
                    <div className={`feed-source-icon ${sourceLabel}`}>{icon}</div>
                    <div className="feed-body">
                      <div className="feed-title">{event.title}</div>
                      {event.body && <div className="feed-snippet">{event.body}</div>}
                      
                      {/* AI Summary Block */}
                      {event.semantic_summary && event.semantic_summary.trim() !== "" && (
                        <div className="feed-summary">
                          <span className="summary-icon">AI Summary</span>
                          <p>{event.semantic_summary}</p>
                        </div>
                      )}

                      <div className="feed-meta">
                        <span className={`source-badge ${sourceLabel}`}>{sourceLabel}</span>
                        <span className="feed-time">{relativeTime(event.occurred_at)}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
              {feedHasMore && feedFilter === "all" && (
                <button className="btn load-more" onClick={loadMoreFeed}>
                  Load more
                </button>
              )}
              {feedTotal > 0 && (
                <div className="feed-count">{feedEvents.length} of {feedTotal} loaded</div>
              )}
            </div>
          </div>
        </div>

        {/* ---- Right Sidebar ---- */}
        <aside className="right-sidebar">

          {/* Approvals */}
          <div className="sidebar-section">
            <h3>
              Approvals
              {approvals.length > 0 && <span className="badge" style={{ marginLeft: 8 }}>{approvals.length}</span>}
            </h3>
            {approvals.length === 0 && (
              <div className="approval-empty">Capture something to generate actions.</div>
            )}
            {approvals.map((request) => {
              const info = getApprovalInfo(request);
              return (
                <div className="approval-card" key={request.id}>
                  <div className="approval-type">{info.type} to {info.plugin}</div>
                  <div className="approval-title">{info.title || "(untitled)"}</div>
                  {info.detail && <div className="approval-detail">{info.detail}</div>}
                  <div className="approval-detail">{request.risk} risk - {request.id.slice(0, 8)}</div>
                  <div className="approval-actions">
                    <button className="btn btn-icon success" onClick={() => decide(request, true)} title="Approve & Execute">
                      <Check size={16} />
                    </button>
                    <button className="btn btn-icon danger" onClick={() => decide(request, false)} title="Reject">
                      <X size={16} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Memory Search */}
          <div className="sidebar-section">
            <h3>Memory Search</h3>
            <div className={`mini-status ${health?.chroma_ready ? "good" : "warn"}`}>
              {health?.chroma_ready ? "Semantic search ready" : "Using keyword fallback while semantic search warms up"}
            </div>
            <div className="search-row">
              <input
                className="search-input"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") retrieve(); }}
                placeholder="Search your memory..."
              />
              <button className="btn btn-icon" onClick={retrieve} title="Search">
                {searchState === "loading" ? <Loader2 className="spin" size={15} /> : <Search size={15} />}
              </button>
            </div>
            <div className="search-results">
              {searchState === "error" && (
                <div className="approval-empty">{humanizeError(searchError)}</div>
              )}
              {searchState === "done" && hits.length === 0 && (
                <div className="approval-empty">
                  {searchMode === "keyword_fallback"
                    ? "No keyword matches yet. Semantic search is still warming up, but local search is active."
                    : "No matching memories found."}
                </div>
              )}
              {hits.map((hit) => (
                <div className="search-hit" key={hit.event.id}>
                  <div className="search-hit-title">{hit.event.title}</div>
                  <div className="search-hit-body">{hit.event.body}</div>
                  <div className="search-hit-meta">
                    {hit.event.source} - score {hit.score.toFixed(2)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </aside>
      </div>

      <AgendaMenu 
        isOpen={isAgendaOpen} 
        onClose={() => setAgendaOpen(false)} 
        tasks={tasks} 
        schedule={schedule} 
        loading={loadState === "loading"}
        tasksState={tasksState}
        scheduleState={scheduleState}
      />

      {/* ============ Sync Band ============ */}
      {lastRun && (
        <div className="sync-band">
          <div className="sync-summary">
            <strong>{lastRun.title}</strong>
            <span>{lastRun.body}</span>
          </div>
          {lastRun.issues && Object.keys(lastRun.issues).length > 0 && (
            <div className="sync-issues">
              {Object.entries(lastRun.issues).map(([name, issue]) => (
                <span key={name}>{name}: {humanizeError(issue)}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </main>
  );
}
