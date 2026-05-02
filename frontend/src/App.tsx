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
  const [captureText, setCaptureText] = useState("");
  const [query, setQuery] = useState("");
  const [lastRun, setLastRun] = useState<Record<string, unknown> | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [error, setError] = useState("");

  const enabledCount = useMemo(() => plugins.filter((plugin) => plugin.enabled).length, [plugins]);

  async function refresh() {
    setLoadState("loading");
    setError("");
    try {
      const [pluginResult, healthResult, approvalResult] = await Promise.all([
        api.plugins(),
        api.health(),
        api.approvals()
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
    const result = await api.brainDump(captureText);
    setCaptureText("");
    setHits([{ event: result.event, score: result.event.importance }, ...hits]);
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
    await refresh();
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
        <button className="icon-button" onClick={refresh} title="Refresh">
          {loadState === "loading" ? <Loader2 className="spin" size={18} /> : <RefreshCw size={18} />}
        </button>
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
                <button className="icon-button" onClick={() => togglePlugin(plugin)} title={plugin.enabled ? "Disable" : "Enable"}>
                  {plugin.enabled ? <Check size={18} /> : <CircleOff size={18} />}
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-title">
            <Send size={18} />
            <h2>Capture</h2>
          </div>
          <textarea
            value={captureText}
            onChange={(event) => setCaptureText(event.target.value)}
            placeholder="Brain dump, message, meeting note, or task..."
          />
          <button className="primary" onClick={capture}>
            <Send size={17} />
            Capture
          </button>
        </div>

        <div className="panel">
          <div className="panel-title">
            <Search size={18} />
            <h2>Retrieval</h2>
          </div>
          <div className="input-row">
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search context" />
            <button className="icon-button" onClick={retrieve} title="Search">
              <Search size={18} />
            </button>
          </div>
          <div className="event-list">
            {hits.map((hit) => (
              <article className="event-item" key={hit.event.id}>
                <strong>{hit.event.title}</strong>
                <p>{hit.event.body}</p>
                <span>{hit.event.source} · {hit.score.toFixed(2)}</span>
              </article>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-title">
            <Bell size={18} />
            <h2>Approvals</h2>
          </div>
          <div className="approval-list">
            {approvals.length === 0 && <p className="muted">No pending approvals.</p>}
            {approvals.map((request) => (
              <article className="approval-item" key={request.id}>
                <div>
                  <strong>{String(request.action.type ?? "action")}</strong>
                  <span>{request.risk} risk · {request.id.slice(0, 8)}</span>
                </div>
                <div className="decision-buttons">
                  <button className="icon-button" onClick={() => decide(request, true)} title="Approve">
                    <Check size={17} />
                  </button>
                  <button className="icon-button danger" onClick={() => decide(request, false)} title="Reject">
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
          Run Orchestration
        </button>
        {lastRun && <pre>{JSON.stringify(lastRun.results ?? lastRun, null, 2)}</pre>}
      </section>
    </main>
  );
}

