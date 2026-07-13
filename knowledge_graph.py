"""Build a visual knowledge graph from current LightRAG materials."""

from __future__ import annotations

from pathlib import Path
import json
import re
from typing import Any

from lightrag_client import LightRAGClient, LightRAGClientError
from tool_registry import register_tool

MAX_DOCUMENT_NODES = 8
MAX_CONCEPT_NODES = 10
MAX_EDGES = 28
MAX_CONCEPTS_PER_DOCUMENT = 3

TECH_TERMS = [
    "铝电解", "电解槽", "阳极效应", "电流效率", "槽电压", "电解质", "氧化铝", "温度预测",
    "多源数据", "数字孪生", "状态监测", "故障诊断", "趋势预测", "能量平衡", "工艺控制",
    "机器学习", "深度学习", "时序网络", "强化学习", "VMD", "频带能量", "特征融合", "闭环优化",
    "专利", "权利要求", "技术方案", "实施例", "有益效果", "评价指标", "控制方法", "预警方法",
]


@register_tool(
    "build_knowledge_graph",
    "在知识库上传、删除、清空或生成专利前读取当前 LightRAG 素材，并生成覆盖多篇素材的原生网络知识图谱。",
    "Knowledge management",
)
def build_knowledge_graph(client: LightRAGClient, documents: dict[str, Any] | None) -> dict[str, Any]:
    """Return graph data ready for the browser to render as one SVG graph."""
    rows = _flatten_documents(documents or {})
    graph = _fallback_graph(rows)
    if not rows:
        return _simplify_graph(graph)

    native_graph = _build_native_lightrag_graph(client, rows)
    if native_graph:
        return _simplify_graph(native_graph)

    prompt = _graph_prompt(rows)
    try:
        answer = client.query(prompt)
    except LightRAGClientError as exc:
        graph["notes"].append(f"AI 图谱提取失败，已使用文档元数据兜底：{exc}")
        return _simplify_graph(graph)

    text = _extract_answer_text(answer)
    ai_graph = _parse_graph_json(text)
    if ai_graph:
        return _simplify_graph(_merge_ai_graph(rows, ai_graph, text))

    concepts = _extract_concepts("\n".join([text, _documents_text(rows)]))
    if concepts:
        graph = _fallback_graph(rows, extra_concepts=concepts, source="ai_lightrag_text")
        graph["summary"] = _clean_summary(text) or graph["summary"]
        graph["notes"].append("AI 已读取知识库，但返回非 JSON；系统已从回答中抽取概念节点。")
    return _simplify_graph(graph)


def format_knowledge_graph_for_prompt(graph: dict[str, Any] | None) -> str:
    """Knowledge-graph evidence package for downstream patent ideation and drafting."""
    if not graph:
        return "暂无知识图谱。"
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    document_nodes = [node for node in nodes if node.get("type") == "document"]
    entity_nodes = [node for node in nodes if node.get("type") != "document"]
    edge_lines = [
        f"- {edge.get('source_label') or edge.get('source')} --{edge.get('label', '关联')}--> {edge.get('target_label') or edge.get('target')}"
        for edge in edges[:80]
    ]
    document_lines = [
        (
            f"- {node.get('full_label') or node.get('label') or node.get('id')}: "
            f"{node.get('summary') or '暂无摘要'}"
        )
        for node in document_nodes[:20]
    ]
    entity_lines = [
        (
            f"- {node.get('full_label') or node.get('label') or node.get('id')}"
            f"（{node.get('native_type') or node.get('type') or 'entity'}，连接数 {node.get('degree', 0)}）: "
            f"{node.get('summary') or '暂无说明'}"
            f"{'；来源：' + str(node.get('file_path')) if node.get('file_path') else ''}"
        )
        for node in entity_nodes[:80]
    ]
    return "\n".join(
        [
            "【知识图谱证据包】",
            "说明：后续候选生成、章节写作和质量检查只能依据本知识图谱证据包以及外部检索结果，不再直接读取原始文章全文或文档摘要。",
            f"图谱摘要：{graph.get('summary', '暂无摘要')}",
            f"图谱来源：{graph.get('source', 'unknown')}；布局：{graph.get('layout', 'unknown')}",
            f"节点数量：{len(nodes)}；关系数量：{len(edges)}；文档覆盖：{len(document_nodes)} 篇",
            "",
            "文档覆盖节点：",
            *(document_lines or ["- 暂无文档节点。"]),
            "",
            "实体/概念节点：",
            *(entity_lines or ["- 暂无实体节点。"]),
            "",
            "主要关系：",
            *(edge_lines or ["- 暂无关系。"]),
        ]
    )


