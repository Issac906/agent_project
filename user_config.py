"""User-editable runtime configuration stored outside .env."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from runtime_paths import data_path


SECRET_KEYS = {
    "LIGHTRAG_API_KEY",
    "SEARCH_API_KEY",
    "LLM_API_KEY",
    "PI_AGENT_API_KEY",
    "DEEPSEEK_API_KEY",
    "FEISHU_APP_SECRET",
    "KB_MANAGER_API_KEY",
}

ALLOWED_KEYS = {
    "LIGHTRAG_BASE_URL",
    "LIGHTRAG_API_KEY",
    "LIGHTRAG_QUERY_MODE",
    "LIGHTRAG_TOP_K",
    "LIGHTRAG_INCLUDE_CHUNK_CONTENT",
    "KB_MANAGER_URL",
    "KB_MANAGER_API_KEY",
    "KB_MANAGER_TIMEOUT",
    "SEARCH_PROVIDER",
    "SEARCH_API_KEY",
    "SEARCH_BASE_URL",
    "SEARCH_ENDPOINT",
    "SEARCH_METHOD",
    "ANYSEARCH_BASE_URL",
    "ANYSEARCH_ENDPOINT",
    "ANYSEARCH_METHOD",
    "AGENT_CORE",
    "PI_CODING_COMMAND",
    "PI_CODING_PROVIDER",
    "PI_CODING_MODEL",
    "PI_CODING_TIMEOUT",
    "PI_AGENT_API_KEY",
    "DEEPSEEK_API_KEY",
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "FEISHU_ENABLED",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_API_BASE_URL",
    "FEISHU_PUBLIC_BASE_URL",
}


@dataclass(frozen=True)
class UserConfigView:
    path: str
    values: dict[str, str]
    secrets: dict[str, dict[str, Any]]


def user_config_path() -> Path:
    override = os.getenv("PATENT_AGENT_USER_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".patent_agent" / "user_config.json"


def load_user_config() -> dict[str, str]:
    path = user_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in data.items():
        if key in ALLOWED_KEYS and value is not None:
            text = str(value).strip()
            if text:
                cleaned[key] = text
    return cleaned


def save_user_config(payload: dict[str, Any]) -> UserConfigView:
    current = load_user_config()
    values = dict(current)
    for key, value in payload.items():
        if key not in ALLOWED_KEYS:
            continue
        if value is None:
            continue
        text = str(value).strip()
        if text:
            values[key] = text
        else:
            values.pop(key, None)
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    return user_config_view(values)


def user_config_view(values: dict[str, str] | None = None) -> UserConfigView:
    values = values if values is not None else load_user_config()
    visible = {key: value for key, value in values.items() if key not in SECRET_KEYS}
    secrets = {
        key: {
            "configured": key in values,
            "masked": _mask_secret(values.get(key, "")),
        }
        for key in SECRET_KEYS
    }
    return UserConfigView(path=str(user_config_path()), values=visible, secrets=secrets)


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
