from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from connectors.config import load_config
from connectors.runner import ConnectorRunner, PluginRegistry
from execution import ActionExecutor, ApprovalGate, ApprovalStore, AuditLogger, RateLimiter, RiskPolicy, RollbackManager
from intelligence import BrainDumpCapture, GoalDecomposer, IntentClassifier, LLMAdapter, PriorityScorer
from memory import EntityExtractor, EventBus, HybridRetriever, MemoryDatabase, MemoryDecay, VectorStore
from memory.backends import create_backend
from orchestration import BrainWorkflow


def _load_env_file() -> None:
    """Load `.env` when available so direct module imports behave like the scripts."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ModuleNotFoundError:
        return None


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


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
    _load_env_file()
    app_root = root or Path(__file__).resolve().parents[1]
    config = load_config(app_root / "config" / "user_config.yml")
    memory_config = config.get("memory", {})
    database_url = os.getenv("DATABASE_URL", memory_config.get("database_url", ""))
    backend = create_backend(database_url)
    sqlite_path = _env_text("SQLITE_PATH", str(memory_config.get("sqlite_path", "data/second_brain.sqlite3")))
    database = MemoryDatabase(
        app_root / sqlite_path,
        backend=backend,
    )
    database.initialize()
    vector_store = VectorStore(app_root / _env_text("CHROMA_PATH", str(memory_config.get("chroma_path", "data/chroma"))))
    event_bus = EventBus(_env_text("REDIS_URL", str(memory_config.get("redis_url", "redis://localhost:6379/0"))))
    extractor = EntityExtractor(_env_text("SPACY_MODEL", str(memory_config.get("spacy_model", "en_core_web_sm"))))
    scorer = PriorityScorer(config.get("priority", {}))
    decay = MemoryDecay(_env_float("HALF_LIFE_DAYS", float(memory_config.get("half_life_days", 30.0))))
    llm = LLMAdapter(config)
    capture = BrainDumpCapture(extractor, scorer)
    retriever = HybridRetriever(database, vector_store, decay=decay, scorer=scorer, extractor=extractor)
    registry = PluginRegistry(root_dir=app_root)
    runner = ConnectorRunner(registry)
    approval_store = ApprovalStore(database)
    user_config = config.get("user", {})
    approval_threshold = _env_text(
        "APPROVAL_RISK_THRESHOLD",
        str(user_config.get("approval_risk_threshold", "medium")),
    )
    approval_gate = ApprovalGate(approval_store, RiskPolicy(approval_threshold))
    audit_logger = AuditLogger(database)
    rollback = RollbackManager(database)
    executor = ActionExecutor(runner, approval_gate, audit_logger, rollback, RateLimiter(), database=database)
    classifier = IntentClassifier(
        llm,
        timezone_name=_env_text("USER_TIMEZONE", str(user_config.get("timezone", "UTC"))),
    )
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
        retriever=services.retriever,
        llm=services.llm,
        event_bus=services.event_bus,
    )

