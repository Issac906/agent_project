"""LLM-backed final writer.

Currently supports Ollama's native `/api/generate` endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from config import AppConfig
from external_search import ExternalSearchResult
from models import KnowledgeBundle, TaskPlan


class LLMWriterError(RuntimeError):
    """Raised when the configured LLM cannot generate a final document."""


@dataclass
class LLMGenerationResult:
    markdown: str
    used_llm: bool
    message: str


def generate_with_llm(
    config: AppConfig,
    task: str,
    plan: TaskPlan,
    knowledge: KnowledgeBundle,
    external_search: ExternalSearchResult,
    draft_markdown: str,
) -> LLMGenerationResult:
    provider = config.llm_provider.strip().lower()
    if provider in {"", "none"}:
        return LLMGenerationResult(
            markdown=draft_markdown,
            used_llm=False,
            message="LLM 未启用，使用模板生成结果。",
        )

    if provider != "ollama":
        return LLMGenerationResult(
            markdown=draft_markdown,
            used_llm=False,
            message=f"暂不支持 LLM_PROVIDER={config.llm_provider}，使用模板生成结果。",
        )

    try:
        markdown = _generate_with_ollama(
            config=config,
            task=task,
            plan=plan,
            knowledge=knowledge,
            external_search=external_search,
            draft_markdown=draft_markdown,
        )
    except LLMWriterError as exc:
        return LLMGenerationResult(
            markdown=draft_markdown,
            used_llm=False,
            message=f"Ollama 生成失败，已回退到模板结果：{exc}",
        )

    return LLMGenerationResult(
        markdown=markdown,
        used_llm=True,
        message=f"已使用 Ollama 模型 {config.llm_model} 生成最终稿。",
    )


def _generate_with_ollama(
    config: AppConfig,
    task: str,
    plan: TaskPlan,
    knowledge: KnowledgeBundle,
    external_search: ExternalSearchResult,
    draft_markdown: str,
) -> str:
    if not config.llm_base_url:
        raise LLMWriterError("LLM_BASE_URL 不能为空。")
    if not config.llm_model:
        raise LLMWriterError("LLM_MODEL 不能为空。")

    url = f"{config.llm_base_url.rstrip('/')}/api/generate"
    prompt = _build_prompt(
        task=task,
        plan=plan,
        knowledge=knowledge,
        external_search=external_search,
        draft_markdown=draft_markdown,
    )

    try:
        response = requests.post(
            url,
            json={
                "model": config.llm_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "num_ctx": 8192,
                },
            },
            timeout=180,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LLMWriterError(str(exc)) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise LLMWriterError(f"Ollama 返回非 JSON 内容：{response.text[:300]}") from exc

    content = data.get("response")
    if not isinstance(content, str) or not content.strip():
        raise LLMWriterError(f"Ollama 返回内容为空：{data}")

    return _strip_markdown_fence(content.strip())


def generate_text_with_ollama(
    config: AppConfig,
    prompt: str,
    timeout: int = 180,
) -> str:
    """Generate plain text through Ollama's native API."""
    if config.llm_provider.strip().lower() != "ollama":
        raise LLMWriterError("当前仅支持 LLM_PROVIDER=ollama。")
    if not config.llm_base_url:
        raise LLMWriterError("LLM_BASE_URL 不能为空。")
    if not config.llm_model:
        raise LLMWriterError("LLM_MODEL 不能为空。")

    url = f"{config.llm_base_url.rstrip('/')}/api/generate"
    try:
        response = requests.post(
            url,
            json={
                "model": config.llm_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.25,
                    "num_ctx": 8192,
                },
            },
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LLMWriterError(str(exc)) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise LLMWriterError(f"Ollama 返回非 JSON 内容：{response.text[:300]}") from exc

    content = data.get("response")
    if not isinstance(content, str) or not content.strip():
        raise LLMWriterError(f"Ollama 返回内容为空：{data}")

    return _strip_markdown_fence(content.strip())


def _build_prompt(
    task: str,
    plan: TaskPlan,
    knowledge: KnowledgeBundle,
    external_search: ExternalSearchResult,
    draft_markdown: str,
) -> str:
    source_summary = _source_summary(knowledge, external_search)
    required_sections = "\n".join(f"- {section}" for section in plan.required_sections)

    return f"""你是严谨的中文发明专利撰写助手。请基于给定任务、素材和草稿，输出一份可读的 Markdown 最终稿。

硬性要求：
1. 只输出 Markdown 正文，不要解释你的写作过程，不要包裹 ```markdown 代码块。
2. 必须保留并完善以下章节结构：
{required_sections}
3. 不要把 API 错误、no-context、网络错误写进正文。
4. 如果素材不足，请用“待补充”标注事实缺口，但仍要给出专业、连贯、可继续修改的初稿。
5. 外部搜索结果只能作为快速摸底，不能写成已经正式检索确认的事实。
6. 权利要求要围绕技术方案写，避免写成项目管理或软件工作流。

用户任务：
{task}

任务类型：
{plan.intent}

素材摘要：
{source_summary}

待改写草稿：
{draft_markdown}
"""


def _source_summary(
    knowledge: KnowledgeBundle,
    external_search: ExternalSearchResult,
) -> str:
    parts: list[str] = []
    for item in knowledge.query_results:
        data = item.get("data")
        if isinstance(data, dict):
            response = str(data.get("response", ""))
            references = data.get("references") or []
            if response and "[no-context]" not in response:
                parts.append(f"知识库回答：{response[:1200]}")
            if references:
                parts.append(f"知识库 references：{references}")

    if external_search.results:
        parts.append("外部搜索结果：")
        for index, result in enumerate(external_search.results, start=1):
            parts.append(
                f"{index}. {result.get('title', '')}\n"
                f"摘要：{result.get('snippet', '')}\n"
                f"链接：{result.get('url', '')}"
            )

    if knowledge.errors:
        parts.append("知识库访问存在错误，错误详情仅用于诊断，不得写入正文。")

    return "\n\n".join(parts) if parts else "暂无可靠素材。"


def _strip_markdown_fence(content: str) -> str:
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return content
