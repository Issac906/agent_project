"""Build virtual content-specific knowledge bases from LightRAG documents."""

from __future__ import annotations

import re
from typing import Any


TOPIC_PROFILES = [
    {
        "id": "aluminum-electrolysis",
        "name": "铝电解工艺知识库",
        "description": "铝电解槽、电解质组成、电流效率、阳极效应、温度与工艺控制相关素材。",
        "keywords": [
            "铝电解",
            "铝",
            "aluminium",
            "aluminum",
            "electrolyte",
            "alumina",
            "alf3",
            "cryolite",
            "电解槽",
            "电解质",
            "氧化铝",
            "阳极",
            "电流效率",
            "槽电压",
        ],
    },
    {
        "id": "oilfield-injection",
        "name": "油田注采分析知识库",
        "description": "注采井、井组连通性、油藏动态、稠油开发和生产优化相关素材。",
        "keywords": [
            "油田",
            "油藏",
            "稠油",
            "注采",
            "注汽",
            "井组",
            "连通性",
            "采油",
            "waterflood",
            "reservoir",
            "well",
            "injection",
        ],
    },
    {
        "id": "ai-industrial-modeling",
        "name": "工业智能建模知识库",
        "description": "机器学习、数字孪生、状态监测、预测控制和工业数据分析相关素材。",
        "keywords": [
            "机器学习",
            "深度学习",
            "数字孪生",
            "状态监测",
            "故障诊断",
            "预测",
            "控制",
            "优化",
            "模型",
            "算法",
            "neural",
            "learning",
            "digital twin",
            "monitoring",
        ],
    },
    {
        "id": "patent-prior-art",
        "name": "专利与现有技术知识库",
        "description": "专利、权利要求、现有技术、技术交底和避重分析相关素材。",
        "keywords": [
            "专利",
            "权利要求",
            "技术交底",
            "现有技术",
            "prior art",
            "patent",
            "claim",
            "invention",
        ],
    },
]


