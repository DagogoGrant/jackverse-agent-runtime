from datetime import datetime
import os
from pathlib import Path
import sys
from typing import Any
import urllib.parse
from dotenv import load_dotenv
from prometheus_client import CollectorRegistry

from harness.agent import (
    AgentFactory,
    DelegateTaskTool,
    ExecutionBudget,
    HierarchicalBudgetLedger,
    ReActController,
    SubAgentManager,
    build_tool_catalog,
    get_standard_specialist_specs,
)
import dataclasses
from harness.config import AppConfig, MCPServerConfig, load_config
from harness.llm import LLMClient, LLMProvider, create_llm_provider, run_llm_diagnostics
from harness.mcp import MCPClient, MCPToolAdapter
from harness.memory import (
    BaselineAdmissionPolicy,
    MemoryFirewall,
    MemoryManager,
    MemoryRetriever,
    SQLiteMemoryStore,
)
from harness.memory.base import MemoryStatus, MemoryType
from harness.memory.embeddings import (
    MemoryEmbeddingIndexer,
    SentenceTransformerEmbeddingProvider,
)
from harness.memory.strategies import (
    BM25FTS5Strategy,
    DenseSemanticStrategy,
    HybridRRFStrategy,
    LexicalOverlapStrategy,
)
from harness.observability import (
    MetricsServer,
    OpenTelemetryObserver,
    PrometheusObserver,
    StructuredLogObserver,
    start_metrics_server,
)
from harness.permissions import (
    ConfirmationHandler,
    ConsoleConfirmationHandler,
    DeterministicConfirmationHandler,
    PermissionManager,
)
from harness.runtime.events import LifecycleEventBus
from harness.tools.base import ToolSource
from harness.tools.executor import ToolExecutor
from harness.tools.filesystem import (
    CreateDirectoryTool,
    CreateFileTool,
    ListDirectoryTool,
    ModifyFileTool,
    ReadFileTool,
    SearchFilesTool,
)
from harness.tools.registry import ToolRegistry
from harness.tools.workspace import Workspace


