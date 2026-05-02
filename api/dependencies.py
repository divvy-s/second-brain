from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from connectors.config import load_config
from connectors.runner import ConnectorRunner, PluginRegistry
from execution import ActionExecutor, ApprovalGate, ApprovalStore, AuditLogger, RateLimiter, RiskPolicy, RollbackManager
from intelligence import BrainDumpCapture, GoalDecomposer, IntentClassifier, LLMAdapter, PriorityScorer
from memory import EntityExtractor, EventBus, HybridRetriever, MemoryDatabase, VectorStore
from orchestration import BrainWorkflow


@dataclass
class AppServices:
    root: Path
    config: dict
    registry: PluginRegistry
    runner: ConnectorRunner
    database: MemoryDatabase
    vector_store: VectorStore
    event_bus: EventBus
    extractor: EntityExtractor
    scorer: PriorityScorer
    llm: LLMAdapter
    capture: BrainDumpCapture
    retriever: HybridRetriever
    approval_gate: ApprovalGate
    executor: ActionExecutor
    classifier: IntentClassifier

    def refresh_plugins(self) -> None:
        self.runner.refresh()


def build_services(root: Path | None = None) -> AppServices:
    app_root = root or Path(__file__).resolve().parents[1]
    config = load_config(app_root / "config" / "user_config.yml")
    memory_config = config.get("memory", {})
    database = MemoryDatabase(app_root / memory_config.get("sqlite_path", "data/second_brain.sqlite3"))
    database.initialize()
    vector_store = VectorStore(app_root / memory_config.get("chroma_path", "data/chroma"))
    event_bus = EventBus(memory_config.get("redis_url", "redis://localhost:6379/0"))
    extractor = EntityExtractor(memory_config.get("spacy_model", "en_core_web_sm"))
    scorer = PriorityScorer(config.get("priority", {}))
    llm = LLMAdapter(config)
    capture = BrainDumpCapture(extractor, scorer)
    retriever = HybridRetriever(database, vector_store)
    registry = PluginRegistry(root_dir=app_root)
    runner = ConnectorRunner(registry)
    approval_store = ApprovalStore(database)
    user_config = config.get("user", {})
    approval_gate = ApprovalGate(approval_store, RiskPolicy(user_config.get("approval_risk_threshold", "medium")))
    audit_logger = AuditLogger(database)
    rollback = RollbackManager(database)
    executor = ActionExecutor(runner, approval_gate, audit_logger, rollback, RateLimiter())
    classifier = IntentClassifier(llm)
    return AppServices(
        root=app_root,
        config=config,
        registry=registry,
        runner=runner,
        database=database,
        vector_store=vector_store,
        event_bus=event_bus,
        extractor=extractor,
        scorer=scorer,
        llm=llm,
        capture=capture,
        retriever=retriever,
        approval_gate=approval_gate,
        executor=executor,
        classifier=classifier,
    )


def build_workflow(services: AppServices) -> BrainWorkflow:
    return BrainWorkflow(
        runner=services.runner,
        database=services.database,
        vector_store=services.vector_store,
        extractor=services.extractor,
        scorer=services.scorer,
        decomposer=GoalDecomposer(services.llm),
        executor=services.executor,
    )

