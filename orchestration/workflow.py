from __future__ import annotations

from typing import Any, TypedDict

from agents import CalendarAgent, EmailAgent, NotificationAgent, TaskAgent
from connectors.base import ContextEvent
from connectors.runner import ConnectorRunner
from execution.executor import ActionExecutor
from intelligence.goal_decomposer import GoalDecomposer
from intelligence.llm_adapter import LLMAdapter, LLMRequest, LLMUnavailable
from intelligence.priority import PriorityScorer
from memory.database import MemoryDatabase
from memory.event_bus import EventBus
from memory.knowledge_graph import KnowledgeGraph
from memory.ner import EntityExtractor
from memory.retrieval import HybridRetriever, RetrievalHit
from memory.vector_store import VectorStore


class BrainState(TypedDict, total=False):
    events: list[ContextEvent]
    prioritized: list[RetrievalHit]
    retrievals: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
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
        *,
        retriever: HybridRetriever | None = None,
        llm: LLMAdapter | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.runner = runner
        self.database = database
        self.vector_store = vector_store
        self.extractor = extractor
        self.scorer = scorer
        self.decomposer = decomposer
        self.executor = executor
        self.retriever = retriever or HybridRetriever(database, vector_store, scorer=scorer, extractor=extractor)
        self.llm = llm
        self.event_bus = event_bus
        self.graph = KnowledgeGraph(database)
        self.agents = [EmailAgent(), CalendarAgent(), TaskAgent(), NotificationAgent()]
        self._compiled_graph = self._build_langgraph()

    def _build_langgraph(self):
        try:
            from langgraph.graph import END, StateGraph

            graph = StateGraph(BrainState)
            graph.add_node("ingest", self.ingest)
            graph.add_node("prioritize", self.prioritize)
            graph.add_node("retrieve_context", self.retrieve_context)
            graph.add_node("plan", self.plan)
            graph.add_node("route", self.route)
            graph.add_node("approval_execute", self.approval_execute)
            graph.set_entry_point("ingest")
            graph.add_edge("ingest", "prioritize")
            graph.add_edge("prioritize", "retrieve_context")
            graph.add_edge("retrieve_context", "plan")
            graph.add_edge("plan", "route")
            graph.add_edge("route", "approval_execute")
            graph.add_edge("approval_execute", END)
            return graph.compile()
        except Exception:
            return None

    async def run(self, events: list[ContextEvent] | None = None) -> BrainState:
        state: BrainState = {"events": events} if events is not None else {}
        if self._compiled_graph is not None:
            # Note: LangGraph has an ainvoke method for async execution
            if hasattr(self._compiled_graph, "ainvoke"):
                return await self._compiled_graph.ainvoke(state)
            else:
                return self._compiled_graph.invoke(state)
        for step in (
            self.ingest,
            self.prioritize,
            self.retrieve_context,
            self.plan,
            self.route,
            self.approval_execute,
        ):
            res = await step(state)
            state.update(res)
        return state

    async def ingest(self, state: BrainState) -> BrainState:
        return await self._ingest_impl(state, use_llm_summaries=True)

    async def ingest_lightweight(self, state: BrainState) -> BrainState:
        return await self._ingest_impl(state, use_llm_summaries=False)

    async def _ingest_impl(self, state: BrainState, *, use_llm_summaries: bool) -> BrainState:
        events = state.get("events")
        if events is None:
            raw_events = self.runner.fetch_all_events()
            events = [event for event in raw_events if event.metadata.get("mode") != "mock"]
            if not events:
                events = list(raw_events)
            events.extend(self._drain_event_bus())
            db_events = self.database.recent_events(limit=20)
            events.extend([event for event in db_events if event.kind == "brain_dump"])

        enriched: list[ContextEvent] = []
        for event in events:
            if not event.entities:
                event.entities = [entity.to_dict() for entity in self.extractor.extract(f"{event.title}\n{event.body}")]
            semantic_summary = (
                await self._semantic_summary(event)
                if use_llm_summaries
                else self._fallback_summary(event)
            )
            event.metadata["semantic_summary"] = semantic_summary
            if self.event_bus is not None:
                self.event_bus.publish(event)
            stored = self.database.add_event(event, semantic_summary=semantic_summary)
            self.vector_store.index_event(stored, summary=semantic_summary)
            self.graph.link_event(stored)
            enriched.append(stored)
        return {"events": enriched}

    async def prioritize(self, state: BrainState) -> BrainState:
        ranked = self.retriever.rank_events()
        return {"prioritized": ranked}

    async def retrieve_context(self, state: BrainState) -> BrainState:
        retrievals: list[dict[str, Any]] = []
        top_ranked = state.get("prioritized", [])[:8]
        for hit in top_ranked:
            result = self.retriever.retrieve(f"{hit.event.title}\n{hit.summary}", limit=5)
            supporting_hits = [item for item in result.hits if item.event.id != hit.event.id]
            retrievals.append(
                {
                    "event_id": hit.event.id,
                    "score": hit.score,
                    "supporting_hits": supporting_hits,
                    "structured": result.structured.to_dict(),
                }
            )
        return {"retrievals": retrievals}

    async def plan(self, state: BrainState) -> BrainState:
        plans: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []
        for hit in state.get("prioritized", []):
            if hit.score < 0.45:
                continue
            context_bundle = self._context_bundle(state, hit.event.id)
            self.database.increment_event_access(hit.event.id, reason="planning")
            recommendation = self._recommend(hit, context_bundle)
            recommendations.append(recommendation)
            if hit.score < 0.6:
                continue
            steps = await self.decomposer.decompose(
                f"Decide what to do about this event: {hit.event.title}\n{hit.event.body}",
                context=self._planning_context(hit, context_bundle, recommendation),
            )
            plans.append(
                {
                    "event_id": hit.event.id,
                    "score": hit.score,
                    "priority_score": hit.priority_score,
                    "recommendation_type": recommendation["category"],
                    "steps": [step.__dict__ for step in steps],
                }
            )
        recommendations.sort(key=lambda item: float(item["score"]), reverse=True)
        return {"recommendations": recommendations, "plans": plans}

    async def route(self, state: BrainState) -> BrainState:
        import asyncio
        existing_requests = self.executor.approval_gate.store.list()
        handled_event_ids = {
            str(request.action.get("source_event_id"))
            for request in existing_requests
            if request.action.get("source_event_id")
        }

        plan_lookup = {plan["event_id"]: plan for plan in state.get("plans", [])}
        recommendation_lookup = {item["event_id"]: item for item in state.get("recommendations", [])}

        actions: list[dict[str, Any]] = []
        
        async def evaluate_agents(hit, context):
            # Run agents concurrently
            decisions = await asyncio.gather(*(
                asyncio.to_thread(agent.handle, hit.event, context)
                for agent in self.agents if agent.can_handle(hit.event)
            ))
            return decisions

        for hit in state.get("prioritized", []):
            if hit.score < 0.5 or hit.event.id in handled_event_ids:
                continue
            context = {
                "retrieval": self._context_bundle(state, hit.event.id),
                "recommendation": recommendation_lookup.get(hit.event.id, {}),
                "score": hit.score,
                "priority_score": hit.priority_score,
            }
            decisions = await evaluate_agents(hit, context)
            for decision in decisions:
                for action in decision.actions:
                    action.setdefault("risk", "medium")
                    action.setdefault("source_event_id", hit.event.id)
                    action["effective_score"] = hit.score
                    action["priority_score"] = hit.priority_score
                    action["recommendation_type"] = context["recommendation"].get("category", "review")
                    actions.append(action)

        for event_id, plan in plan_lookup.items():
            if event_id in handled_event_ids:
                continue
            for step in plan.get("steps", []):
                action = dict(step["action"])
                action.setdefault("risk", step.get("risk", "medium"))
                action.setdefault("source_event_id", event_id)
                action["effective_score"] = float(plan.get("score", 0.0))
                action["priority_score"] = float(plan.get("priority_score", 0.0))
                action["recommendation_type"] = str(plan.get("recommendation_type", "review"))
                actions.append(action)

        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for action in sorted(actions, key=lambda item: float(item.get("effective_score", 0.0)), reverse=True):
            key = (
                str(action.get("type")),
                str(action.get("plugin")),
                str(action.get("source_event_id")),
            )
            if key not in seen:
                seen.add(key)
                deduped.append(action)
        return {"actions": deduped}

    async def approval_execute(self, state: BrainState) -> BrainState:
        store = self.executor.approval_gate.store
        existing_keys: set[tuple[str, str, str]] = {
            (
                str(request.action.get("type")),
                str(request.action.get("plugin")),
                str(request.action.get("source_event_id")),
            )
            for request in store.list()
            if request.status == "pending"
        }
        results: list[dict[str, Any]] = []
        for action in sorted(
            state.get("actions", []),
            key=lambda item: float(item.get("effective_score", 0.0)),
            reverse=True,
        ):
            key = (
                str(action.get("type")),
                str(action.get("plugin")),
                str(action.get("source_event_id")),
            )
            if key in existing_keys:
                results.append({"status": "already_pending", "key": str(key), "effective_score": action.get("effective_score", 0.0)})
                continue
            result = self.executor.approval_gate.evaluate(action)
            results.append(result | {"effective_score": action.get("effective_score", 0.0)})
        return {"results": results}

    async def _semantic_summary(self, event: ContextEvent) -> str:
        if self.llm is not None and self.llm.is_configured():
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Summarize the event for semantic memory. "
                        "Keep it under 2 sentences and preserve who, what, and time-sensitive facts."
                    ),
                },
                {"role": "user", "content": f"Title: {event.title}\nBody: {event.body}"},
            ]
            try:
                response = await self.llm.complete(LLMRequest(messages=messages, temperature=0.1, max_tokens=140))
                summary = response.content.strip()
                if summary:
                    return summary
            except LLMUnavailable:
                pass
            except Exception:
                pass
        return self._fallback_summary(event)

    def _fallback_summary(self, event: ContextEvent) -> str:
        raw = f"{event.title}. {event.body}".strip()
        raw = " ".join(raw.split())
        return raw[:280]

    def _context_bundle(self, state: BrainState, event_id: str) -> dict[str, Any]:
        for item in state.get("retrievals", []):
            if item.get("event_id") == event_id:
                return item
        return {"event_id": event_id, "supporting_hits": [], "structured": {"contacts": [], "preferences": [], "entities": [], "query_entities": []}}

    def _drain_event_bus(self) -> list[ContextEvent]:
        if self.event_bus is None:
            return []
        last_id = str(self.database.get_setting("event_bus_last_id", "0-0"))
        entries = self.event_bus.read_entries(last_id=last_id, count=50, block_ms=None)
        if not entries:
            return []
        self.database.set_setting("event_bus_last_id", entries[-1][0])
        return [event for _, event in entries]

    def _recommend(self, hit: RetrievalHit, context_bundle: dict[str, Any]) -> dict[str, Any]:
        components = hit.components
        category = "review"
        next_best_action = "Keep this in the ranked memory queue."
        if components.get("urgency", 0.0) >= 0.72:
            category = "immediate_action"
            next_best_action = "Route an action immediately and surface it at the top of approvals."
        elif components.get("user_importance", 0.0) >= 0.72:
            category = "planning"
            next_best_action = "Turn this into a concrete plan and schedule follow-through."
        elif components.get("frequency", 0.0) >= 0.5:
            category = "long_term_tracking"
            next_best_action = "Keep tracking this pattern and preserve it as a recurring priority."
        structured = context_bundle.get("structured", {})
        return {
            "event_id": hit.event.id,
            "score": hit.score,
            "priority_score": hit.priority_score,
            "category": category,
            "reason": (
                f"urgency={components.get('urgency', 0.0):.2f}; "
                f"user_importance={components.get('user_importance', 0.0):.2f}; "
                f"frequency={components.get('frequency', 0.0):.2f}"
            ),
            "next_best_action": next_best_action,
            "contacts": structured.get("contacts", []),
            "preferences": structured.get("preferences", []),
            "entities": structured.get("entities", []),
        }

    def _planning_context(
        self,
        hit: RetrievalHit,
        context_bundle: dict[str, Any],
        recommendation: dict[str, Any],
    ) -> str:
        supporting = context_bundle.get("supporting_hits", [])
        support_lines = [
            f"support_event={item.event.id}; score={item.score:.2f}; summary={item.summary}"
            for item in supporting[:3]
        ]
        structured = context_bundle.get("structured", {})
        return (
            f"source={hit.event.source}; kind={hit.event.kind}; effective_score={hit.score:.2f}; "
            f"priority_score={hit.priority_score:.2f}; recommendation={recommendation['category']}\n"
            f"structured_contacts={structured.get('contacts', [])}\n"
            f"structured_preferences={structured.get('preferences', [])}\n"
            f"structured_entities={structured.get('entities', [])}\n"
            + "\n".join(support_lines)
        )