def build_controller(
    config: AppConfig,
    api_key: str | None = None,
    event_bus: LifecycleEventBus | None = None,
    enable_delegation: bool = False,
    confirmation_handler: ConfirmationHandler | None = None,
    provider: LLMProvider | None = None,
) -> tuple[ReActController, Workspace, ToolRegistry]:
    """Compose the production ReActController with all registered capabilities."""
    bus = event_bus if event_bus is not None else LifecycleEventBus()

    workspace_path = Path(config.tools.workspace_root).resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    (workspace_path / "mcp_external_demo").mkdir(parents=True, exist_ok=True)
    workspace = Workspace(workspace_path)

    if confirmation_handler is None:
        if config.permissions.confirmation_handler == "deterministic":
            confirmation_handler = DeterministicConfirmationHandler(always_allow=True)
        else:
            confirmation_handler = ConsoleConfirmationHandler()

    permission_manager = PermissionManager.from_config(
        config.permissions,
        confirmation_handler=confirmation_handler,
        workspace=workspace,
        event_bus=bus,
    )

    registry = ToolRegistry()
    executor = ToolExecutor(
        max_observation_chars=config.agent.max_observation_chars,
        event_bus=bus,
        permission_manager=permission_manager,
    )

    # Register all six filesystem tools (no deletion capability)
    registry.register(CreateDirectoryTool(workspace))
    registry.register(CreateFileTool(workspace))
    registry.register(ReadFileTool(workspace))
    registry.register(ListDirectoryTool(workspace))
    registry.register(SearchFilesTool(workspace))
    registry.register(ModifyFileTool(workspace))

    # Dynamically discover and register external MCP tools if configured
    for server_cfg in config.mcp_servers:
        mcp_client = MCPClient(server_cfg)
        for spec in mcp_client.list_tools():
            adapter = MCPToolAdapter(spec, mcp_client, prefix=server_cfg.prefix)
            registry.register(adapter)

    if provider is not None:
        llm_client = provider
    else:
        effective_key = api_key if (api_key is not None) else config.llm.api_key
        effective_llm_config = dataclasses.replace(config.llm, api_key=effective_key)
        llm_client = create_llm_provider(effective_llm_config, event_bus=bus)

    budget = ExecutionBudget(
        max_steps=config.agent.max_steps,
        max_tool_calls=config.agent.max_tool_calls,
        max_runtime_seconds=config.agent.max_runtime_seconds,
        max_observation_chars=config.agent.max_observation_chars,
    )

    memory_manager: MemoryManager | None = None
    if getattr(config, "memory", None) and config.memory.enabled:
        storage_path = Path(config.memory.storage_path)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        store = SQLiteMemoryStore(storage_path)
        admission = MemoryFirewall(
            store=store,
            max_entry_chars=config.memory.max_entry_chars,
        )
        mem_cfg = config.memory
        strategy_name = mem_cfg.retrieval_strategy.lower()
        if strategy_name == "hybrid":
            strategy_name = "hybrid_rrf"
        readiness = mem_cfg.embedding.index_readiness.lower()
        if readiness == "skip":
            readiness = "sparse_only"

        provider: SentenceTransformerEmbeddingProvider | None = None
        indexer: MemoryEmbeddingIndexer | None = None

        # Build embedding provider only if not sparse_only and strategy needs embeddings
        if readiness != "sparse_only" and strategy_name in ("dense", "hybrid_rrf"):
            provider = SentenceTransformerEmbeddingProvider(
                model_name=mem_cfg.embedding.model,
                dimension=mem_cfg.embedding.dimension,
                revision=mem_cfg.embedding.revision,
                query_prompt_name=mem_cfg.embedding.query_prompt_name,
                query_prefix=mem_cfg.embedding.query_prefix,
                local_files_only=True,
                device="cpu",
            )
            indexer = MemoryEmbeddingIndexer(store, provider)

            if readiness == "eager":
                # Eagerly backfill missing embeddings for eligible accepted declarative memories
                eligible = [
                    m for m in store.list_all()
                    if m.status == MemoryStatus.ACCEPTED and m.memory_type == MemoryType.DECLARATIVE
                ]
                indexer.ensure_embeddings(eligible)

        # Select retrieval strategy
        if strategy_name == "lexical":
            strategy = LexicalOverlapStrategy()
        elif strategy_name == "bm25":
            strategy = BM25FTS5Strategy(store)
        elif strategy_name == "dense":
            if provider is None:
                raise ValueError(
                    "Dense retrieval cannot be used with index_readiness='sparse_only' "
                    "because sparse_only guarantees zero model loading."
                )
            strategy = DenseSemanticStrategy(
                store=store,
                provider=provider,
                threshold=mem_cfg.embedding.threshold,
                missing_embedding=mem_cfg.embedding.missing_embedding,
            )
        elif strategy_name == "hybrid_rrf":
            sparse_strat = BM25FTS5Strategy(store)
            if provider is None:
                # In sparse_only mode, fall back to pure sparse retrieval with zero model loading
                strategy = sparse_strat
            else:
                dense_strat = DenseSemanticStrategy(
                    store=store,
                    provider=provider,
                    threshold=mem_cfg.embedding.threshold,
                    missing_embedding=mem_cfg.embedding.missing_embedding,
                )
                strategy = HybridRRFStrategy(
                    sparse=sparse_strat,
                    dense=dense_strat,
                    candidate_pool=mem_cfg.hybrid.candidate_pool,
                    rrf_k=mem_cfg.hybrid.rrf_k,
                    sparse_weight=mem_cfg.hybrid.sparse_weight,
                    dense_weight=mem_cfg.hybrid.dense_weight,
                    sparse_min_coverage=mem_cfg.sparse.min_coverage,
                    sparse_bm25_cutoff=mem_cfg.sparse.bm25_cutoff,
                    sparse_stopword_policy_version=mem_cfg.sparse.stopword_policy,
                    enable_relevance_gate=mem_cfg.hybrid.relevance_gate,
                )
        else:
            raise ValueError(f"Unsupported retrieval_strategy: '{strategy_name}'")

        retriever = MemoryRetriever(
            store=store,
            strategy=strategy,
            candidate_limit=mem_cfg.candidate_limit,
            max_retrieved=mem_cfg.max_retrieved,
            max_context_chars=mem_cfg.max_context_chars,
        )
        memory_manager = MemoryManager(
            store=store,
            admission_policy=admission,
            retriever=retriever,
            indexer=indexer if readiness != "sparse_only" else None,
            event_bus=bus,
        )

    if enable_delegation:
        # Build canonical tool catalog for sub-agent delegation
        tool_catalog = build_tool_catalog([registry.get(s.name) for s in registry.list_specs()])
        specialist_specs = get_standard_specialist_specs()
        agent_factory = AgentFactory(
            specs=specialist_specs,
            tool_catalog=tool_catalog,
            llm_client=llm_client,
            permission_manager=permission_manager,
            event_bus=bus,
        )
        sub_agent_manager = SubAgentManager(
            agent_factory=agent_factory,
            budget_ledger=None,  # Dynamic per-turn budget ledger via parent_ctx
            event_bus=bus,
        )
        delegate_tool = DelegateTaskTool(sub_agent_manager)

        # Under least-privilege multi-principal architecture (Review G), orchestrator
        # delegates specialized capabilities. Rebuild orchestrator registry without direct MCP tools.
        orchestrator_registry = ToolRegistry()
        for spec in registry.list_specs():
            tool = registry.get(spec.name)
            if tool.spec.source != ToolSource.MCP:
                orchestrator_registry.register(tool)
        orchestrator_registry.register(delegate_tool)
        registry = orchestrator_registry

    controller = ReActController(
        llm_client=llm_client,
        tool_registry=registry,
        tool_executor=executor,
        budget=budget,
        memory_manager=memory_manager,
        event_bus=bus,
        budget_ledger=None,  # Each independent root turn receives a fresh HierarchicalBudgetLedger
        max_delegation_depth=2,
        max_delegations=5,
    )

    return controller, workspace, registry


