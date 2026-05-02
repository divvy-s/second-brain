import { useEffect, useMemo, useState, useCallback } from "react";
import {
  Bell,
  Brain,
  Check,
  CircleOff,
  Loader2,
  RefreshCw,
  Search,
  Send,
  X,
  Zap,
} from "lucide-react";
import { ApprovalRequest, ContextEvent, HealthData, Plugin, api } from "./api";
import { ActionCenter } from "./components/ActionCenter";
import { AgendaMenu } from "./components/AgendaMenu";
import { useWebSocket } from "./hooks/useWebSocket";
import { useNotifications } from "./hooks/useNotifications";
import { useToast } from "./context/ToastContext";
import { ToastContainer } from "./components/Toast";

type LoadState = "idle" | "loading" | "error";
type FeedFilter = "all" | "email" | "telegram" | "whatsapp" | "calendar" | "slack" | "brain_dump";

const SOURCE_ICONS: Record<string, string> = {
  mcp_gmail: "📧",
  mcp_telegram: "💬",
  mcp_whatsapp: "📱",
  mcp_calendar: "📅",
  mcp_slack: "🔔",
  mcp_todoist: "✅",
  system: "🧠",
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

export function App() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [feedEvents, setFeedEvents] = useState<ContextEvent[]>([]);
  const [tasks, setTasks] = useState<any[]>([]);
  const [schedule, setSchedule] = useState<any[]>([]);
  const [feedFilter, setFeedFilter] = useState<FeedFilter>("all");
  const [hits, setHits] = useState<Array<{ event: ContextEvent; score: number }>>([]);
  const [captureText, setCaptureText] = useState("");
  const [query, setQuery] = useState("");
  const [lastRun, setLastRun] = useState<Record<string, unknown> | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState("");
  const [isActionCenterOpen, setActionCenterOpen] = useState(false);
  const [isAgendaOpen, setAgendaOpen] = useState(false);

  const { notify } = useNotifications();
  const { pushToast } = useToast();

  const enabledCount = useMemo(() => plugins.filter((p) => p.enabled).length, [plugins]);

  const filteredFeed = useMemo(() => {
    if (feedFilter === "all") return feedEvents;
    return feedEvents.filter((e) => {
      const label = SOURCE_LABELS[e.source] ?? e.source;
      return label === feedFilter;
    });
  }, [feedEvents, feedFilter]);

  async function refresh() {
    setLoadState("loading");
    setError("");
    try {
      const [pluginResult, healthResult, approvalResult, feedResult, tasksResult, scheduleResult] = await Promise.all([
        api.plugins(),
        api.health(),
        api.approvals(),
        api.feed(50),
        api.tasks(),
        api.schedule()
      ]);
      setPlugins(pluginResult.plugins);
      setHealth(healthResult);
      setApprovals(approvalResult.approvals);
      setFeedEvents(feedResult.events);
      setTasks(tasksResult.tasks || []);
      setSchedule(scheduleResult.events || []);
      setLoadState("idle");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
      setLoadState("error");
    }
  }

  async function refreshApprovals() {
    const [approvalResult, feedResult] = await Promise.all([
      api.approvals(),
      api.feed(50),
    ]);
    setApprovals(approvalResult.approvals);
    setFeedEvents(feedResult.events);
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
      await api.brainDump(captureText);
      setCaptureText("");
      await refreshApprovals();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Capture failed");
    } finally {
      setCapturing(false);
    }
  }

  async function retrieve() {
    if (!query.trim()) return;
    const result = await api.retrieve(query);
    setHits(result.hits);
  }

  async function runBrain() {
    setLoadState("loading");
    try {
      const result = await api.orchestrate();
      setLastRun(result);
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
    } else if (msg.type === "approval_request") {
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
            <span className="brand-subtitle">{enabledCount} plugins · {feedEvents.length} events indexed</span>
          </div>
        </div>
        <div className="topbar-actions">
          <button className="btn" onClick={() => setAgendaOpen(true)} title="Agenda">
            <Check size={15} />
            Agenda
            {(tasks.length > 0 || schedule.length > 0) && (
              <span className="nav-badge pulse">{tasks.length + schedule.length}</span>
            )}
          </button>
          <button className="btn" onClick={() => setActionCenterOpen(true)} title="Action Center">
            <Bell size={15} />
            Action Center
            {approvals.length > 0 && (
              <span className="nav-badge pulse">{approvals.length}</span>
            )}
          </button>
          <button className="btn" onClick={runBrain} title="Sync Plugins & Run Orchestration">
            <Zap size={15} />
            Sync
          </button>
          <button className="btn btn-icon" onClick={refresh} title="Refresh">
            {loadState === "loading" ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
          </button>
        </div>
      </header>

      {error && <div className="notice error">{error}</div>}

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
                    {plugin.enabled ? "enabled" : "disabled"} · v{plugin.version}
                  </div>
                </div>
                {plugin.enabled ? <Check size={14} style={{ color: "var(--accent-emerald)" }} /> : <CircleOff size={14} style={{ color: "var(--text-muted)" }} />}
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
              <span className="capture-hint">Actions appear in Approvals instantly. Click ✓ to execute.</span>
              <button
                className="btn btn-primary"
                onClick={capture}
                disabled={capturing || !captureText.trim()}
              >
                {capturing ? <Loader2 className="spin" size={15} /> : <Send size={15} />}
                {capturing ? "Generating…" : "Capture"}
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
              {filteredFeed.length === 0 && (
                <div className="approval-empty">No events yet. Sync plugins to pull in your data.</div>
              )}
              {filteredFeed.map((event) => {
                const sourceLabel = SOURCE_LABELS[event.source] ?? event.source;
                const icon = SOURCE_ICONS[event.source] ?? "📌";
                return (
                  <div className="feed-item" key={event.id}>
                    <div className={`feed-source-icon ${sourceLabel}`}>{icon}</div>
                    <div className="feed-body">
                      <div className="feed-title">{event.title}</div>
                      {event.body && <div className="feed-snippet">{event.body}</div>}
                      
                      {/* AI Summary Block */}
                      {event.semantic_summary && event.semantic_summary.trim() !== "" && (
                        <div className="feed-summary">
                          <span className="summary-icon">✨ AI Summary</span>
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
                  <div className="approval-type">{info.type} → {info.plugin}</div>
                  <div className="approval-title">{info.title || "(untitled)"}</div>
                  {info.detail && <div className="approval-detail">{info.detail}</div>}
                  <div className="approval-detail">{request.risk} risk · {request.id.slice(0, 8)}</div>
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
            <div className="search-row">
              <input
                className="search-input"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") retrieve(); }}
                placeholder="Search your memory…"
              />
              <button className="btn btn-icon" onClick={retrieve} title="Search">
                <Search size={15} />
              </button>
            </div>
            <div className="search-results">
              {hits.map((hit) => (
                <div className="search-hit" key={hit.event.id}>
                  <div className="search-hit-title">{hit.event.title}</div>
                  <div className="search-hit-body">{hit.event.body}</div>
                  <div className="search-hit-meta">
                    {hit.event.source} · score {hit.score.toFixed(2)}
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
      />

      {/* ============ Sync Band ============ */}
      {lastRun && (
        <div className="sync-band">
          <pre>{JSON.stringify(lastRun.results ?? lastRun, null, 2)}</pre>
        </div>
      )}
    </main>
  );
}
