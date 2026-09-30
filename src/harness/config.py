from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any
import urllib.parse
import yaml


class ConfigurationError(ValueError):
    """Raised when application or subsystem configuration is invalid or missing."""


@dataclass(frozen=True)
class MCPResilienceConfig:
    """Configuration for MCP retry and circuit breaker policies."""

    max_retries: int = 2
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 2.0
    backoff_multiplier: float = 2.0
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: float = 30.0
    idempotent_tools: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for an external MCP server connection (stdio or streamable_http)."""

    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"
    url: str | None = None
    auth_token_env: str | None = None
    timeout_seconds: float = 30.0
    prefix: str | None = None
    trusted_insecure_hosts: list[str] = field(default_factory=list)
    resilience: MCPResilienceConfig = field(default_factory=MCPResilienceConfig)


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for agent reasoning and execution bounds."""

    max_steps: int
    max_tool_calls: int = 25
    max_runtime_seconds: float = 60.0
    max_observation_chars: int | None = 16_000


@dataclass(frozen=True)
class LLMConfig:
    """Configuration for LLM client/provider communication."""

    base_url: str
    model: str
    temperature: float
    provider: str = "openai_compatible"
    api_key: str | None = None
    timeout: float = 30.0
    max_retries: int = 2
    retry_backoff: float = 0.5
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolsConfig:
    """Configuration for tools and workspace execution."""

    workspace_root: str


@dataclass(frozen=True)
class TransportLiveConfig:
    """Configuration for live external transport provider."""

    base_url: str = "https://v6.db.transport.rest"
    api_key_env: str | None = None
    timeout_seconds: float = 8.0
    max_retries: int = 1
    fallback_to_synthetic: bool = False


@dataclass(frozen=True)
class TransportConfig:
    """Configuration for journey planning transport providers."""

    provider: str = "synthetic"  # "synthetic" | "live"
    live: TransportLiveConfig = field(default_factory=TransportLiveConfig)


@dataclass(frozen=True)
class MemoryEmbeddingConfig:
    """Configuration for dense semantic embedding in long-term memory."""

    model: str = "Snowflake/snowflake-arctic-embed-s"
    revision: str = "e596f507467533e48a2e17c007f0e1dacc837b33"
    dimension: int = 384
    query_prompt_name: str | None = "query"
    query_prefix: str | None = None
    threshold: float = 0.55
    missing_embedding: str = "skip"  # "skip" | "error"
    index_readiness: str = "incremental"  # "eager" | "incremental" | "sparse_only"


@dataclass(frozen=True)
class MemorySparseConfig:
    """Configuration for sparse lexical/FTS5 retrieval in long-term memory."""

    min_coverage: float = 0.35
    bm25_cutoff: float = -2.5
    stopword_policy: str = "v1-standard-english-34"


@dataclass(frozen=True)
class MemoryHybridConfig:
    """Configuration for reciprocal rank fusion hybrid retrieval."""

    candidate_pool: int = 10
    rrf_k: int = 10
    sparse_weight: float = 1.0
    dense_weight: float = 1.0
    relevance_gate: bool = True


@dataclass(frozen=True)
class MemoryConfig:
    """Configuration for persistent long-term memory."""

    enabled: bool = False
    storage_path: str = ".agent_memory/memory.db"
    max_retrieved: int = 3
    max_context_chars: int = 2000
    max_entry_chars: int = 4000
    retrieval_strategy: str = "hybrid_rrf"  # "hybrid" | "hybrid_rrf" | "lexical" | "bm25" | "dense"
    candidate_limit: int | None = None
    embedding: MemoryEmbeddingConfig = field(default_factory=MemoryEmbeddingConfig)
    sparse: MemorySparseConfig = field(default_factory=MemorySparseConfig)
    hybrid: MemoryHybridConfig = field(default_factory=MemoryHybridConfig)

    # Convenience properties for backward compatibility with flat access
    @property
    def dense_model(self) -> str:
        return self.embedding.model

    @property
    def dense_revision(self) -> str:
        return self.embedding.revision

    @property
    def dense_threshold(self) -> float:
        return self.embedding.threshold

    @property
    def index_readiness(self) -> str:
        return self.embedding.index_readiness

    @property
    def missing_embedding(self) -> str:
        return self.embedding.missing_embedding

    @property
    def query_prompt_name(self) -> str | None:
        return self.embedding.query_prompt_name

    @property
    def query_prefix(self) -> str | None:
        return self.embedding.query_prefix

    @property
    def sparse_min_coverage(self) -> float:
        return self.sparse.min_coverage

    @property
    def sparse_bm25_cutoff(self) -> float:
        return self.sparse.bm25_cutoff

    @property
    def sparse_stopword_policy_version(self) -> str:
        return self.sparse.stopword_policy

    @property
    def candidate_pool(self) -> int:
        return self.hybrid.candidate_pool

    @property
    def rrf_k(self) -> int:
        return self.hybrid.rrf_k

    @property
    def sparse_weight(self) -> float:
        return self.hybrid.sparse_weight

    @property
    def dense_weight(self) -> float:
        return self.hybrid.dense_weight

    @property
    def enable_relevance_gate(self) -> bool:
        return self.hybrid.relevance_gate


@dataclass(frozen=True)
class MetricsConfig:
    """Configuration for Prometheus metrics exposition."""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 9101
    required: bool = True


@dataclass(frozen=True)
class TracingConfig:
    """Configuration for OpenTelemetry distributed tracing."""

    enabled: bool = False
    endpoint: str = "http://127.0.0.1:4318/v1/traces"
    service_name: str = "agent-harness"
    environment: str = "development"
    export_timeout_seconds: float = 5.0


@dataclass(frozen=True)
class LoggingConfig:
    """Configuration for structured JSON logging."""

    structured_enabled: bool = False
    file_path: str | None = None


