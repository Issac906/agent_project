"""Environment configuration for the patent workflow."""

from __future__ import annotations

from dataclasses import dataclass
import os

from user_config import load_user_config


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
    user_values = load_user_config()
    _apply_user_values_to_environment(user_values)
    config = AppConfig(
        lightrag_base_url=_value("LIGHTRAG_BASE_URL", DEFAULT_LIGHTRAG_BASE_URL, user_values),
        lightrag_api_key=_optional("LIGHTRAG_API_KEY", user_values),
        lightrag_query_mode=_value("LIGHTRAG_QUERY_MODE", "mix", user_values),
        lightrag_top_k=_optional("LIGHTRAG_TOP_K", user_values),
        lightrag_include_chunk_content=_bool("LIGHTRAG_INCLUDE_CHUNK_CONTENT", True, user_values),
        llm_provider=_value("LLM_PROVIDER", "none", user_values),
        llm_api_key=_optional("LLM_API_KEY", user_values),
        llm_base_url=_optional("LLM_BASE_URL", user_values),
        llm_model=_optional("LLM_MODEL", user_values),
        search_provider=_value("SEARCH_PROVIDER", "duckduckgo", user_values),
        search_api_key=_optional("SEARCH_API_KEY", user_values),
        agent_core=_value("AGENT_CORE", "pi_coding_agent", user_values).strip().lower(),
        codex_command=_value("CODEX_CLI_COMMAND", "codex", user_values),
        codex_model=_optional("CODEX_CLI_MODEL", user_values),
        codex_enable_search=_bool("CODEX_CLI_ENABLE_SEARCH", True, user_values),
        codex_sandbox=_value("CODEX_CLI_SANDBOX", "read-only", user_values),
        codex_timeout=_int("CODEX_CLI_TIMEOUT", 600, user_values),
        pi_command=_value("PI_CODING_COMMAND", "pi", user_values),
        pi_provider=_value("PI_CODING_PROVIDER", "", user_values),
        pi_model=_optional("PI_CODING_MODEL", user_values),
        pi_timeout=_int("PI_CODING_TIMEOUT", 600, user_values),
    )
    _apply_config_to_environment(config)
    return config


def _value(name: str, default: str, user_values: dict[str, str] | None = None) -> str:
    user_values = user_values or {}
    value = user_values.get(name)
    if value is None or not value.strip():
        value = os.getenv(name, default)
    return value.strip() if value and value.strip() else default


def _optional(name: str, user_values: dict[str, str] | None = None) -> str | None:
    user_values = user_values or {}
    value = user_values.get(name)
    if value is None:
        value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _bool(name: str, default: bool, user_values: dict[str, str] | None = None) -> bool:
    user_values = user_values or {}
    value = user_values.get(name)
    if value is None:
        value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int, user_values: dict[str, str] | None = None) -> int:
    user_values = user_values or {}
    value = user_values.get(name)
    if value is None:
        value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _apply_user_values_to_environment(user_values: dict[str, str]) -> None:
    for key, value in user_values.items():
        if value:
            os.environ[key] = value
    _apply_generic_search_aliases(user_values)
    _apply_pi_provider_key_aliases(user_values)


def _apply_generic_search_aliases(user_values: dict[str, str]) -> None:
    base_url = user_values.get("SEARCH_BASE_URL") or user_values.get("SEARCH_ENDPOINT")
    if base_url:
        os.environ["SEARCH_BASE_URL"] = base_url
        os.environ.setdefault("ANYSEARCH_BASE_URL", base_url)
    method = user_values.get("SEARCH_METHOD")
    if method:
        os.environ["SEARCH_METHOD"] = method
        os.environ.setdefault("ANYSEARCH_METHOD", method)


def _apply_pi_provider_key_aliases(user_values: dict[str, str] | None = None) -> None:
    user_values = user_values or {}
    key = user_values.get("PI_AGENT_API_KEY") or os.getenv("PI_AGENT_API_KEY")
    if not key:
        return
    provider = (
        user_values.get("PI_CODING_PROVIDER")
        or os.getenv("PI_CODING_PROVIDER")
        or ""
    ).strip().lower()
    aliases = {
        "deepseek": ["DEEPSEEK_API_KEY"],
        "openai": ["OPENAI_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY"],
        "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    }
    os.environ["PI_AGENT_API_KEY"] = key
    for env_name in aliases.get(provider, []):
        os.environ[env_name] = key


def _apply_config_to_environment(config: AppConfig) -> None:
    values = {
        "LIGHTRAG_BASE_URL": config.lightrag_base_url,
        "LIGHTRAG_API_KEY": config.lightrag_api_key,
        "LIGHTRAG_QUERY_MODE": config.lightrag_query_mode,
        "LIGHTRAG_TOP_K": config.lightrag_top_k,
        "LIGHTRAG_INCLUDE_CHUNK_CONTENT": "true" if config.lightrag_include_chunk_content else "false",
        "SEARCH_PROVIDER": config.search_provider,
        "SEARCH_API_KEY": config.search_api_key,
        "AGENT_CORE": config.agent_core,
        "PI_CODING_COMMAND": config.pi_command,
        "PI_CODING_PROVIDER": config.pi_provider,
        "PI_CODING_MODEL": config.pi_model,
        "PI_CODING_TIMEOUT": str(config.pi_timeout),
        "SEARCH_BASE_URL": os.getenv("SEARCH_BASE_URL"),
        "SEARCH_ENDPOINT": os.getenv("SEARCH_ENDPOINT"),
        "SEARCH_METHOD": os.getenv("SEARCH_METHOD"),
        "PI_AGENT_API_KEY": os.getenv("PI_AGENT_API_KEY"),
        "LLM_PROVIDER": config.llm_provider,
        "LLM_API_KEY": config.llm_api_key,
        "LLM_BASE_URL": config.llm_base_url,
        "LLM_MODEL": config.llm_model,
    }
    for key, value in values.items():
        if value is not None:
            os.environ[key] = value
    _apply_generic_search_aliases({key: value for key, value in values.items() if value})
    _apply_pi_provider_key_aliases()