def print_banner(
    config: AppConfig,
    workspace: Workspace,
    registry: ToolRegistry,
    metrics_server: MetricsServer | None = None,
) -> None:
    """Print a clean startup banner with basic runtime summary and available commands."""
    tool_count = len(registry.list_specs())
    mcp_count = len(config.mcp_servers) if config.mcp_servers else 0
    mem_enabled = getattr(config, "memory", None) and config.memory.enabled
    mem_status = (
        f"enabled ({config.memory.storage_path})"
        if mem_enabled
        else "disabled"
    )

    print("==================================================")
    print("             JACKVERSE AGENT RUNTIME")
    print("==================================================")
    print(f"Model       : {config.llm.model}")
    print(f"LLM Provider: {config.llm.provider}")
    print(f"LLM Base URL: {config.llm.base_url}")
    print(f"Auth Status : {'configured (redacted)' if config.llm.api_key else 'anonymous / not required'}")
    print(f"Workspace   : {workspace.root}")
    print(f"Memory      : {mem_status}")
    if mem_enabled:
        strat_display = config.memory.retrieval_strategy.replace("_rrf", "")
        model_short = config.memory.embedding.model.split("/")[-1].lower()
        cand_scope = "full" if config.memory.candidate_limit is None else f"top {config.memory.candidate_limit}"
        print(f"Retrieval   : {strat_display} ({model_short}, readiness={config.memory.embedding.index_readiness}, scope={cand_scope})")
    print(f"MCP Servers : {mcp_count} configured")
    print(f"Max Steps   : {config.agent.max_steps}")
    print(f"Tools       : {tool_count} available")
    if metrics_server is not None:
        print(f"Metrics     : http://{metrics_server.host}:{metrics_server.port}/metrics")
    elif getattr(config, "observability", None) and config.observability.metrics.enabled:
        print(f"Metrics     : enabled ({config.observability.metrics.port})")
    else:
        print("Metrics     : disabled")
    if getattr(config, "observability", None) and config.observability.tracing.enabled:
        print(f"Tracing     : enabled ({config.observability.tracing.endpoint})")
    else:
        print("Tracing     : disabled")
    print("")
    print("Commands:")
    print("  /help     Show available commands")
    print("  /reset    Reset conversation context (starts fresh evaluation task)")
    print("  /tools    Show registered tools")
    print("  /mcp      Show MCP server and discovered-tool status")
    print("  /memory   Show persistent-memory lifecycle summary")
    print("  /config   Show runtime configuration")
    print("  exit      Exit the harness")
    print("--------------------------------------------------\n")


def print_help() -> None:
    """Print the available local CLI commands."""
    print("Available commands:")
    print("  /help     Show available commands")
    print("  /reset    Reset conversation context (starts fresh evaluation task)")
    print("  /tools    Show registered tools")
    print("  /mcp      Show MCP server and discovered-tool status")
    print("  /memory   Show persistent-memory lifecycle summary")
    print("  /config   Show runtime configuration")
    print("  exit      Exit the harness (or quit)")
    print("")