@dataclass(frozen=True)
class ObservabilityConfig:
    """Configuration for runtime observability (metrics, tracing, structured logging)."""

    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)



@dataclass(frozen=True)
class PolicyRuleConfig:
    """Configuration for an individual action authorization rule."""

    name: str
    decision: str
    priority: int = 100
    tool_pattern: str = "*"
    source: str | None = None
    server_name: str | None = None
    role: str | None = None
    risk_level: str = "mutating"
    resource_pattern: str | None = None
    argument_matches: Mapping[str, str] | None = None
    match_risk_level: str | None = None


def default_permission_rules() -> list[PolicyRuleConfig]:
    return [
        PolicyRuleConfig(name="allow_filesystem_reads", decision="allow", priority=100, tool_pattern="read_file", source="builtin", risk_level="read_only"),
        PolicyRuleConfig(name="allow_filesystem_list", decision="allow", priority=100, tool_pattern="list_directory", source="builtin", risk_level="read_only"),
        PolicyRuleConfig(name="allow_filesystem_search", decision="allow", priority=100, tool_pattern="search_files", source="builtin", risk_level="read_only"),
        PolicyRuleConfig(name="confirm_filesystem_modify", decision="require_confirmation", priority=100, tool_pattern="modify_file", source="builtin", risk_level="mutating"),
        PolicyRuleConfig(name="confirm_filesystem_create_file", decision="require_confirmation", priority=100, tool_pattern="create_file", source="builtin", risk_level="mutating"),
        PolicyRuleConfig(name="confirm_filesystem_create_dir", decision="require_confirmation", priority=100, tool_pattern="create_directory", source="builtin", risk_level="mutating"),
        PolicyRuleConfig(name="allow_transport_mcp", decision="allow", priority=100, tool_pattern="*", source="mcp", server_name="transport_service", risk_level="read_only"),
        PolicyRuleConfig(name="allow_transport_find", decision="allow", priority=100, tool_pattern="find_connection", risk_level="read_only"),
        PolicyRuleConfig(name="allow_transport_station", decision="allow", priority=100, tool_pattern="get_station_info", risk_level="read_only"),
        PolicyRuleConfig(name="allow_test_echo_mcp", decision="allow", priority=100, tool_pattern="*", source="mcp", server_name="test_echo_server", risk_level="read_only"),
        PolicyRuleConfig(name="allow_echo_tool", decision="allow", priority=100, tool_pattern="*echo*", risk_level="read_only"),
        PolicyRuleConfig(name="allow_add_tool", decision="allow", priority=100, tool_pattern="add", risk_level="read_only"),
    ]


@dataclass(frozen=True)
class PermissionsConfig:
    """Configuration for the permissions and action authorization subsystem."""

    default_stance: str = "deny"
    confirmation_handler: str = "deterministic"
    rules: list[PolicyRuleConfig] = field(default_factory=default_permission_rules)


@dataclass(frozen=True)
class AppConfig:
    """Root configuration object for the agent harness."""

    agent: AgentConfig
    llm: LLMConfig
    tools: ToolsConfig
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    transport: TransportConfig = field(default_factory=TransportConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        """Load configuration from a YAML file path."""
        return load_config(path)


def _parse_resilience_config(server_name: str, resilience_data: Any) -> MCPResilienceConfig:
    """Parse and validate resilience configuration for an MCP server."""
    if resilience_data is None:
        return MCPResilienceConfig()
    if not isinstance(resilience_data, dict):
        raise ValueError(f"Field 'resilience' for MCP server '{server_name}' must be a dictionary.")

    raw_retries = resilience_data.get("max_retries", 2)
    try:
        max_retries = int(raw_retries)
        if max_retries < 0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Field 'max_retries' for MCP server '{server_name}' must be a non-negative integer, got '{raw_retries}'."
        ) from e

    raw_init_backoff = resilience_data.get("initial_backoff_seconds", 0.5)
    try:
        initial_backoff_seconds = float(raw_init_backoff)
        if initial_backoff_seconds < 0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Field 'initial_backoff_seconds' for MCP server '{server_name}' must be non-negative, got '{raw_init_backoff}'."
        ) from e

    raw_max_backoff = resilience_data.get("max_backoff_seconds", 2.0)
    try:
        max_backoff_seconds = float(raw_max_backoff)
        if max_backoff_seconds < initial_backoff_seconds:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Field 'max_backoff_seconds' for MCP server '{server_name}' must be >= initial_backoff_seconds, got '{raw_max_backoff}'."
        ) from e

    raw_multiplier = resilience_data.get("backoff_multiplier", 2.0)
    try:
        backoff_multiplier = float(raw_multiplier)
        if backoff_multiplier < 1.0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Field 'backoff_multiplier' for MCP server '{server_name}' must be >= 1.0, got '{raw_multiplier}'."
        ) from e

    raw_threshold = resilience_data.get("circuit_failure_threshold", 3)
    try:
        circuit_failure_threshold = int(raw_threshold)
        if circuit_failure_threshold <= 0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Field 'circuit_failure_threshold' for MCP server '{server_name}' must be a positive integer, got '{raw_threshold}'."
        ) from e

    raw_cooldown = resilience_data.get("circuit_cooldown_seconds", 30.0)
    try:
        circuit_cooldown_seconds = float(raw_cooldown)
        if circuit_cooldown_seconds < 0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Field 'circuit_cooldown_seconds' for MCP server '{server_name}' must be non-negative, got '{raw_cooldown}'."
        ) from e

    raw_idempotent = resilience_data.get("idempotent_tools", [])
    if not isinstance(raw_idempotent, (list, set, tuple)) or not all(isinstance(t, str) for t in raw_idempotent):
        raise ValueError(
            f"Field 'idempotent_tools' for MCP server '{server_name}' must be a list of strings, got '{raw_idempotent}'."
        )
    idempotent_tools = frozenset(raw_idempotent)

    return MCPResilienceConfig(
        max_retries=max_retries,
        initial_backoff_seconds=initial_backoff_seconds,
        max_backoff_seconds=max_backoff_seconds,
        backoff_multiplier=backoff_multiplier,
        circuit_failure_threshold=circuit_failure_threshold,
        circuit_cooldown_seconds=circuit_cooldown_seconds,
        idempotent_tools=idempotent_tools,
    )