def _build_native_lightrag_graph(client: LightRAGClient, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        labels_response = client.popular_graph_labels(limit=24)
        labels = _normalize_label_list(labels_response)
        selected_labels = _select_native_labels(labels, rows, limit=max(3, min(8, len(rows) + 2)))
        if not selected_labels:
            return None
    except LightRAGClientError:
        return None

    raw_graphs: list[dict[str, Any]] = []
    for label in selected_labels:
        try:
            raw_graph = client.get_graph(label, max_depth=2, max_nodes=55)
        except LightRAGClientError:
            continue
        if isinstance(raw_graph, dict) and raw_graph.get("nodes"):
            raw_graphs.append(raw_graph)
    if not raw_graphs:
        return None
    return _native_graph_to_app_graph(selected_labels, _merge_native_raw_graphs(raw_graphs), rows)


def _normalize_label_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        items = value.get("labels") or value.get("data") or value.get("items") or []
    else:
        items = []
    labels: list[str] = []
    for item in items:
        label = item.get("label") if isinstance(item, dict) else str(item)
        label = label.strip()
        if label:
            labels.append(label)
    return labels


def _select_native_label(labels: list[str], rows: list[dict[str, Any]]) -> str | None:
    if not labels:
        return None
    material = _documents_text(rows).lower()
    scored = []
    for index, label in enumerate(labels):
        normalized = label.lower()
        score = 0
        if normalized in material:
            score += 10
        score += max(0, 6 - index)
        if any(token.lower() in material for token in re.split(r"[\s_\-/()（）]+", label) if len(token) >= 3):
            score += 2
        scored.append((score, -index, label))
    scored.sort(reverse=True)
    return scored[0][2]


def _select_native_labels(labels: list[str], rows: list[dict[str, Any]], limit: int) -> list[str]:
    if not labels:
        return []
    selected: list[str] = []
    for row in rows:
        material = f"{_doc_label(row)} {row.get('content_summary') or ''}".lower()
        scored: list[tuple[int, int, str]] = []
        for index, label in enumerate(labels):
            if label in selected:
                continue
            normalized = label.lower()
            tokens = [token.lower() for token in re.split(r"[\s_\-/()（）,，]+", label) if len(token) >= 3]
            score = max(0, 8 - index)
            if normalized in material:
                score += 18
            score += sum(4 for token in tokens if token in material)
            if score > 0:
                scored.append((score, -index, label))
        if scored:
            scored.sort(reverse=True)
            selected.append(scored[0][2])
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        global_material = _documents_text(rows).lower()
        global_scores: list[tuple[int, int, str]] = []
        for index, label in enumerate(labels):
            if label in selected:
                continue
            normalized = label.lower()
            tokens = [token.lower() for token in re.split(r"[\s_\-/()（）,，]+", label) if len(token) >= 3]
            score = max(0, 8 - index)
            if normalized in global_material:
                score += 12
            score += sum(3 for token in tokens if token in global_material)
            global_scores.append((score, -index, label))
        global_scores.sort(reverse=True)
        for _, _, label in global_scores:
            selected.append(label)
            if len(selected) >= limit:
                break
    return selected


def _merge_native_raw_graphs(raw_graphs: list[dict[str, Any]]) -> dict[str, Any]:
    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw_graph in raw_graphs:
        for item in raw_graph.get("nodes", []) or []:
            if not isinstance(item, dict):
                continue
            node_key = str(item.get("id") or _native_property(item, "entity_id") or "").strip()
            if not node_key:
                continue
            if node_key not in nodes_by_id:
                nodes_by_id[node_key] = dict(item)
                properties = item.get("properties")
                nodes_by_id[node_key]["properties"] = dict(properties) if isinstance(properties, dict) else {}
                continue
            current = nodes_by_id[node_key]
            current_props = current.setdefault("properties", {})
            item_props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
            for key in ("description", "file_path", "source_id"):
                merged = _merge_sep_values(str(current_props.get(key) or current.get(key) or ""), str(item_props.get(key) or item.get(key) or ""))
                if merged:
                    current_props[key] = merged
        for item in raw_graph.get("edges", []) or []:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "")
            target = str(item.get("target") or "")
            relation = str(_native_property(item, "description") or item.get("type") or "关联")
            if not source or not target:
                continue
            edges_by_key[(source, target, relation[:80])] = item
    return {"nodes": list(nodes_by_id.values()), "edges": list(edges_by_key.values())}


