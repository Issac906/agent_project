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