def print_tools(registry: ToolRegistry) -> None:
    """Print registered tools one per line."""
    print("Available tools:")
    for spec in registry.list_specs():
        print(f"  - {spec.name}")
    print("")


def _sanitize_mcp_target(server: MCPServerConfig) -> str:
    """Format a safe, sanitized target string without credentials or query secrets."""
    if server.url:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(server.url)
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))

    sanitized_args: list[str] = []
    sensitive_keywords = ("token", "key", "secret", "password", "auth", "bearer")
    skip_next = False
    for arg in server.args:
        if skip_next:
            sanitized_args.append("[REDACTED]")
            skip_next = False
            continue
        lower_arg = arg.lower()
        if any(f"--{kw}" in lower_arg or f"-{kw}" in lower_arg for kw in sensitive_keywords):
            if "=" in arg:
                flag, _ = arg.split("=", 1)
                sanitized_args.append(f"{flag}=[REDACTED]")
            else:
                sanitized_args.append(arg)
                skip_next = True
            continue
        if any(kw in lower_arg for kw in ("bearer", "secret", "password")):
            sanitized_args.append("[REDACTED]")
            continue
        sanitized_args.append(arg)

    if server.auth_token_env:
        secret = os.environ.get(server.auth_token_env)
        if secret:
            sanitized_args = [a.replace(secret, "[REDACTED]") for a in sanitized_args]

    if sanitized_args:
        return f"{server.command} {' '.join(sanitized_args)}"
    return server.command


def print_mcp(config: AppConfig, registry: ToolRegistry) -> None:
    """Print detailed status of configured external MCP servers and discovered tools."""
    if not config.mcp_servers:
        print("No external MCP servers configured.\n")
        return

    print(f"MCP Servers ({len(config.mcp_servers)} configured):")
    for server in config.mcp_servers:
        transport_type = "streamable-http" if server.url else "stdio"
        target = _sanitize_mcp_target(server)
        print(f"  • {server.name} [{transport_type}]")
        print(f"    Target    : {target}")

        server_tools: list[Any] = []
        for spec in registry.list_specs():
            try:
                tool = registry.get(spec.name)
                if isinstance(tool, MCPToolAdapter):
                    cfg = getattr(tool.client, "config", None)
                    cfg_name = getattr(cfg, "name", None)
                    server_name = getattr(tool.client, "server_name", None)
                    if cfg_name == server.name or server_name == server.name:
                        server_tools.append(spec)
            except Exception:
                pass

        if server_tools:
            print("    Discovery : successful")
            print(f"    Tools     : {len(server_tools)}")
            for t in server_tools:
                desc = t.description.splitlines()[0] if t.description else "No description"
                print(f"      - {t.name}: {desc}")
        else:
            print("    Discovery : successful (0 tools discovered)")
    print("")


def print_memory(config: AppConfig, now: str | Any | None = None) -> None:
    """Print aggregate persistent-memory lifecycle summary (safe, zero raw data)."""
    if not (getattr(config, "memory", None) and config.memory.enabled):
        print("Memory Status:")
        print("  Enabled              : no\n")
        return

    db_path = Path(config.memory.storage_path)
    strat_display = config.memory.retrieval_strategy.replace("_rrf", "")
    model_short = config.memory.embedding.model.split("/")[-1].lower()
    cand_scope = "full" if config.memory.candidate_limit is None else f"top {config.memory.candidate_limit}"

    print("Memory Status:")
    print("  Enabled              : yes")
    print(f"  Storage Path         : {db_path}")
    print(f"  Memory Retrieval     : {strat_display}")
    print(f"  Semantic Encoder     : {model_short}")
    print(f"  Index Policy         : {config.memory.embedding.index_readiness}")
    print(f"  Candidate Scope      : {cand_scope}")

    if not db_path.exists():
        print("  Dense Cache          : 0 / 0 eligible")
        print("  Active Declarative   : 0")
        print("  Procedural Lessons   : 0")
        print("  Quarantined Inputs   : 0")
        print("  Superseded Records   : 0")
        print("  Total Records        : 0")
        print("  Note                 : Database file does not exist yet (no memories stored)\n")
        return

    try:
        store = SQLiteMemoryStore(db_path)
        active_decl = store.count_active(memory_type=MemoryType.DECLARATIVE, now=now)
        procedural = store.count_active(memory_type=MemoryType.PROCEDURAL, now=now)
        quarantined = store.count(status=MemoryStatus.QUARANTINED)
        superseded = store.count(status=MemoryStatus.SUPERSEDED)
        total = store.count()
        fts_count = store.count_fts()
        cached_embeddings = store.count_embeddings()
        store.close()

        print(f"  Dense Cache          : {cached_embeddings} / {active_decl} eligible")
        print(f"  Active Declarative   : {active_decl}")
        print(f"  Procedural Lessons   : {procedural}")
        print(f"  Quarantined Inputs   : {quarantined}")
        print(f"  Superseded Records   : {superseded}")
        print(f"  FTS5 Indexed Records : {fts_count}")
        print(f"  Total Records        : {total}\n")
    except Exception as e:
        print(f"  Error reading memory database: {e}\n")