def load_config(path: str | Path) -> AppConfig:
    """Load, validate, and return AppConfig from a YAML file.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        ValueError: If YAML is malformed or required fields are missing/invalid.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Malformed YAML in configuration file '{config_path}': {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Configuration file '{config_path}' must contain a top-level mapping/dictionary.")

    # Validate Agent section
    if "agent" not in data or not isinstance(data["agent"], dict):
        raise ValueError(f"Missing or invalid required section 'agent' in configuration file '{config_path}'.")

    agent_data = data["agent"]
    if "max_steps" not in agent_data:
        raise ValueError("Missing required field 'max_steps' in 'agent' configuration section.")

    raw_max_steps = agent_data["max_steps"]
    if isinstance(raw_max_steps, bool) or not isinstance(raw_max_steps, int) or raw_max_steps <= 0:
        raise ValueError("Field 'agent.max_steps' must be a positive integer (> 0).")

    max_steps = raw_max_steps

    raw_max_tool_calls = agent_data.get("max_tool_calls", 25)
    if isinstance(raw_max_tool_calls, bool) or not isinstance(raw_max_tool_calls, int) or raw_max_tool_calls <= 0:
        raise ValueError("Field 'agent.max_tool_calls' must be a positive integer (> 0).")
    max_tool_calls = raw_max_tool_calls

    raw_max_runtime_seconds = agent_data.get("max_runtime_seconds", 60.0)
    if isinstance(raw_max_runtime_seconds, bool) or not isinstance(raw_max_runtime_seconds, (int, float)) or raw_max_runtime_seconds <= 0.0:
        raise ValueError("Field 'agent.max_runtime_seconds' must be a positive number (> 0.0).")
    max_runtime_seconds = float(raw_max_runtime_seconds)

    raw_max_obs = agent_data.get("max_observation_chars", 16_000)
    if raw_max_obs is not None:
        if isinstance(raw_max_obs, bool) or not isinstance(raw_max_obs, int) or raw_max_obs <= 0:
            raise ValueError("Field 'agent.max_observation_chars' must be a positive integer (> 0).")
    max_observation_chars = raw_max_obs

    # Validate LLM section
    if "llm" not in data or not isinstance(data["llm"], dict):
        raise ConfigurationError(f"Missing or invalid required section 'llm' in configuration file '{config_path}'.")

    llm_data = data["llm"]

    # Provider (environment variable > config YAML > default: openai_compatible)
    raw_provider = os.environ.get("LLM_PROVIDER", llm_data.get("provider", "openai_compatible"))
    if not isinstance(raw_provider, str) or not raw_provider.strip():
        raise ConfigurationError("Field 'llm.provider' cannot be empty.")
    llm_provider = raw_provider.strip().lower()

    # Base URL (environment variable > config YAML)
    raw_base_url = os.environ.get("LLM_BASE_URL", llm_data.get("base_url"))
    if raw_base_url is None or not str(raw_base_url).strip():
        raise ConfigurationError("Missing required field 'base_url' in 'llm' configuration section. Set LLM_BASE_URL in .env or config.")
    base_url = str(raw_base_url).strip()
    parsed_base = urllib.parse.urlsplit(base_url)
    if parsed_base.scheme not in ("http", "https"):
        raise ConfigurationError(f"Field 'llm.base_url' must start with 'http://' or 'https://', got '{base_url}'.")

    # Model (environment variable > config YAML)
    raw_model = os.environ.get("LLM_MODEL", llm_data.get("model"))
    if raw_model is None or not str(raw_model).strip():
        raise ConfigurationError("Missing required field 'model' in 'llm' configuration section. Set LLM_MODEL in .env or config.")
    model = str(raw_model).strip()

    # Temperature (environment variable > config YAML)
    raw_temp = os.environ.get("LLM_TEMPERATURE")
    if raw_temp is not None:
        try:
            temperature = float(raw_temp)
        except (ValueError, TypeError) as e:
            raise ConfigurationError(f"Invalid temperature value '{raw_temp}', must be a valid float.") from e
    else:
        if "temperature" not in llm_data:
            raise ConfigurationError("Missing required field 'temperature' in 'llm' configuration section.")
        try:
            temperature = float(llm_data["temperature"])
        except (ValueError, TypeError) as e:
            raise ConfigurationError(f"Invalid temperature value '{llm_data['temperature']}', must be a valid float.") from e

    # API key (optional at type level: environment variable > api_key_env > config YAML)
    api_key_env_var = str(llm_data.get("api_key_env", "LLM_API_KEY")).strip()
    api_key: str | None = (
        os.environ.get("LLM_API_KEY")
        or (os.environ.get(api_key_env_var) if api_key_env_var else None)
        or llm_data.get("api_key")
    )
    if api_key is not None:
        api_key = str(api_key).strip()

    # Timeout (environment variable > config YAML > default 30.0)
    raw_timeout = os.environ.get(
        "LLM_TIMEOUT_SECONDS",
        os.environ.get("LLM_TIMEOUT", llm_data.get("timeout_seconds", llm_data.get("timeout", 30.0))),
    )
    try:
        timeout = float(raw_timeout)
        if timeout <= 0.0:
            raise ConfigurationError("Field 'llm.timeout' must be a positive number (> 0).")
    except (ValueError, TypeError) as e:
        raise ConfigurationError(f"Invalid timeout value '{raw_timeout}': {e}") from e

    # Max retries (environment variable > config YAML > default 2)
    raw_max_retries = os.environ.get("LLM_MAX_RETRIES", llm_data.get("max_retries", 2))
    if isinstance(raw_max_retries, bool):
        raise ConfigurationError("Field 'llm.max_retries' must be a non-negative integer (>= 0).")
    try:
        max_retries = int(raw_max_retries)
        if max_retries < 0:
            raise ConfigurationError("Field 'llm.max_retries' must be a non-negative integer (>= 0).")
    except (ValueError, TypeError) as e:
        raise ConfigurationError(f"Invalid max_retries value '{raw_max_retries}': {e}") from e

    # Retry backoff (environment variable > config YAML > default 0.5)
    raw_retry_backoff = os.environ.get("LLM_RETRY_BACKOFF", llm_data.get("retry_backoff", 0.5))
    try:
        retry_backoff = float(raw_retry_backoff)
        if retry_backoff < 0.0:
            raise ConfigurationError("Field 'llm.retry_backoff' must be a non-negative number (>= 0).")
    except (ValueError, TypeError) as e:
        raise ConfigurationError(f"Invalid retry_backoff value '{raw_retry_backoff}': {e}") from e

    # Extra headers (config YAML merged with LLM_EXTRA_HEADERS JSON env var)
    extra_headers: dict[str, str] = {}
    yaml_headers = llm_data.get("extra_headers", {})
    if isinstance(yaml_headers, dict):
        extra_headers.update({str(k): str(v) for k, v in yaml_headers.items()})
    elif yaml_headers is not None:
        raise ConfigurationError("Field 'llm.extra_headers' must be a dictionary in configuration.")

    env_headers = os.environ.get("LLM_EXTRA_HEADERS")
    if env_headers:
        try:
            parsed_headers = json.loads(env_headers)
            if not isinstance(parsed_headers, dict):
                raise ConfigurationError(
                    f"Field 'LLM_EXTRA_HEADERS' must be a valid JSON dictionary, got {type(parsed_headers).__name__}."
                )
            extra_headers.update({str(k): str(v) for k, v in parsed_headers.items()})
        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Invalid JSON in LLM_EXTRA_HEADERS: {e}") from e

    # Validate Tools section
    if "tools" not in data or not isinstance(data["tools"], dict):
        raise ValueError(f"Missing or invalid required section 'tools' in configuration file '{config_path}'.")

    tools_data = data["tools"]
    if "workspace_root" not in tools_data:
        raise ValueError("Missing required field 'workspace_root' in 'tools' configuration section.")

    raw_workspace_root = tools_data["workspace_root"]
    if not isinstance(raw_workspace_root, str) or not raw_workspace_root.strip():
        raise ValueError("Field 'tools.workspace_root' must be a non-empty string.")

    workspace_root = raw_workspace_root.strip()

    # Validate optional MCP servers section
    mcp_servers: list[MCPServerConfig] = []
    if "mcp_servers" in data:
        raw_mcp_servers = data["mcp_servers"]
        if not isinstance(raw_mcp_servers, list):
            raise ValueError(f"Field 'mcp_servers' must be a list in '{config_path}'.")
        for idx, server_data in enumerate(raw_mcp_servers):
            if not isinstance(server_data, dict):
                raise ValueError(f"MCP server entry at index {idx} must be a dictionary in '{config_path}'.")
            if "name" not in server_data or not isinstance(server_data["name"], str) or not server_data["name"].strip():
                raise ValueError(f"MCP server entry at index {idx} must have a non-empty 'name' string.")

            name = server_data["name"].strip()
            raw_transport = server_data.get("transport", "stdio")
            if not isinstance(raw_transport, str) or raw_transport.strip().lower() not in ("stdio", "streamable_http"):
                raise ValueError(
                    f"MCP server '{name}' transport must be 'stdio' or 'streamable_http', got '{raw_transport}'."
                )
            transport = raw_transport.strip().lower()

            prefix: str | None = None
            if "prefix" in server_data and server_data["prefix"] is not None:
                raw_prefix = server_data["prefix"]
                if not isinstance(raw_prefix, str) or not raw_prefix.strip():
                    raise ValueError(f"Field 'prefix' for MCP server '{name}' must be a non-empty string.")
                prefix = raw_prefix.strip()

            trusted_insecure_hosts: list[str] = []
            if "trusted_insecure_hosts" in server_data and server_data["trusted_insecure_hosts"] is not None:
                raw_hosts = server_data["trusted_insecure_hosts"]
                if not isinstance(raw_hosts, list) or not all(isinstance(h, str) and h.strip() for h in raw_hosts):
                    raise ValueError(
                        f"Field 'trusted_insecure_hosts' for MCP server '{name}' must be a list of non-empty strings."
                    )
                trusted_insecure_hosts = [h.strip() for h in raw_hosts]

            if transport == "stdio":
                if "command" not in server_data or not isinstance(server_data["command"], str) or not server_data["command"].strip():
                    raise ValueError(f"MCP server '{name}' with stdio transport requires a non-empty 'command' string.")
                command = server_data["command"].strip()
                args = server_data.get("args", [])
                if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                    raise ValueError(f"Field 'args' for MCP server '{name}' must be a list of strings.")
                env = server_data.get("env", {})
                if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
                    raise ValueError(f"Field 'env' for MCP server '{name}' must be a dictionary of strings.")
                resilience = _parse_resilience_config(name, server_data.get("resilience"))
                mcp_servers.append(
                    MCPServerConfig(
                        name=name,
                        transport="stdio",
                        command=command,
                        args=list(args),
                        env=dict(env),
                        prefix=prefix,
                        trusted_insecure_hosts=list(trusted_insecure_hosts),
                        resilience=resilience,
                    )
                )
            else:  # streamable_http
                if "url" not in server_data or not isinstance(server_data["url"], str) or not server_data["url"].strip():
                    raise ValueError(f"MCP server '{name}' with streamable_http transport requires a non-empty 'url' string.")
                url = server_data["url"].strip()
                parsed = urllib.parse.urlsplit(url)
                if parsed.scheme not in ("http", "https"):
                    raise ValueError(f"MCP server '{name}' url must start with 'http://' or 'https://', got '{url}'.")

                hostname = (parsed.hostname or "").lower()
                trusted_hosts_lower = {h.lower() for h in trusted_insecure_hosts}
                is_local = (hostname in ("localhost", "127.0.0.1", "::1")) or (hostname in trusted_hosts_lower)
                if parsed.scheme == "http" and not is_local:
                    raise ValueError(
                        f"Remote MCP server '{name}' must use HTTPS ('{url}'). Insecure HTTP is only permitted for localhost or configured trusted_insecure_hosts."
                    )

                auth_token_env: str | None = None
                if "auth_token_env" in server_data and server_data["auth_token_env"] is not None:
                    raw_auth_env = server_data["auth_token_env"]
                    if not isinstance(raw_auth_env, str) or not raw_auth_env.strip():
                        raise ValueError(f"Field 'auth_token_env' for MCP server '{name}' must be a non-empty string.")
                    auth_token_env = raw_auth_env.strip()

                raw_timeout = server_data.get("timeout_seconds", 30.0)
                try:
                    timeout_seconds = float(raw_timeout)
                    if timeout_seconds <= 0.0:
                        raise ValueError()
                except (ValueError, TypeError) as e:
                    raise ValueError(
                        f"Field 'timeout_seconds' for MCP server '{name}' must be a positive number (> 0), got '{raw_timeout}'."
                    ) from e

                resilience = _parse_resilience_config(name, server_data.get("resilience"))
                mcp_servers.append(
                    MCPServerConfig(
                        name=name,
                        transport="streamable_http",
                        url=url,
                        auth_token_env=auth_token_env,
                        timeout_seconds=timeout_seconds,
                        prefix=prefix,
                        trusted_insecure_hosts=list(trusted_insecure_hosts),
                        resilience=resilience,
                    )
                )


    # Validate optional Transport section
    transport_data = data.get("transport", {})
    if not isinstance(transport_data, dict):
        raise ValueError(f"Field 'transport' must be a dictionary in '{config_path}'.")

    raw_provider = os.environ.get(
        "TRANSPORT_PROVIDER",
        transport_data.get("provider", "synthetic"),
    )
    if not isinstance(raw_provider, str) or raw_provider.strip().lower() not in ("synthetic", "live"):
        raise ValueError(f"Transport provider must be 'synthetic' or 'live', got '{raw_provider}'.")
    provider = raw_provider.strip().lower()

    live_data = transport_data.get("live", {})
    if not isinstance(live_data, dict):
        raise ValueError(f"Field 'transport.live' must be a dictionary in '{config_path}'.")

    raw_base_url = os.environ.get(
        "TRANSPORT_API_URL",
        live_data.get("base_url", "https://v6.db.transport.rest"),
    )
    if not isinstance(raw_base_url, str) or not raw_base_url.strip():
        raise ValueError("Field 'transport.live.base_url' must be a non-empty string.")
    base_url_live = raw_base_url.strip()

    raw_api_key_env = os.environ.get(
        "TRANSPORT_API_KEY_ENV",
        live_data.get("api_key_env", None),
    )

    raw_timeout = os.environ.get(
        "TRANSPORT_TIMEOUT_SECONDS",
        live_data.get("timeout_seconds", 8.0),
    )
    try:
        timeout_seconds = float(raw_timeout)
        if timeout_seconds <= 0.0:
            raise ValueError("Timeout must be > 0.0")
    except (ValueError, TypeError) as e:
        raise ValueError(f"Field 'transport.live.timeout_seconds' must be a positive number: {e}") from e

    raw_max_retries = live_data.get("max_retries", 1)
    if isinstance(raw_max_retries, bool) or not isinstance(raw_max_retries, int) or raw_max_retries < 0:
        raise ValueError("Field 'transport.live.max_retries' must be a non-negative integer.")

    raw_fallback = os.environ.get(
        "TRANSPORT_FALLBACK_TO_SYNTHETIC",
        live_data.get("fallback_to_synthetic", False),
    )
    if isinstance(raw_fallback, str):
        fallback_to_synthetic = raw_fallback.strip().lower() in ("1", "true", "yes")
    elif isinstance(raw_fallback, bool):
        fallback_to_synthetic = raw_fallback
    else:
        raise ValueError("Field 'transport.live.fallback_to_synthetic' must be a boolean.")

    transport_config = TransportConfig(
        provider=provider,
        live=TransportLiveConfig(
            base_url=base_url_live,
            api_key_env=raw_api_key_env,
            timeout_seconds=timeout_seconds,
            max_retries=raw_max_retries,
            fallback_to_synthetic=fallback_to_synthetic,
        ),
    )

    # Validate optional memory section
    memory_data = data.get("memory", {})
    if not isinstance(memory_data, dict):
        raise ValueError("Field 'memory' must be a dictionary.")

    mem_enabled = bool(memory_data.get("enabled", False))
    mem_storage_path = str(memory_data.get("storage_path", ".agent_memory/memory.db")).strip()
    if not mem_storage_path:
        raise ValueError("Field 'memory.storage_path' must be a non-empty string.")

    try:
        mem_max_retrieved = int(memory_data.get("max_retrieved", 3))
        if mem_max_retrieved <= 0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError("Field 'memory.max_retrieved' must be a positive integer (> 0).") from e

    try:
        mem_max_context_chars = int(memory_data.get("max_context_chars", 2000))
        if mem_max_context_chars <= 0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError("Field 'memory.max_context_chars' must be a positive integer (> 0).") from e

    try:
        mem_max_entry_chars = int(memory_data.get("max_entry_chars", 4000))
        if mem_max_entry_chars <= 0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError("Field 'memory.max_entry_chars' must be a positive integer (> 0).") from e

    # Retrieval strategy validation
    raw_strategy = os.environ.get(
        "MEMORY_RETRIEVAL_STRATEGY",
        memory_data.get("retrieval_strategy", "hybrid_rrf"),
    )
    if not isinstance(raw_strategy, str) or not raw_strategy.strip():
        raise ValueError("Field 'memory.retrieval_strategy' must be a non-empty string.")
    norm_strategy = raw_strategy.strip().lower()
    if norm_strategy == "hybrid":
        norm_strategy = "hybrid_rrf"
    valid_strategies = {"hybrid_rrf", "lexical", "bm25", "dense"}
    if norm_strategy not in valid_strategies:
        raise ValueError(
            f"Invalid retrieval_strategy '{raw_strategy}'. Expected one of: {sorted(valid_strategies)} (or 'hybrid')."
        )

    # Candidate limit (None = full corpus)
    raw_cand_limit = memory_data.get("candidate_limit", None)
    cand_limit: int | None = None
    if raw_cand_limit is not None:
        try:
            cand_limit = int(raw_cand_limit)
            if cand_limit <= 0:
                raise ValueError()
        except (ValueError, TypeError) as e:
            raise ValueError("Field 'memory.candidate_limit' must be a positive integer (> 0) or null.") from e

    # Embedding subsection (with flat fallback support)
    emb_data = memory_data.get("embedding", {})
    if not isinstance(emb_data, dict):
        raise ValueError("Field 'memory.embedding' must be a dictionary.")

    emb_model = os.environ.get(
        "MEMORY_DENSE_MODEL",
        emb_data.get("model", memory_data.get("dense_model", "Snowflake/snowflake-arctic-embed-s")),
    )
    if not isinstance(emb_model, str) or not emb_model.strip():
        raise ValueError("Field 'memory.embedding.model' must be a non-empty string.")

    emb_revision = str(emb_data.get("revision", memory_data.get("dense_revision", "e596f507467533e48a2e17c007f0e1dacc837b33"))).strip()
    if not emb_revision:
        raise ValueError("Field 'memory.embedding.revision' must be a non-empty string.")

    try:
        emb_dimension = int(emb_data.get("dimension", memory_data.get("dimension", 384)))
        if emb_dimension <= 0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError("Field 'memory.embedding.dimension' must be a positive integer (> 0).") from e

    emb_qpname = emb_data.get("query_prompt_name", memory_data.get("query_prompt_name", "query"))
    if emb_qpname is not None and (not isinstance(emb_qpname, str) or not emb_qpname.strip()):
        raise ValueError("Field 'memory.embedding.query_prompt_name' must be a non-empty string or null.")

    emb_qpfx = emb_data.get("query_prefix", memory_data.get("query_prefix", None))
    if emb_qpfx is not None and not isinstance(emb_qpfx, str):
        raise ValueError("Field 'memory.embedding.query_prefix' must be a string or null.")

    try:
        emb_threshold = float(emb_data.get("threshold", memory_data.get("dense_threshold", 0.55)))
        if not (0.0 <= emb_threshold <= 1.0):
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError("Field 'memory.embedding.threshold' must be a float between 0.0 and 1.0.") from e

    raw_missing = str(emb_data.get("missing_embedding", memory_data.get("missing_embedding", "skip"))).strip().lower()
    if raw_missing not in ("skip", "error"):
        raise ValueError(f"Field 'memory.embedding.missing_embedding' must be 'skip' or 'error', got '{raw_missing}'.")

    raw_readiness = os.environ.get(
        "MEMORY_INDEX_READINESS",
        str(emb_data.get("index_readiness", memory_data.get("index_readiness", "incremental"))),
    ).strip().lower()
    if raw_readiness == "skip":
        raw_readiness = "sparse_only"
    if raw_readiness not in ("eager", "incremental", "sparse_only"):
        raise ValueError(
            f"Field 'memory.embedding.index_readiness' must be 'eager', 'incremental', or 'sparse_only', got '{raw_readiness}'."
        )

    embedding_config = MemoryEmbeddingConfig(
        model=emb_model.strip(),
        revision=emb_revision,
        dimension=emb_dimension,
        query_prompt_name=emb_qpname.strip() if emb_qpname else None,
        query_prefix=emb_qpfx,
        threshold=emb_threshold,
        missing_embedding=raw_missing,
        index_readiness=raw_readiness,
    )

    # Sparse subsection (with flat fallback support)
    sparse_data = memory_data.get("sparse", {})
    if not isinstance(sparse_data, dict):
        raise ValueError("Field 'memory.sparse' must be a dictionary.")

    try:
        sparse_cov = float(sparse_data.get("min_coverage", memory_data.get("sparse_min_coverage", 0.35)))
        if not (0.0 <= sparse_cov <= 1.0):
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError("Field 'memory.sparse.min_coverage' must be a float between 0.0 and 1.0.") from e

    try:
        sparse_cutoff = float(sparse_data.get("bm25_cutoff", memory_data.get("sparse_bm25_cutoff", -2.5)))
    except (ValueError, TypeError) as e:
        raise ValueError(f"Field 'memory.sparse.bm25_cutoff' must be a float: {e}") from e

    sparse_stopword = str(
        sparse_data.get(
            "stopword_policy",
            memory_data.get("sparse_stopword_policy_version", "v1-standard-english-34"),
        )
    ).strip()
    if not sparse_stopword:
        raise ValueError("Field 'memory.sparse.stopword_policy' must be a non-empty string.")

    sparse_config = MemorySparseConfig(
        min_coverage=sparse_cov,
        bm25_cutoff=sparse_cutoff,
        stopword_policy=sparse_stopword,
    )

    # Hybrid subsection (with flat fallback support)
    hybrid_data = memory_data.get("hybrid", {})
    if not isinstance(hybrid_data, dict):
        raise ValueError("Field 'memory.hybrid' must be a dictionary.")

    try:
        h_pool = int(hybrid_data.get("candidate_pool", memory_data.get("candidate_pool", 10)))
        if h_pool <= 0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError("Field 'memory.hybrid.candidate_pool' must be a positive integer (> 0).") from e

    try:
        h_k = int(hybrid_data.get("rrf_k", memory_data.get("rrf_k", 10)))
        if h_k <= 0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError("Field 'memory.hybrid.rrf_k' must be a positive integer (> 0).") from e

    try:
        h_sw = float(hybrid_data.get("sparse_weight", memory_data.get("sparse_weight", 1.0)))
        if h_sw <= 0.0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError("Field 'memory.hybrid.sparse_weight' must be a positive float (> 0.0).") from e

    try:
        h_dw = float(hybrid_data.get("dense_weight", memory_data.get("dense_weight", 1.0)))
        if h_dw <= 0.0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError("Field 'memory.hybrid.dense_weight' must be a positive float (> 0.0).") from e

    h_gate = bool(hybrid_data.get("relevance_gate", memory_data.get("enable_relevance_gate", True)))

    hybrid_config = MemoryHybridConfig(
        candidate_pool=h_pool,
        rrf_k=h_k,
        sparse_weight=h_sw,
        dense_weight=h_dw,
        relevance_gate=h_gate,
    )

    memory_config = MemoryConfig(
        enabled=mem_enabled,
        storage_path=mem_storage_path,
        max_retrieved=mem_max_retrieved,
        max_context_chars=mem_max_context_chars,
        max_entry_chars=mem_max_entry_chars,
        retrieval_strategy=norm_strategy,
        candidate_limit=cand_limit,
        embedding=embedding_config,
        sparse=sparse_config,
        hybrid=hybrid_config,
    )

    # Observability section (optional, defaults to enabled on port 9101)
    obs_data = data.get("observability", {})
    if not isinstance(obs_data, dict):
        raise ValueError("Field 'observability' must be a dictionary.")

    metrics_data = obs_data.get("metrics", {})
    if not isinstance(metrics_data, dict):
        raise ValueError("Field 'observability.metrics' must be a dictionary.")

    metrics_enabled = bool(metrics_data.get("enabled", True))
    metrics_host = str(metrics_data.get("host", "0.0.0.0")).strip()
    raw_port = metrics_data.get("port", 9101)
    if isinstance(raw_port, bool) or not isinstance(raw_port, int) or not (1 <= raw_port <= 65535):
        raise ValueError("Field 'observability.metrics.port' must be an integer between 1 and 65535.")
    metrics_port = raw_port
    metrics_required = bool(metrics_data.get("required", True))

    # Tracing section
    tracing_data = obs_data.get("tracing", {})
    if not isinstance(tracing_data, dict):
        raise ValueError("Field 'observability.tracing' must be a dictionary.")

    raw_tracing_enabled = os.environ.get(
        "TRACING_ENABLED",
        tracing_data.get("enabled", False),
    )
    if isinstance(raw_tracing_enabled, str):
        tracing_enabled = raw_tracing_enabled.strip().lower() in ("1", "true", "yes")
    elif isinstance(raw_tracing_enabled, bool):
        tracing_enabled = raw_tracing_enabled
    else:
        raise ValueError("Field 'observability.tracing.enabled' must be a boolean.")

    raw_tracing_endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            tracing_data.get("endpoint", "http://127.0.0.1:4318/v1/traces"),
        ),
    )
    if not isinstance(raw_tracing_endpoint, str) or not raw_tracing_endpoint.strip():
        raise ValueError("Field 'observability.tracing.endpoint' must be a non-empty string.")
    tracing_endpoint = raw_tracing_endpoint.strip()

    service_name = str(
        os.environ.get("OTEL_SERVICE_NAME", tracing_data.get("service_name", "agent-harness"))
    ).strip()
    if not service_name:
        raise ValueError("Field 'observability.tracing.service_name' cannot be empty.")

    environment = str(
        os.environ.get("ENVIRONMENT", tracing_data.get("environment", "development"))
    ).strip()
    if not environment:
        raise ValueError("Field 'observability.tracing.environment' cannot be empty.")

    try:
        export_timeout = float(tracing_data.get("export_timeout_seconds", 5.0))
        if export_timeout <= 0.0:
            raise ValueError()
    except (ValueError, TypeError) as e:
        raise ValueError("Field 'observability.tracing.export_timeout_seconds' must be > 0.0.") from e

    # Structured logging section
    logging_data = obs_data.get("logging", {})
    if not isinstance(logging_data, dict):
        raise ValueError("Field 'observability.logging' must be a dictionary.")

    raw_structured = os.environ.get(
        "STRUCTURED_LOGGING_ENABLED",
        logging_data.get("structured_enabled", False),
    )
    if isinstance(raw_structured, str):
        structured_enabled = raw_structured.strip().lower() in ("1", "true", "yes")
    elif isinstance(raw_structured, bool):
        structured_enabled = raw_structured
    else:
        raise ValueError("Field 'observability.logging.structured_enabled' must be a boolean.")

    log_file_path = logging_data.get("file_path", None)
    if log_file_path is not None and not isinstance(log_file_path, str):
        raise ValueError("Field 'observability.logging.file_path' must be a string or null.")

    observability_config = ObservabilityConfig(
        metrics=MetricsConfig(
            enabled=metrics_enabled,
            host=metrics_host,
            port=metrics_port,
            required=metrics_required,
        ),
        tracing=TracingConfig(
            enabled=tracing_enabled,
            endpoint=tracing_endpoint,
            service_name=service_name,
            environment=environment,
            export_timeout_seconds=export_timeout,
        ),
        logging=LoggingConfig(
            structured_enabled=structured_enabled,
            file_path=log_file_path.strip() if log_file_path else None,
        ),
    )

    # Validate Permissions section
    permissions_data = data.get("permissions")
    if permissions_data is not None:
        if not isinstance(permissions_data, dict):
            raise ValueError(f"Section 'permissions' in '{config_path}' must be a dictionary.")

        raw_default_stance = permissions_data.get("default_stance", "deny")
        if str(raw_default_stance).lower() not in ("allow", "deny", "require_confirmation"):
            raise ValueError(f"Invalid default_stance '{raw_default_stance}', must be 'allow', 'deny', or 'require_confirmation'.")
        default_stance = str(raw_default_stance).lower()

        raw_handler = permissions_data.get("confirmation_handler", "console")
        if str(raw_handler).lower() not in ("console", "deterministic"):
            raise ValueError(f"Invalid confirmation_handler '{raw_handler}', must be 'console' or 'deterministic'.")
        confirmation_handler = str(raw_handler).lower()

        raw_rules = permissions_data.get("rules", [])
        if not isinstance(raw_rules, list):
            raise ValueError("Field 'permissions.rules' must be a list.")

        parsed_rules: list[PolicyRuleConfig] = []
        for idx, r in enumerate(raw_rules):
            if not isinstance(r, dict):
                raise ValueError(f"Rule at index {idx} in 'permissions.rules' must be a mapping/dict.")
            r_name = str(r.get("name", f"rule_{idx}")).strip()
            r_decision = str(r.get("decision", "require_confirmation")).lower()
            if r_decision not in ("allow", "deny", "require_confirmation"):
                raise ValueError(f"Invalid decision '{r_decision}' in rule '{r_name}'.")

            raw_priority = r.get("priority", 100)
            if not isinstance(raw_priority, int):
                raise ValueError(f"Field 'priority' in rule '{r_name}' must be an integer.")

            r_tool_pattern = str(r.get("tool_pattern", "*")).strip()
            r_source = str(r["source"]).strip().lower() if "source" in r and r["source"] else None
            r_server = str(r["server_name"]).strip() if "server_name" in r and r["server_name"] else None
            r_role = str(r["role"]).strip() if "role" in r and r["role"] else None
            r_risk = str(r.get("risk_level", "mutating")).strip().lower()
            r_resource = str(r["resource_pattern"]).strip() if "resource_pattern" in r and r["resource_pattern"] else None
            r_arg_matches = (
                {str(k): str(v) for k, v in r["argument_matches"].items()}
                if "argument_matches" in r and isinstance(r["argument_matches"], dict)
                else None
            )
            r_match_risk = str(r["match_risk_level"]).strip().lower() if "match_risk_level" in r and r["match_risk_level"] else None

            parsed_rules.append(
                PolicyRuleConfig(
                    name=r_name,
                    decision=r_decision,
                    priority=raw_priority,
                    tool_pattern=r_tool_pattern,
                    source=r_source,
                    server_name=r_server,
                    role=r_role,
                    risk_level=r_risk,
                    resource_pattern=r_resource,
                    argument_matches=r_arg_matches,
                    match_risk_level=r_match_risk,
                )
            )

        permissions_config = PermissionsConfig(
            default_stance=default_stance,
            confirmation_handler=confirmation_handler,
            rules=parsed_rules,
        )
    else:
        # Default closed fallback permissions configuration
        permissions_config = PermissionsConfig(
            default_stance="deny",
            confirmation_handler="console",
            rules=[
                PolicyRuleConfig(name="allow_filesystem_reads", decision="allow", priority=100, tool_pattern="read_file", source="builtin", risk_level="read_only"),
                PolicyRuleConfig(name="allow_filesystem_list", decision="allow", priority=100, tool_pattern="list_directory", source="builtin", risk_level="read_only"),
                PolicyRuleConfig(name="allow_filesystem_search", decision="allow", priority=100, tool_pattern="search_files", source="builtin", risk_level="read_only"),
                PolicyRuleConfig(name="confirm_filesystem_modify", decision="require_confirmation", priority=100, tool_pattern="modify_file", source="builtin", risk_level="mutating"),
                PolicyRuleConfig(name="confirm_filesystem_create_file", decision="require_confirmation", priority=100, tool_pattern="create_file", source="builtin", risk_level="mutating"),
                PolicyRuleConfig(name="confirm_filesystem_create_dir", decision="require_confirmation", priority=100, tool_pattern="create_directory", source="builtin", risk_level="mutating"),
                PolicyRuleConfig(name="allow_transport_mcp", decision="allow", priority=100, tool_pattern="*", source="mcp", server_name="transport_service", risk_level="read_only"),
            ],
        )

    return AppConfig(
        agent=AgentConfig(
            max_steps=max_steps,
            max_tool_calls=max_tool_calls,
            max_runtime_seconds=max_runtime_seconds,
            max_observation_chars=max_observation_chars,
        ),
        llm=LLMConfig(
            provider=llm_provider,
            base_url=base_url,
            model=model,
            temperature=temperature,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            extra_headers=extra_headers,
        ),
        tools=ToolsConfig(
            workspace_root=workspace_root,
        ),
        mcp_servers=mcp_servers,
        transport=transport_config,
        memory=memory_config,
        observability=observability_config,
        permissions=permissions_config,
    )
