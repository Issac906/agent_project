"""End-to-end patent writing workflow."""

from __future__ import annotations

from pathlib import Path

from config import AppConfig
from evaluator import build_evaluation_markdown, evaluate_markdown
from external_search import search_external_materials
from lightrag_client import LightRAGClient, LightRAGClientError
from llm_writer import generate_with_llm
from models import KnowledgeBundle
from skill_router import route_task
from writer import build_patent_markdown


def run_workflow(
    task: str,
    client: LightRAGClient,
    output_dir: Path,
    config: AppConfig,
    include_query_data: bool = False,
    enable_external_search: bool = False,
) -> Path:
    plan = route_task(task)
    knowledge = collect_knowledge(client, plan.suggested_queries, include_query_data)
    should_search = enable_external_search or not knowledge.has_context() or bool(knowledge.errors)
    external = search_external_materials(task, enabled=should_search)

    draft_markdown = build_patent_markdown(
        task=task,
        plan=plan,
        knowledge=knowledge,
        external_search=external,
        include_raw=include_query_data,
    )
    llm_result = generate_with_llm(
        config=config,
        task=task,
        plan=plan,
        knowledge=knowledge,
        external_search=external,
        draft_markdown=draft_markdown,
    )
    markdown = llm_result.markdown
    evaluation = evaluate_markdown(markdown, plan)
    markdown = (
        f"{markdown}\n\n---\n\n"
        f"## 生成说明\n\n"
        f"- LLM 状态：{llm_result.message}\n"
        f"- 知识库 API：查询 {len(knowledge.query_results)} 次，错误 {len(knowledge.errors)} 个\n"
        f"- 外部资料：{'已启用' if external.enabled else '未启用'}\n\n"
        f"{build_evaluation_markdown(evaluation)}"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / plan.output_filename
    final_path.write_text(markdown, encoding="utf-8")

    legacy_path = output_dir / "result.md"
    legacy_path.write_text(markdown, encoding="utf-8")
    return final_path


def collect_knowledge(
    client: LightRAGClient,
    queries: list[str],
    include_query_data: bool,
) -> KnowledgeBundle:
    bundle = KnowledgeBundle()

    for question in queries:
        try:
            data = client.query(question)
        except LightRAGClientError as exc:
            bundle.errors.append(f"/query `{question}` 失败：{exc}")
        else:
            bundle.query_results.append({"question": question, "data": data})

        if include_query_data:
            try:
                data = client.query_data(question)
            except LightRAGClientError as exc:
                bundle.query_data_results.append(
                    {"question": question, "data": {"error": str(exc)}}
                )
            else:
                bundle.query_data_results.append({"question": question, "data": data})

    return bundle