def print_config(config: AppConfig, workspace: Workspace) -> None:
    """Print safe runtime configuration (never reveals API key)."""
    mem_enabled = getattr(config, "memory", None) and config.memory.enabled
    mem_status = "enabled" if mem_enabled else "disabled"
    mcp_count = len(config.mcp_servers) if config.mcp_servers else 0

    print("Runtime configuration:")
    print(f"  Model         : {config.llm.model}")
    print(f"  LLM Provider  : {config.llm.provider}")
    print(f"  LLM Base URL  : {config.llm.base_url}")
    print(f"  Authentication: {'configured (redacted)' if config.llm.api_key else 'anonymous / not required'}")
    print(f"  Workspace     : {workspace.root}")
    print(f"  Max Steps     : {config.agent.max_steps}")
    print(f"  Memory        : {mem_status}")
    if mem_enabled and config.memory.storage_path:
        print(f"  Memory Path   : {config.memory.storage_path}")
    if mem_enabled:
        strat_display = config.memory.retrieval_strategy.replace("_rrf", "")
        model_short = config.memory.embedding.model.split("/")[-1].lower()
        cand_scope = "full" if config.memory.candidate_limit is None else f"top {config.memory.candidate_limit}"
        print(f"  Memory Retrieval : {strat_display}")
        print(f"  Semantic Encoder : {model_short}")
        print(f"  Index Policy     : {config.memory.embedding.index_readiness}")
        print(f"  Candidate Scope  : {cand_scope}")
    print(f"  MCP Servers   : {mcp_count}")
    print(f"  Timeout       : {config.llm.timeout}s")
    print(f"  Max Retries   : {config.llm.max_retries}")
    print(f"  Retry Backoff : {config.llm.retry_backoff}s")
    if getattr(config, "observability", None) and config.observability.metrics.enabled:
        print(f"  Metrics       : enabled (http://{config.observability.metrics.host}:{config.observability.metrics.port}/metrics, required={config.observability.metrics.required})")
    else:
        print("  Metrics       : disabled")
    if getattr(config, "observability", None) and config.observability.tracing.enabled:
        print(f"  Tracing       : enabled ({config.observability.tracing.endpoint}, service={config.observability.tracing.service_name})")
    else:
        print("  Tracing       : disabled")
    if getattr(config, "observability", None) and config.observability.logging.structured_enabled:
        print("  Structured Log: enabled")
    else:
        print("  Structured Log: disabled")
    print("")


