"""Environment configuration for the patent workflow."""

from __future__ import annotations

from dataclasses import dataclass
import os


DEFAULT_LIGHTRAG_BASE_URL = "http://192.168.130.130:9621/webui/#/"


@dataclass(frozen=True)
class AppConfig:
    """Runtime config loaded from .env."""

    lightrag_base_url: str
    lightrag_api_key: str | None
    lightrag_query_mode: str
    lightrag_top_k: str | None
    lightrag_include_chunk_content: bool

    llm_provider: str
    llm_api_key: str | None
    llm_base_url: str | None
    llm_model: str | None

    search_provider: str
    search_api_key: str | None

    agent_core: str
    codex_command: str
    codex_model: str | None
    codex_enable_search: bool
    codex_sandbox: str
    codex_timeout: int

    pi_command: str
    pi_provider: str
    pi_model: str | None
    pi_timeout: int


def load_config() -> AppConfig:
    return AppConfig(
        lightrag_base_url=os.getenv("LIGHTRAG_BASE_URL", DEFAULT_LIGHTRAG_BASE_URL),
        lightrag_api_key=_optional("LIGHTRAG_API_KEY"),
        lightrag_query_mode=os.getenv("LIGHTRAG_QUERY_MODE", "mix"),
        lightrag_top_k=_optional("LIGHTRAG_TOP_K"),
        lightrag_include_chunk_content=_bool("LIGHTRAG_INCLUDE_CHUNK_CONTENT", True),
        llm_provider=os.getenv("LLM_PROVIDER", "none"),
        llm_api_key=_optional("LLM_API_KEY"),
        llm_base_url=_optional("LLM_BASE_URL"),
        llm_model=_optional("LLM_MODEL"),
        search_provider=os.getenv("SEARCH_PROVIDER", "duckduckgo"),
        search_api_key=_optional("SEARCH_API_KEY"),
        agent_core=os.getenv("AGENT_CORE", "pi_coding_agent").strip().lower(),
        codex_command=os.getenv("CODEX_CLI_COMMAND", "codex"),
        codex_model=_optional("CODEX_CLI_MODEL"),
        codex_enable_search=_bool("CODEX_CLI_ENABLE_SEARCH", True),
        codex_sandbox=os.getenv("CODEX_CLI_SANDBOX", "read-only"),
        codex_timeout=_int("CODEX_CLI_TIMEOUT", 600),
        pi_command=os.getenv("PI_CODING_COMMAND", "pi"),
        pi_provider=os.getenv("PI_CODING_PROVIDER", "deepseek"),
        pi_model=_optional("PI_CODING_MODEL") or "deepseek-chat",
        pi_timeout=_int("PI_CODING_TIMEOUT", 600),
    )


def _optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default
