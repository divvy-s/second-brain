from __future__ import annotations

from typing import Any, TypedDict

from agents import CalendarAgent, EmailAgent, NotificationAgent, TaskAgent
from connectors.base import ContextEvent
from connectors.runner import ConnectorRunner
from execution.executor import ActionExecutor
from intelligence.goal_decomposer import GoalDecomposer
from intelligence.priority import PriorityScorer
from memory.database import MemoryDatabase
from memory.knowledge_graph import KnowledgeGraph
from memory.ner import EntityExtractor
from memory.vector_store import VectorStore


class BrainState(TypedDict, total=False):
    events: list[ContextEvent]
    prioritized: list[tuple[ContextEvent, float]]
    plans: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    results: list[dict[str, Any]]


class BrainWorkflow:
    def __init__(
        self,
        runner: ConnectorRunner,
        database: MemoryDatabase,
        vector_store: VectorStore,
        extractor: EntityExtractor,
        scorer: PriorityScorer,
        decomposer: GoalDecomposer,
        executor: ActionExecutor,
    ) -> None:
        self.runner = runner
        self.database = database
        self.vector_store = vector_store
        self.extractor = extractor
        self.scorer = scorer
        self.decomposer = decomposer
        self.executor = executor
        self.graph = KnowledgeGraph(database)
        self.agents = [EmailAgent(), CalendarAgent(), TaskAgent(), NotificationAgent()]
        self._compiled_graph = self._build_langgraph()

    def _build_langgraph(self):
        try:
            from langgraph.graph import END, StateGraph

            graph = StateGraph(BrainState)
            graph.add_node("ingest", self.ingest)
            graph.add_node("prioritize", self.prioritize)
            graph.add_node("plan", self.plan)
            graph.add_node("route", self.route)
            graph.add_node("approval_execute", self.approval_execute)
            graph.set_entry_point("ingest")
            graph.add_edge("ingest", "prioritize")
            graph.add_edge("prioritize", "plan")
            graph.add_edge("plan", "route")
            graph.add_edge("route", "approval_execute")
            graph.add_edge("approval_execute", END)
            return graph.compile()
        except Exception:
            return None

    def run(self, events: list[ContextEvent] | None = None) -> BrainState:
        state: BrainState = {"events": events or []}
        if self._compiled_graph is not None:
            return self._compiled_graph.invoke(state)
        for step in (self.ingest, self.prioritize, self.plan, self.route, self.approval_execute):
            state = step(state)
        return state

    def ingest(self, state: BrainState) -> BrainState:
        # If events are provided, use them. Otherwise fetch from plugins.
        events = state.get("events")
        if events is None:
            # When auto-fetching, we filter out mock events to reduce noise
            raw_events = self.runner.fetch_all_events()
            events = [e for e in raw_events if e.metadata.get("mode") != "mock"]
            
            # Also fetch recent manual brain dumps so they get processed
            db_events = self.database.recent_events(limit=20)
            events.extend([e for e in db_events if e.kind == "brain_dump"])
            
        enriched: list[ContextEvent] = []
        for event in events:
            if not event.entities:
                event.entities = [entity.to_dict() for entity in self.extractor.extract(f"{event.title}\n{event.body}")]
            self.database.add_event(event)
            self.vector_store.index_event(event)
            self.graph.link_event(event)
            enriched.append(event)
        return {**state, "events": enriched}

    def prioritize(self, state: BrainState) -> BrainState:
        prioritized = [(event, self.scorer.score(event)) for event in state.get("events", [])]
        prioritized.sort(key=lambda item: item[1], reverse=True)
        return {**state, "prioritized": prioritized}

    def plan(self, state: BrainState) -> BrainState:
        plans: list[dict[str, Any]] = []
        for event, score in state.get("prioritized", []):
            # Higher threshold for autonomous planning
            if score < 0.6:
                continue
            steps = self.decomposer.decompose(
                f"Decide what to do about this event: {event.title}\n{event.body}",
                context=f"source={event.source}; kind={event.kind}; score={score:.2f}",
            )
            plans.append({"event_id": event.id, "score": score, "steps": [step.__dict__ for step in steps]})
        return {**state, "plans": plans}

    def route(self, state: BrainState) -> BrainState:
        # Get existing requests to avoid processing the same event twice
        existing_requests = self.executor.approval_gate.store.list()
        handled_event_ids = {
            str(req.action.get("source_event_id")) 
            for req in existing_requests 
            if req.action.get("source_event_id")
        }

        actions: list[dict[str, Any]] = []
        for event, _ in state.get("prioritized", []):
            # Skip if this event has EVER generated an action
            if event.id in handled_event_ids:
                continue
                
            for agent in self.agents:
                if agent.can_handle(event):
                    decision = agent.handle(event)
                    actions.extend(decision.actions)
                    
        for plan in state.get("plans", []):
            if plan["event_id"] in handled_event_ids:
                continue
                
            for step in plan.get("steps", []):
                action = dict(step["action"])
                action.setdefault("risk", step.get("risk", "medium"))
                action.setdefault("source_event_id", plan["event_id"])
                actions.append(action)
                
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for action in actions:
            key = (str(action.get("type")), str(action.get("plugin")), str(action.get("source_event_id")))
            if key not in seen:
                seen.add(key)
                deduped.append(action)
        return {**state, "actions": deduped}

    def approval_execute(self, state: BrainState) -> BrainState:
        """Submit each action to the approval gate. Actual execution happens when the user approves via the UI."""
        store = self.executor.approval_gate.store
        # Build set of (type, plugin, source_event_id) already in the store to avoid duplicates
        existing_keys: set[tuple[str, str, str]] = {
            (str(req.action.get("type")), str(req.action.get("plugin")), str(req.action.get("source_event_id")))
            for req in store.list()
            if req.status == "pending"
        }
        results: list[dict[str, Any]] = []
        for action in state.get("actions", []):
            key = (str(action.get("type")), str(action.get("plugin")), str(action.get("source_event_id")))
            if key in existing_keys:
                results.append({"status": "already_pending", "key": str(key)})
                continue
            # evaluate() creates the pending ApprovalRequest without executing
            result = self.executor.approval_gate.evaluate(action)
            results.append(result)
        return {**state, "results": results}