def main() -> None:
    """CLI entrypoint for interactive multi-turn agent-harness execution."""
    if any(arg in ("--help", "-h") for arg in sys.argv[1:]):
        print("Usage: python -m harness [OPTIONS]")
        print("")
        print("Options:")
        print("  doctor           Run LLM provider diagnostic and connectivity checks")
        print("  --tui, tui       Launch the Live Operator Console (Textual TUI)")
        print("  --help, -h       Show this help message and exit")
        print("")
        print("Interactive CLI Commands (in non-TUI mode):")
        print("  /help            Show runtime commands")
        print("  /tools           Show registered tools")
        print("  /mcp             Show MCP server status")
        print("  /memory          Show persistent memory summary")
        print("  /config          Show active configuration")
        print("  exit             Exit the harness")
        sys.exit(0)

    load_dotenv()
    config_path = Path(
        os.environ.get("AGENT_HARNESS_CONFIG", "config/config.yaml")
    )

    if any(arg == "doctor" for arg in sys.argv[1:]):
        try:
            config = load_config(config_path)
        except Exception as e:
            print(f"Error loading configuration from '{config_path}': {e}", file=sys.stderr)
            sys.exit(1)
        report = run_llm_diagnostics(config.llm)
        print(report.render())
        sys.exit(0 if report.all_passed else 1)

    use_tui = any(arg in ("--tui", "tui") for arg in sys.argv[1:])

    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"Error loading configuration from '{config_path}': {e}", file=sys.stderr)
        sys.exit(1)

    api_key = config.llm.api_key or os.environ.get("LLM_API_KEY")
    is_local_endpoint = any(
        h in (config.llm.base_url or "").lower()
        for h in ("localhost", "127.0.0.1", "::1", "host.docker.internal", "ollama")
    )
    if not api_key and not is_local_endpoint:
        print("Error: LLM_API_KEY environment variable is not set.", file=sys.stderr)
        print("Set it before running: export LLM_API_KEY='your-key-here'", file=sys.stderr)
        sys.exit(1)

    # Process-level observability runtime composition root
    process_event_bus = LifecycleEventBus()
    collector_registry = CollectorRegistry(auto_describe=True)
    prometheus_observer = PrometheusObserver(
        registry=collector_registry,
        default_model=config.llm.model,
    )
    process_event_bus.subscribe(prometheus_observer)

    metrics_server: MetricsServer | None = None
    if getattr(config, "observability", None) and config.observability.metrics.enabled:
        metrics_server = start_metrics_server(
            registry=collector_registry,
            host=config.observability.metrics.host,
            port=config.observability.metrics.port,
            required=config.observability.metrics.required,
        )

    otel_observer: OpenTelemetryObserver | None = None
    if getattr(config, "observability", None) and config.observability.tracing.enabled:
        otel_observer = OpenTelemetryObserver(
            endpoint=config.observability.tracing.endpoint,
            service_name=config.observability.tracing.service_name,
            environment=config.observability.tracing.environment,
            export_timeout_seconds=config.observability.tracing.export_timeout_seconds,
        )
        process_event_bus.subscribe(otel_observer)

    log_observer: StructuredLogObserver | None = None
    if getattr(config, "observability", None) and config.observability.logging.structured_enabled:
        log_observer = StructuredLogObserver(
            destination=config.observability.logging.file_path,
        )
        process_event_bus.subscribe(log_observer)

    tui_confirmation_handler: ConfirmationHandler | None = None
    tui_store: Any | None = None
    if use_tui:
        from harness.tui.app import AgentHarnessApp
        from harness.tui.confirmation import TUIConfirmationHandler
        from harness.tui.observer import TUIObserver
        from harness.tui.store import RuntimeStateStore

        tui_store = RuntimeStateStore()
        tui_observer = TUIObserver(tui_store)
        process_event_bus.subscribe(tui_observer)
        tui_confirmation_handler = TUIConfirmationHandler()

    try:
        controller, workspace, registry = build_controller(
            config=config,
            api_key=api_key,
            event_bus=process_event_bus,
            enable_delegation=True,
            confirmation_handler=tui_confirmation_handler,
        )

        if use_tui and tui_store is not None:
            app = AgentHarnessApp(
                controller=controller,
                config=config,
                workspace=workspace,
                registry=registry,
                event_bus=process_event_bus,
                store=tui_store,
                confirmation_handler=tui_confirmation_handler,
                memory_manager=controller.memory_manager,
            )
            app.run()
            return

        print_banner(config, workspace, registry, metrics_server=metrics_server)

        while True:
            try:
                user_input = input("You > ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nExiting...")
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                break

            if user_input == "/help":
                print_help()
                continue

            if user_input in ("/reset", "/new"):
                if hasattr(controller, "reset_conversation"):
                    controller.reset_conversation()
                print("Conversation context reset to initial system prompt. (Persistent memory preserved)\n")
                continue

            if user_input == "/tools":
                print_tools(registry)
                continue

            if user_input == "/mcp":
                print_mcp(config, registry)
                continue

            if user_input == "/memory":
                print_memory(config)
                continue

            if user_input == "/config":
                print_config(config, workspace)
                continue

            print("Agent is working...")
            try:
                response = controller.run(user_input)
                print(f"Agent > {response}\n")
            except Exception as e:
                print(f"Error during execution: {e}\n", file=sys.stderr)
    finally:
        if otel_observer is not None:
            otel_observer.shutdown()
        if log_observer is not None:
            log_observer.close()
        if metrics_server is not None:
            metrics_server.stop()



if __name__ == "__main__":
    main()
