import { useEffect, useMemo, useState } from "react";
import {
  Bell,
  Brain,
  Check,
  CircleOff,
  Database,
  Loader2,
  Plug,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  X
} from "lucide-react";
import { ApprovalRequest, ContextEvent, Plugin, api } from "./api";

type LoadState = "idle" | "loading" | "error";

export function App() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [health, setHealth] = useState<Record<string, unknown>>({});
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [hits, setHits] = useState<Array<{ event: ContextEvent; score: number }>>([]);
  const [apiToken, setApiToken] = useState(api.getToken());
  const [captureText, setCaptureText] = useState("");
  const [query, setQuery] = useState("");
  const [lastRun, setLastRun] = useState<Record<string, unknown> | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState("");

  const enabledCount = useMemo(() => plugins.filter((p) => p.enabled).length, [plugins]);

  async function refresh() {
    setLoadState("loading");
    setError("");
    try {
      const [pluginResult, healthResult, approvalResult] = await Promise.all([
        api.plugins(),
        api.health(),
        api.approvals(),
      ]);
      setPlugins(pluginResult.plugins);
      setHealth(healthResult);
      setApprovals(approvalResult.approvals);
      setLoadState("idle");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
      setLoadState("error");
    }
  }

  async function refreshApprovals() {
    const result = await api.approvals();
    setApprovals(result.approvals);
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
      // Add to retrieval list immediately
      setHits((prev) => [{ event: result.event, score: result.event.importance }, ...prev]);
      // Auto-refresh approvals — brain dump now auto-generates them on the backend
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
    const result = await api.orchestrate();
    setLastRun(result);
    await refresh();
    setLoadState("idle");
  }

  async function decide(request: ApprovalRequest, approved: boolean) {
    if (approved) {
      await api.approve(request.id);
    } else {
      await api.reject(request.id);
    }
    await refreshApprovals();
  }

  function getApprovalSummary(request: ApprovalRequest): string {
    const action = request.action;
    const type = String(action.type ?? "action");
    const plugin = String(action.plugin ?? "");
    const title = String(action.title ?? action.text ?? action.subject ?? "");
    let titleStr = title ? `"${title}"` : "";
    if (action.start_time) {
      try {
        const d = new Date(action.start_time as string);
        titleStr += ` (at ${d.toLocaleString()})`;
      } catch (e) {
        titleStr += ` (${action.start_time})`;
      }
    }
    return [type, plugin && `→ ${plugin}`, titleStr].filter(Boolean).join(" ");
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <Brain size={28} />
          <div>
            <h1>Second Brain</h1>
            <span>{enabledCount} plugins enabled</span>
          </div>
        </div>
        <div className="topbar-actions">
          <input
            className="token-input"
            type="password"
            value={apiToken}
            onChange={(e) => {
              const nextValue = e.target.value;
              setApiToken(nextValue);
              api.setToken(nextValue);
            }}
            placeholder="API token"
          />
          <button className="icon-button" onClick={refresh} title="Refresh">
            {loadState === "loading" ? <Loader2 className="spin" size={18} /> : <RefreshCw size={18} />}
          </button>
        </div>
      </header>

      {error && <div className="notice error">{error}</div>}

      <section className="status-grid">
        <div className="metric">
          <Plug size={18} />
          <span>Plugin Health</span>
          <strong>{JSON.stringify(health.plugins ?? {})}</strong>
        </div>
        <div className="metric">
          <Database size={18} />
          <span>Redis Streams</span>
          <strong>{health.redis_backed ? "Connected" : "Local fallback"}</strong>
        </div>
        <div className="metric">
          <ShieldCheck size={18} />
          <span>LLM</span>
          <strong>{health.llm_configured ? "Configured" : "Keys missing"}</strong>
        </div>
      </section>

      <section className="workspace-grid">
        {/* Plugins Panel */}
        <div className="panel">
          <div className="panel-title">
            <Plug size={18} />
            <h2>Plugins</h2>
          </div>
          <div className="plugin-list">
            {plugins.map((plugin) => (
              <div className="plugin-row" key={plugin.name}>
                <div>
                  <strong>{plugin.name}</strong>
                  <span>{plugin.auth_type} · {plugin.location} · v{plugin.version}</span>
                </div>
                <button
                  className="icon-button"
                  onClick={() => togglePlugin(plugin)}
                  title={plugin.enabled ? "Disable" : "Enable"}
                >
                  {plugin.enabled ? <Check size={18} /> : <CircleOff size={18} />}
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Capture Panel */}
        <div className="panel">
          <div className="panel-title">
            <Send size={18} />
            <h2>Capture</h2>
          </div>
          <textarea
            value={captureText}
            onChange={(e) => setCaptureText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && e.ctrlKey) capture(); }}
            placeholder="Type a task, message, reminder, or note... (Ctrl+Enter to send)"
          />
          <button
            className="primary"
            onClick={capture}
            disabled={capturing || !captureText.trim()}
          >
            {capturing ? <Loader2 className="spin" size={17} /> : <Send size={17} />}
            {capturing ? "Generating actions…" : "Capture + Generate Actions"}
          </button>
          <p className="muted" style={{ marginTop: "0.5rem", fontSize: "0.78rem" }}>
            Actions appear in Approvals immediately. Click ✓ to execute.
          </p>
        </div>

        {/* Retrieval Panel */}
        <div className="panel">
          <div className="panel-title">
            <Search size={18} />
            <h2>Retrieval</h2>
          </div>
          <div className="input-row">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") retrieve(); }}
              placeholder="Search your memory…"
            />
            <button className="icon-button" onClick={retrieve} title="Search">
              <Search size={18} />
            </button>
          </div>
          <div className="event-list">
            {hits.map((hit) => (
              <article className="event-item" key={hit.event.id}>
                <strong>{hit.event.title}</strong>
                <p>{hit.event.body}</p>
                <span>{hit.event.source} · score {hit.score.toFixed(2)}</span>
              </article>
            ))}
          </div>
        </div>

        {/* Approvals Panel */}
        <div className="panel">
          <div className="panel-title">
            <Bell size={18} />
            <h2>
              Approvals
              {approvals.length > 0 && (
                <span className="badge" style={{ marginLeft: "0.5rem" }}>{approvals.length}</span>
              )}
            </h2>
          </div>
          <div className="approval-list">
            {approvals.length === 0 && (
              <p className="muted">Capture something above — actions will appear here instantly.</p>
            )}
            {approvals.map((request) => (
              <article className="approval-item" key={request.id}>
                <div>
                  <strong>{getApprovalSummary(request)}</strong>
                  <span>{request.risk} risk · {request.id.slice(0, 8)}</span>
                </div>
                <div className="decision-buttons">
                  <button
                    className="icon-button"
                    onClick={() => decide(request, true)}
                    title="Approve & Execute"
                  >
                    <Check size={17} />
                  </button>
                  <button
                    className="icon-button danger"
                    onClick={() => decide(request, false)}
                    title="Reject"
                  >
                    <X size={17} />
                  </button>
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="run-band">
        <button className="primary" onClick={runBrain}>
          <Brain size={18} />
          Sync Plugins &amp; Run Orchestration
        </button>
        {lastRun && <pre>{JSON.stringify(lastRun.results ?? lastRun, null, 2)}</pre>}
      </section>
    </main>
  );
}