def build_virtual_knowledge_bases(
    documents: list[dict[str, Any]],
    graph: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Classify current documents into virtual KBs and attach a graph to each group."""
    rows = [doc for doc in documents if isinstance(doc, dict)]
    if not rows:
        return []

    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        profile = _best_profile(row)
        if not profile:
            profile = _fallback_profile(row)
        bucket = buckets.setdefault(
            profile["id"],
            {
                "id": profile["id"],
                "name": profile["name"],
                "description": profile["description"],
                "keywords": profile.get("keywords", [])[:8],
                "documents": [],
            },
        )
        bucket["documents"].append(row)

    groups = list(buckets.values())
    groups.sort(key=lambda item: (-len(item["documents"]), item["name"]))
    for group in groups:
        group["graph"] = _filter_graph_for_group(graph, group["documents"], group["name"])
        group["document_count"] = len(group["documents"])
        group["node_count"] = len(group["graph"].get("nodes", []))
        group["edge_count"] = len(group["graph"].get("edges", []))
    return groups


def _best_profile(row: dict[str, Any]) -> dict[str, Any] | None:
    text = _document_text(row)
    scored: list[tuple[int, dict[str, Any]]] = []
    for profile in TOPIC_PROFILES:
        score = sum(_keyword_score(text, keyword) for keyword in profile["keywords"])
        if score:
            scored.append((score, profile))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _fallback_profile(row: dict[str, Any]) -> dict[str, Any]:
    label = _doc_label(row)
    tokens = _tokens(label)
    topic = tokens[0] if tokens else "综合素材"
    safe_id = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fa5]+", "-", topic).strip("-") or "general"
    return {
        "id": f"topic-{safe_id.lower()}",
        "name": f"{topic}知识库",
        "description": f"围绕“{topic}”自动建立的素材分组。",
        "keywords": tokens[:6],
    }


def _filter_graph_for_group(
    graph: dict[str, Any] | None,
    documents: list[dict[str, Any]],
    group_name: str,
) -> dict[str, Any]:
    if not isinstance(graph, dict):
        return {
            "source": "virtual_knowledge_base",
            "layout": "native-network",
            "summary": f"{group_name} 暂无可用图谱。",
            "nodes": [],
            "edges": [],
            "notes": ["未读取到全局知识图谱，无法生成子图。"],
        }

    raw_nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict) and node.get("id")]
    raw_edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict) and edge.get("source") and edge.get("target")]
    doc_labels = {_doc_label(doc) for doc in documents}
    max_document_nodes = max(1, len(documents))
    max_entity_nodes = max(6, min(36, len(documents) * 8))
    max_edges = max(8, min(80, len(documents) * 14))
    doc_terms = set()
    for doc in documents:
        doc_terms.update(_tokens(_document_text(doc))[:10])

    document_ids: set[str] = set()
    sourced_entity_scores: list[tuple[int, str]] = []
    term_entity_scores: list[tuple[int, str]] = []
    for node in raw_nodes:
        node_text = _node_text(node)
        if _is_document_node_for_group(node, doc_labels):
            document_ids.add(str(node["id"]))
            continue
        source_score = _source_match_score(node, doc_labels)
        if source_score:
            sourced_entity_scores.append((source_score + int(node.get("degree") or 0), str(node["id"])))
            continue
        term_score = _term_match_score(node_text, doc_terms)
        if term_score:
            term_entity_scores.append((term_score + min(6, int(node.get("degree") or 0)), str(node["id"])))

    connected_to_docs: set[str] = set()
    for edge in raw_edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        if source in document_ids and target not in document_ids:
            connected_to_docs.add(target)
        if target in document_ids and source not in document_ids:
            connected_to_docs.add(source)

    selected_entity_ids: list[str] = []
    for _, node_id in sorted(sourced_entity_scores, reverse=True):
        if node_id not in selected_entity_ids:
            selected_entity_ids.append(node_id)
    for node_id in connected_to_docs:
        if node_id not in selected_entity_ids:
            selected_entity_ids.append(node_id)
    if not selected_entity_ids:
        for _, node_id in sorted(term_entity_scores, reverse=True):
            if node_id not in selected_entity_ids:
                selected_entity_ids.append(node_id)

    selected_ids = set(list(document_ids)[:max_document_nodes]) | set(selected_entity_ids[:max_entity_nodes])
    if not selected_ids:
        selected_ids = _fallback_group_node_ids(raw_nodes, doc_terms, max_entity_nodes)

    nodes = [dict(node) for node in raw_nodes if str(node.get("id")) in selected_ids]
    node_ids = {str(node.get("id")) for node in nodes}
    edges = [
        dict(edge)
        for edge in raw_edges
        if str(edge.get("source")) in node_ids and str(edge.get("target")) in node_ids
    ][:max_edges]
    _recompute_degrees(nodes, edges)
    return {
        "source": f"{graph.get('source') or 'lightrag'}:virtual_group",
        "layout": graph.get("layout") or "native-network",
        "summary": f"{group_name}：覆盖 {len(documents)} 份素材、{len(nodes)} 个图谱节点、{len(edges)} 条关系。",
        "nodes": nodes,
        "edges": edges,
        "notes": graph.get("notes") or [],
    }


def _is_document_node_for_group(node: dict[str, Any], doc_labels: set[str]) -> bool:
    if node.get("type") != "document":
        return False
    node_label = str(node.get("full_label") or node.get("label") or node.get("file_path") or "")
    return any(_loose_contains(node_label, label) for label in doc_labels)


def _shares_source(text: str, doc_labels: set[str]) -> bool:
    return any(_loose_contains(text, label) or _loose_contains(label, text) for label in doc_labels if label)


def _shares_terms(text: str, terms: set[str]) -> bool:
    normalized = text.lower()
    return any(term and term.lower() in normalized for term in terms)


def _source_match_score(node: dict[str, Any], doc_labels: set[str]) -> int:
    file_path = str(node.get("file_path") or "")
    source_id = str(node.get("source_id") or "")
    text = f"{file_path} {source_id}"
    score = 0
    for label in doc_labels:
        if not label:
            continue
        if _loose_contains(text, label):
            score += 20
        label_name = label.rsplit("/", 1)[-1]
        if label_name != label and _loose_contains(text, label_name):
            score += 12
    return score


def _term_match_score(text: str, terms: set[str]) -> int:
    normalized = text.lower()
    score = 0
    for term in terms:
        if term and term.lower() in normalized:
            score += 3 if len(term) >= 4 else 1
    return score


def _fallback_group_node_ids(raw_nodes: list[dict[str, Any]], terms: set[str], limit: int) -> set[str]:
    scored: list[tuple[int, str]] = []
    for node in raw_nodes:
        if node.get("type") == "root":
            continue
        score = _term_match_score(_node_text(node), terms) + int(node.get("degree") or 0)
        if node.get("type") == "document":
            score += 4
        scored.append((score, str(node.get("id"))))
    scored.sort(reverse=True)
    return {node_id for _, node_id in scored[:limit]}


def _keyword_score(text: str, keyword: str) -> int:
    normalized = text.lower()
    keyword_normalized = keyword.lower()
    if keyword_normalized in normalized:
        return 6 if len(keyword) >= 4 else 3
    return 0


def _document_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("file_path", "filename", "content_summary", "summary", "status")
    )


def _doc_label(row: dict[str, Any]) -> str:
    return str(row.get("file_path") or row.get("filename") or row.get("id") or "未知文档")


def _node_text(node: dict[str, Any]) -> str:
    return " ".join(
        str(node.get(key) or "")
        for key in ("id", "label", "full_label", "summary", "file_path", "source_id", "native_type", "type")
    )


def _tokens(text: str) -> list[str]:
    raw_tokens = re.split(r"[\s_\-/()（）,，.。:：;；\[\]【】]+", str(text))
    cleaned: list[str] = []
    for token in raw_tokens:
        token = token.strip()
        if len(token) < 2:
            continue
        if token.lower() in {"pdf", "docx", "html", "ppt", "xlsx", "presentation", "report"}:
            continue
        if token not in cleaned:
            cleaned.append(token)
    return cleaned


def _loose_contains(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_norm = re.sub(r"\s+", "", str(left).lower())
    right_norm = re.sub(r"\s+", "", str(right).lower())
    return bool(left_norm and right_norm and (left_norm in right_norm or right_norm in left_norm))


def _recompute_degrees(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    degree = {str(node.get("id")): 0 for node in nodes}
    for edge in edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        if source in degree:
            degree[source] += 1
        if target in degree:
            degree[target] += 1
    for node in nodes:
        node["degree"] = degree.get(str(node.get("id")), 0)