def _merge_sep_values(*values: str) -> str:
    merged: list[str] = []
    for value in values:
        for part in _split_native_values(value):
            if part and part not in merged:
                merged.append(part)
    return "<SEP>".join(merged)


def _native_graph_to_app_graph(labels: list[str], raw_graph: Any, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(raw_graph, dict):
        return None
    native_nodes = [node for node in raw_graph.get("nodes", []) if isinstance(node, dict)]
    native_edges = [edge for edge in raw_graph.get("edges", []) if isinstance(edge, dict)]
    if not native_nodes:
        return None

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    entity_ids: set[str] = set()
    original_to_safe: dict[str, str] = {}
    selected_labels = {label.lower() for label in labels}

    for item in native_nodes:
        original_id = str(item.get("id") or _native_property(item, "entity_id") or "entity")
        node_id = _safe_id(original_id, "entity")
        if node_id in entity_ids:
            continue
        entity_ids.add(node_id)
        original_to_safe[original_id] = node_id
        label_text = str(item.get("id") or _native_property(item, "entity_id") or node_id)
        entity_type = _map_native_entity_type(str(_native_property(item, "entity_type") or "concept"))
        file_paths = _split_native_values(str(_native_property(item, "file_path") or ""))[:4]
        nodes.append(
            {
                "id": node_id,
                "label": _short_label(label_text, 34),
                "full_label": label_text,
                "type": entity_type,
                "native_type": str(_native_property(item, "entity_type") or "concept"),
                "summary": _clean_native_description(str(_native_property(item, "description") or "")),
                "file_path": "；".join(file_paths),
                "source_id": str(_native_property(item, "source_id") or ""),
                "is_focus": label_text.lower() in selected_labels,
            }
        )

    for item in native_edges:
        raw_source = str(item.get("source") or "")
        raw_target = str(item.get("target") or "")
        source = original_to_safe.get(raw_source, _safe_id(raw_source, ""))
        target = original_to_safe.get(raw_target, _safe_id(raw_target, ""))
        if not source or not target or source not in entity_ids or target not in entity_ids:
            continue
        relation_label = _clean_native_description(str(_native_property(item, "description") or item.get("type") or "关联"))[:32]
        edges.append(
            {
                "source": source,
                "target": target,
                "source_label": raw_source or source,
                "target_label": raw_target or target,
                "label": relation_label or "关联",
            }
        )

    if not any(node.get("is_focus") for node in nodes):
        focus_label = "全库知识图谱"
        focus_id = _safe_id(focus_label, "knowledge_root")
        nodes.insert(
            0,
            {
                "id": focus_id,
                "label": focus_label,
                "full_label": focus_label,
                "type": "root",
                "summary": f"LightRAG 原生知识图谱聚合了 {len(labels)} 个代表实体。",
                "is_focus": True,
            },
        )
        entity_ids.add(focus_id)
        for node in nodes[1:8]:
            edges.append(
                {
                    "source": focus_id,
                    "target": str(node.get("id")),
                    "source_label": focus_label,
                    "target_label": str(node.get("full_label") or node.get("label") or node.get("id")),
                    "label": "邻近",
                }
            )

    _attach_document_coverage(rows, nodes, edges)

    return {
        "summary": f"已直接调用 LightRAG 原生知识图谱，按 {len(labels)} 个代表实体合并展示全库 {len(rows)} 篇素材的关系网络。",
        "nodes": nodes,
        "edges": _dedupe_edges(edges),
        "source": "lightrag_native_graph",
        "layout": "native-network",
        "updated_at": "",
        "notes": ["当前图谱来自 LightRAG 原生 /graph 与 /graphs API，已按多素材合并网络图方式展示。"],
    }


def _attach_document_coverage(rows: list[dict[str, Any]], nodes: list[dict[str, Any]], edges: list[dict[str, str]]) -> None:
    existing_ids = {str(node.get("id")) for node in nodes}
    entity_nodes = [node for node in nodes if node.get("type") != "document"]
    for index, row in enumerate(rows[:MAX_DOCUMENT_NODES], start=1):
        doc_label = _doc_label(row)
        doc_id = _safe_id(f"doc_{index}_{doc_label}", f"doc_{index}")
        doc_summary = str(row.get("content_summary") or "暂无摘要")[:240]
        if doc_id not in existing_ids:
            nodes.append(
                {
                    "id": doc_id,
                    "label": _short_label(doc_label, 34),
                    "full_label": doc_label,
                    "type": "document",
                    "native_type": "document",
                    "summary": doc_summary,
                    "file_path": doc_label,
                    "source_id": str(row.get("id") or row.get("doc_id") or ""),
                }
            )
            existing_ids.add(doc_id)

        matches = _document_entity_matches(row, entity_nodes)
        for node in matches[:5]:
            edges.append(
                {
                    "source": doc_id,
                    "target": str(node.get("id")),
                    "source_label": doc_label,
                    "target_label": str(node.get("full_label") or node.get("label") or node.get("id")),
                    "label": "来源/支撑",
                }
            )


def _document_entity_matches(row: dict[str, Any], entity_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    doc_label = _doc_label(row)
    doc_text = f"{doc_label} {row.get('content_summary') or ''}".lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for node in entity_nodes:
        label = str(node.get("full_label") or node.get("label") or "")
        if not label:
            continue
        label_lower = label.lower()
        file_path = str(node.get("file_path") or "").lower()
        summary = str(node.get("summary") or "").lower()
        tokens = [token.lower() for token in re.split(r"[\s_\-/()（）,，]+", label) if len(token) >= 3]
        score = 0
        if doc_label.lower() and doc_label.lower() in file_path:
            score += 20
        if label_lower in doc_text:
            score += 12
        score += sum(3 for token in tokens if token in doc_text)
        if score == 0 and any(token in summary for token in tokens[:3]):
            score += 1
        if score > 0:
            scored.append((score, node))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        return [node for _, node in scored]
    return sorted(entity_nodes, key=lambda node: int(node.get("degree") or 0), reverse=True)[:2]


def _native_property(item: dict[str, Any], key: str) -> Any:
    properties = item.get("properties") if isinstance(item.get("properties"), dict) else {}
    return properties.get(key, item.get(key))


def _split_native_values(value: str) -> list[str]:
    return [part.strip() for part in value.split("<SEP>") if part.strip()]


def _clean_native_description(value: str) -> str:
    return "；".join(_split_native_values(value))[:260]


def _map_native_entity_type(value: str) -> str:
    normalized = value.lower().strip()
    if normalized in {"method", "algorithm", "model"}:
        return "method"
    if normalized in {"artifact", "system", "component"}:
        return "method"
    if normalized in {"data", "metric", "indicator"}:
        return "metric"
    if normalized in {"event", "scenario", "application"}:
        return "scenario"
    if normalized in {"problem", "issue", "risk"}:
        return "problem"
    return "concept"


def _short_label(value: str, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1] + "…"


def _graph_prompt(rows: list[dict[str, Any]]) -> str:
    docs = "\n".join(
        f"- 文档ID: doc_{index}; 文件: {_doc_label(row)}; 摘要: {str(row.get('content_summary') or '')[:500]}"
        for index, row in enumerate(rows[:MAX_DOCUMENT_NODES], start=1)
    )
    return f"""请读取当前知识库素材，抽取一张用于专利构思的知识图谱。

要求：
1. 只输出 JSON，不要输出 Markdown 代码块。
2. concept/problem/method/scenario/metric 节点总数不超过 10 个。
3. 节点 label 必须短，建议 4-10 个汉字，不能把整句话当节点。
4. edges 总数不超过 24 条；每个文档最多连接 3 个核心概念。
5. 不要编造不存在于知识库中的具体实验数据。
6. 输出结构固定为：
{{
  "summary": "一句话概括当前知识库主题和材料分布",
  "nodes": [{{"id": "concept_1", "label": "概念名", "type": "concept", "summary": "一句话说明"}}],
  "edges": [{{"source": "doc_1", "target": "concept_1", "label": "涉及/支撑/约束/关联"}}]
}}

当前文档：
{docs}
"""


def _fallback_graph(
    rows: list[dict[str, Any]],
    extra_concepts: list[str] | None = None,
    source: str = "document_metadata",
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {
            "id": "knowledge_root",
            "label": "当前知识库",
            "type": "root",
            "summary": f"共 {len(rows)} 份素材。",
        }
    ]
    edges: list[dict[str, str]] = []
    concept_map: dict[str, str] = {}

    for index, row in enumerate(rows[:MAX_DOCUMENT_NODES], start=1):
        doc_id = f"doc_{index}"
        label = _doc_label(row)
        nodes.append(
            {
                "id": doc_id,
                "label": label,
                "type": "document",
                "summary": str(row.get("content_summary") or "暂无摘要")[:240],
                "status": str(row.get("status") or "未知"),
                "chunks_count": int(row.get("chunks_count") or 0),
            }
        )
        edges.append(
            {
                "source": "knowledge_root",
                "target": doc_id,
                "source_label": "当前知识库",
                "target_label": label,
                "label": "包含",
            }
        )
        doc_text = f"{label}\n{row.get('content_summary') or ''}"
        for concept in _extract_concepts(doc_text)[:MAX_CONCEPTS_PER_DOCUMENT]:
            concept_id = concept_map.setdefault(concept, f"concept_{len(concept_map) + 1}")
            if not any(node["id"] == concept_id for node in nodes):
                nodes.append({"id": concept_id, "label": concept, "type": "concept", "summary": "由当前素材抽取的主题概念。"})
            edges.append(
                {
                    "source": doc_id,
                    "target": concept_id,
                    "source_label": label,
                    "target_label": concept,
                    "label": "涉及",
                }
            )

    for concept in (extra_concepts or [])[:MAX_CONCEPT_NODES]:
        concept_id = concept_map.setdefault(concept, f"concept_{len(concept_map) + 1}")
        if not any(node["id"] == concept_id for node in nodes):
            nodes.append({"id": concept_id, "label": concept, "type": "concept", "summary": "由 AI 读取知识库后抽取的主题概念。"})
        edges.append(
            {
                "source": "knowledge_root",
                "target": concept_id,
                "source_label": "当前知识库",
                "target_label": concept,
                "label": "提炼",
            }
        )

    return {
        "summary": _graph_summary(rows, [node["label"] for node in nodes if node.get("type") == "concept"]),
        "nodes": nodes[:1 + MAX_DOCUMENT_NODES + MAX_CONCEPT_NODES],
        "edges": _dedupe_edges(edges)[:MAX_EDGES],
        "source": source,
        "updated_at": "",
        "notes": [],
    }


def _merge_ai_graph(rows: list[dict[str, Any]], ai_graph: dict[str, Any], raw_text: str) -> dict[str, Any]:
    base = _fallback_graph(rows, source="ai_lightrag_json")
    nodes = {node["id"]: node for node in base["nodes"]}
    label_by_id = {node["id"]: node.get("label", node["id"]) for node in base["nodes"]}

    for item in ai_graph.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        node_id = _safe_id(str(item.get("id") or item.get("label") or "concept"), "concept")
        label = str(item.get("label") or node_id).strip()[:40]
        if not label:
            continue
        if node_id.startswith("doc_") and node_id in nodes:
            continue
        node_type = str(item.get("type") or "concept").strip().lower()
        if node_type not in {"concept", "problem", "method", "scenario", "metric", "document"}:
            node_type = "concept"
        nodes[node_id] = {
            "id": node_id,
            "label": label,
            "type": node_type,
            "summary": str(item.get("summary") or "")[:180],
        }
        label_by_id[node_id] = label

    edges = list(base["edges"])
    for item in ai_graph.get("edges") or []:
        if not isinstance(item, dict):
            continue
        source = _safe_id(str(item.get("source") or ""), "")
        target = _safe_id(str(item.get("target") or ""), "")
        if not source or not target or source not in nodes or target not in nodes:
            continue
        edges.append(
            {
                "source": source,
                "target": target,
                "source_label": label_by_id.get(source, source),
                "target_label": label_by_id.get(target, target),
                "label": str(item.get("label") or "关联")[:16],
            }
        )

    graph = {
        "summary": str(ai_graph.get("summary") or _clean_summary(raw_text) or base["summary"])[:240],
        "nodes": list(nodes.values())[:1 + MAX_DOCUMENT_NODES + MAX_CONCEPT_NODES],
        "edges": _dedupe_edges(edges)[:MAX_EDGES],
        "source": "ai_lightrag_json",
        "updated_at": "",
        "notes": ["AI 已读取当前知识库并生成结构化知识图谱。"],
    }
    return graph


def _simplify_graph(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = graph.get("nodes") or []
    edges = _dedupe_edges(graph.get("edges") or [])
    if graph.get("source") == "lightrag_native_graph":
        degree: dict[str, int] = {}
        for edge in edges:
            degree[str(edge.get("source") or "")] = degree.get(str(edge.get("source") or ""), 0) + 1
            degree[str(edge.get("target") or "")] = degree.get(str(edge.get("target") or ""), 0) + 1
        ranked_nodes = sorted(
            nodes,
            key=lambda node: (
                bool(node.get("is_focus")),
                str(node.get("type")) == "document",
                degree.get(str(node.get("id")), 0),
                str(node.get("label") or ""),
            ),
            reverse=True,
        )[:60]
        kept_ids = {str(node.get("id")) for node in ranked_nodes}
        graph = dict(graph)
        graph["nodes"] = [dict(node, degree=degree.get(str(node.get("id")), 0)) for node in ranked_nodes]
        graph["edges"] = [edge for edge in edges if str(edge.get("source")) in kept_ids and str(edge.get("target")) in kept_ids][:120]
        graph["layout"] = "native-network"
        graph.setdefault("notes", [])
        return graph

    root_nodes = [node for node in nodes if node.get("type") == "root"][:1]
    doc_nodes = [node for node in nodes if node.get("type") == "document"][:MAX_DOCUMENT_NODES]
    concept_nodes = [
        node for node in nodes
        if node.get("type") not in {"root", "document"}
    ][:MAX_CONCEPT_NODES]
    kept_nodes = [*(root_nodes or nodes[:1]), *doc_nodes, *concept_nodes]
    kept_ids = {str(node.get("id")) for node in kept_nodes}
    simplified_edges: list[dict[str, str]] = []
    per_doc_edges: dict[str, int] = {}
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in kept_ids or target not in kept_ids:
            continue
        doc_id = source if source.startswith("doc_") else target if target.startswith("doc_") else ""
        if doc_id and not (source == "knowledge_root" or target == "knowledge_root"):
            count = per_doc_edges.get(doc_id, 0)
            if count >= MAX_CONCEPTS_PER_DOCUMENT:
                continue
            per_doc_edges[doc_id] = count + 1
        simplified_edges.append(edge)
        if len(simplified_edges) >= MAX_EDGES:
            break
    graph = dict(graph)
    graph["nodes"] = kept_nodes
    graph["edges"] = simplified_edges
    graph["layout"] = "layered-readable"
    graph.setdefault("notes", [])
    return graph


def _flatten_documents(documents: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    statuses = documents.get("statuses")
    if isinstance(statuses, dict):
        for status, items in statuses.items():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        copied = dict(item)
                        copied.setdefault("status", status)
                        rows.append(copied)
    for key in ("documents", "rows", "data"):
        items = documents.get(key)
        if isinstance(items, list):
            rows.extend([item for item in items if isinstance(item, dict)])
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("id") or row.get("doc_id") or row.get("file_path") or row.get("filename") or row)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _doc_label(row: dict[str, Any]) -> str:
    raw = str(row.get("file_path") or row.get("filename") or row.get("id") or "未命名素材")
    return Path(raw).name[:52]


def _documents_text(rows: list[dict[str, Any]]) -> str:
    return "\n".join(f"{_doc_label(row)} {row.get('content_summary') or ''}" for row in rows)


def _extract_answer_text(answer: Any) -> str:
    if isinstance(answer, dict):
        for key in ("response", "answer", "content", "text"):
            value = answer.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(answer, ensure_ascii=False)
    return str(answer or "").strip()


def _parse_graph_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    candidates = [cleaned]
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("nodes"), list) and isinstance(data.get("edges"), list):
            return data
    return None


def _extract_concepts(text: str) -> list[str]:
    found: list[str] = []
    for term in TECH_TERMS:
        if term.lower() in text.lower():
            found.append(term)
    chinese_terms = re.findall(r"[一-鿿A-Za-z0-9]{3,16}(?:方法|系统|模型|算法|指标|特征|机制|流程|控制|预测|诊断|预警|优化|分析)", text)
    found.extend(chinese_terms)
    unique: list[str] = []
    seen: set[str] = set()
    for item in found:
        label = item.strip(" ，。；：:、()（）[]【】\n\t")[:24]
        if len(label) < 2 or label in seen:
            continue
        seen.add(label)
        unique.append(label)
    return unique[:MAX_CONCEPT_NODES]


def _graph_summary(rows: list[dict[str, Any]], concepts: list[str]) -> str:
    if not rows:
        return "知识库暂无素材，图谱为空。"
    topic = "、".join(concepts[:5]) if concepts else "当前素材主题"
    return f"当前知识库包含 {len(rows)} 份素材，核心关联集中在：{topic}。"


def _clean_summary(text: str) -> str:
    first = re.split(r"[。\n]", text.strip(), maxsplit=1)[0]
    return first[:180]


def _safe_id(value: str, default_prefix: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned and default_prefix:
        cleaned = default_prefix
    return cleaned[:48]


def _dedupe_edges(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, str]] = []
    for edge in edges:
        key = (edge.get("source", ""), edge.get("target", ""), edge.get("label", ""))
        if key in seen or not key[0] or not key[1]:
            continue
        seen.add(key)
        result.append(edge)
    return result
