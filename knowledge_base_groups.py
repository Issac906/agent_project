"""Persist independently deployed LightRAG knowledge-base registrations."""

from __future__ import annotations

from datetime import datetime
import json
import re
from threading import Lock
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from runtime_paths import data_path


CATALOG_PATH = data_path("knowledge_base_catalog.json")
CATALOG_LOCK = Lock()
UNASSIGNED_ID = "unassigned"


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
    """Return user-managed KBs and attach the matching graph subset to each one."""
    rows = [doc for doc in documents if isinstance(doc, dict)]
    with CATALOG_LOCK:
        catalog = _load_catalog()
        if not catalog.get("initialized"):
            catalog = _bootstrap_catalog(rows)
            _save_catalog(catalog)

    assignments = catalog.get("assignments") or {}
    groups_by_id = {
        str(item.get("id")): {
            "id": str(item.get("id")),
            "name": str(item.get("name") or "未命名知识库"),
            "description": str(item.get("description") or "由用户管理的知识库。"),
            "keywords": [],
            "documents": [],
            "user_managed": True,
        }
        for item in catalog.get("knowledge_bases", [])
        if isinstance(item, dict) and item.get("id")
    }
    unassigned: list[dict[str, Any]] = []
    for row in rows:
        knowledge_base_id = _assigned_knowledge_base_id(row, assignments)
        bucket = groups_by_id.get(knowledge_base_id)
        if bucket is None:
            unassigned.append(row)
            continue
        bucket["documents"].append(row)

    groups = list(groups_by_id.values())
    if unassigned:
        groups.append(
            {
                "id": UNASSIGNED_ID,
                "name": "待分类素材",
                "description": "这些素材尚未由用户指定知识库，不会由 AI 自动分类。",
                "keywords": [],
                "documents": unassigned,
                "user_managed": True,
                "selectable": False,
            }
        )
    groups.sort(key=lambda item: (item.get("id") == UNASSIGNED_ID, item["name"]))
    for group in groups:
        group["graph"] = _filter_graph_for_group(graph, group["documents"], group["name"])
        group["document_count"] = len(group["documents"])
        group["node_count"] = len(group["graph"].get("nodes", []))
        group["edge_count"] = len(group["graph"].get("edges", []))
        group.setdefault("selectable", True)
    return groups


def list_knowledge_base_catalog() -> list[dict[str, Any]]:
    """List destinations available to the upload form."""
    with CATALOG_LOCK:
        catalog = _load_catalog()
    return [
        {
            "id": str(item.get("id")),
            "name": str(item.get("name") or "未命名知识库"),
            "description": str(item.get("description") or ""),
            "base_url": str(item.get("base_url") or ""),
            "graph_url": graph_webui_url(str(item.get("base_url") or "")),
            "isolation": "physical" if item.get("base_url") else "migration_required",
            "selectable": bool(item.get("base_url")),
            "manager_instance_id": str(item.get("manager_instance_id") or ""),
            "managed": bool(item.get("manager_instance_id")),
        }
        for item in catalog.get("knowledge_bases", [])
        if isinstance(item, dict) and item.get("id")
    ]


def create_knowledge_base(
    name: str,
    description: str = "",
    base_url: str = "",
    *,
    manager_instance_id: str = "",
) -> dict[str, Any]:
    """Register a user-named physical LightRAG knowledge base instance."""
    clean_name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not clean_name:
        raise ValueError("请输入新知识库名称。")
    clean_base_url = normalize_instance_url(base_url)
    if not clean_base_url:
        raise ValueError("物理隔离知识库必须填写独立的 LightRAG API 地址。")
    with CATALOG_LOCK:
        catalog = _load_catalog()
        catalog["initialized"] = True
        existing = {
            str(item.get("name") or "").strip().casefold(): item
            for item in catalog.get("knowledge_bases", [])
            if isinstance(item, dict)
        }
        if clean_name.casefold() in existing:
            raise ValueError("已存在同名知识库，请直接选择该知识库。")
        if any(normalize_instance_url(str(row.get("base_url") or "")) == clean_base_url for row in catalog.get("knowledge_bases", []) if row.get("base_url")):
            raise ValueError("该 LightRAG 实例已经绑定到另一个知识库。")
        used_ids = {str(item.get("id")) for item in catalog.get("knowledge_bases", []) if isinstance(item, dict)}
        base_id = _slug(clean_name) or "knowledge-base"
        knowledge_base_id = base_id
        suffix = 2
        while knowledge_base_id in used_ids or knowledge_base_id in {"all", UNASSIGNED_ID}:
            knowledge_base_id = f"{base_id}-{suffix}"
            suffix += 1
        item = {
            "id": knowledge_base_id,
            "name": clean_name,
            "description": str(description or "").strip() or f"由用户创建的“{clean_name}”。",
            "base_url": clean_base_url,
            "manager_instance_id": str(manager_instance_id or "").strip(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        catalog.setdefault("knowledge_bases", []).append(item)
        _save_catalog(catalog)
    return _public_catalog_item(item)


def delete_knowledge_base_registration(knowledge_base_id: str) -> dict[str, Any]:
    """Remove one isolated instance registration; the total KB is not stored here."""
    clean_id = str(knowledge_base_id or "").strip()
    with CATALOG_LOCK:
        catalog = _load_catalog()
        item = next(
            (
                row
                for row in catalog.get("knowledge_bases", [])
                if isinstance(row, dict) and str(row.get("id")) == clean_id
            ),
            None,
        )
        if not item:
            raise ValueError("知识库不存在。")
        catalog["knowledge_bases"] = [
            row for row in catalog.get("knowledge_bases", []) if str(row.get("id")) != clean_id
        ]
        _save_catalog(catalog)
    return _public_catalog_item(item)


def require_knowledge_base(knowledge_base_id: str, require_configured: bool = True) -> dict[str, Any]:
    clean_id = str(knowledge_base_id or "").strip()
    with CATALOG_LOCK:
        catalog = _load_catalog()
    item = next(
        (
            row
            for row in catalog.get("knowledge_bases", [])
            if isinstance(row, dict) and str(row.get("id")) == clean_id
        ),
        None,
    )
    if not item:
        raise ValueError("请选择一个有效的已有知识库。")
    result = _public_catalog_item(item)
    if require_configured and not result["base_url"]:
        raise ValueError("该知识库是旧版逻辑分组，尚未绑定独立 LightRAG 实例，不能用于物理隔离操作。")
    return result


def update_knowledge_base_instance(knowledge_base_id: str, base_url: str) -> dict[str, Any]:
    """Bind a legacy catalog entry to a dedicated LightRAG server instance."""
    clean_url = normalize_instance_url(base_url)
    if not clean_url:
        raise ValueError("请输入独立 LightRAG API 地址。")
    with CATALOG_LOCK:
        catalog = _load_catalog()
        item = next((row for row in catalog.get("knowledge_bases", []) if str(row.get("id")) == knowledge_base_id), None)
        if not item:
            raise ValueError("知识库不存在。")
        if any(
            str(row.get("id")) != knowledge_base_id
            and row.get("base_url")
            and normalize_instance_url(str(row.get("base_url"))) == clean_url
            for row in catalog.get("knowledge_bases", [])
        ):
            raise ValueError("该 LightRAG 实例已经绑定到另一个知识库。")
        item["base_url"] = clean_url
        catalog["version"] = 2
        _save_catalog(catalog)
    return _public_catalog_item(item)


def normalize_instance_url(base_url: str) -> str:
    cleaned = str(base_url or "").strip()
    if not cleaned:
        return ""
    parts = urlsplit(cleaned)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("LightRAG API 地址必须是完整的 http:// 或 https:// 地址。")
    path = parts.path.rstrip("/")
    if path.endswith("/webui"):
        path = path[:-6]
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")


def graph_webui_url(base_url: str) -> str:
    clean = normalize_instance_url(base_url)
    return f"{clean}/webui/?tab=knowledge-graph#/" if clean else ""


def _public_catalog_item(item: dict[str, Any]) -> dict[str, Any]:
    base_url = str(item.get("base_url") or "")
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("id") or "未命名知识库"),
        "description": str(item.get("description") or ""),
        "base_url": base_url,
        "graph_url": graph_webui_url(base_url),
        "isolation": "physical" if base_url else "migration_required",
        "selectable": bool(base_url),
        "manager_instance_id": str(item.get("manager_instance_id") or ""),
        "managed": bool(item.get("manager_instance_id")),
    }


def assign_document_to_knowledge_base(
    knowledge_base_id: str,
    *,
    filename: str = "",
    document_id: str = "",
) -> None:
    """Persist an explicit user assignment using stable document references."""
    require_knowledge_base(knowledge_base_id)
    references = _reference_candidates({"id": document_id, "file_path": filename, "filename": filename})
    if not references:
        raise ValueError("无法识别要分配的素材。")
    with CATALOG_LOCK:
        catalog = _load_catalog()
        assignments = catalog.setdefault("assignments", {})
        for reference in references:
            assignments[reference] = knowledge_base_id
        _save_catalog(catalog)


def remove_document_assignments(documents: list[dict[str, Any]]) -> None:
    """Remove stale assignment keys after users delete documents."""
    references = {ref for row in documents for ref in _reference_candidates(row)}
    if not references:
        return
    with CATALOG_LOCK:
        catalog = _load_catalog()
        assignments = catalog.setdefault("assignments", {})
        changed = False
        for reference in references:
            if reference in assignments:
                assignments.pop(reference, None)
                changed = True
        if changed:
            _save_catalog(catalog)


def clear_document_assignments() -> None:
    """Clear assignments while keeping user-created knowledge base destinations."""
    with CATALOG_LOCK:
        catalog = _load_catalog()
        catalog["initialized"] = True
        catalog["assignments"] = {}
        _save_catalog(catalog)


def _load_catalog() -> dict[str, Any]:
    try:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    return {
        "version": int(payload.get("version") or 1),
        "initialized": bool(payload.get("initialized")),
        "knowledge_bases": payload.get("knowledge_bases") if isinstance(payload.get("knowledge_bases"), list) else [],
        "assignments": payload.get("assignments") if isinstance(payload.get("assignments"), dict) else {},
    }


def _save_catalog(catalog: dict[str, Any]) -> None:
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CATALOG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CATALOG_PATH)


def _bootstrap_catalog(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Preserve existing automatic groups once, then switch permanently to user control."""
    buckets: dict[str, dict[str, Any]] = {}
    assignments: dict[str, str] = {}
    for row in rows:
        profile = _best_profile(row) or _fallback_profile(row)
        buckets.setdefault(
            profile["id"],
            {
                "id": profile["id"],
                "name": profile["name"],
                "description": profile["description"],
                "created_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        for reference in _reference_candidates(row):
            assignments[reference] = profile["id"]
    return {
        "version": 1,
        "initialized": True,
        "knowledge_bases": sorted(buckets.values(), key=lambda item: item["name"]),
        "assignments": assignments,
    }


def _assigned_knowledge_base_id(row: dict[str, Any], assignments: dict[str, Any]) -> str:
    for reference in _reference_candidates(row):
        value = str(assignments.get(reference) or "").strip()
        if value:
            return value
    return ""


def _reference_candidates(row: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    document_id = str(row.get("id") or row.get("document_id") or row.get("doc_id") or "").strip()
    file_path = str(row.get("file_path") or row.get("filename") or "").strip()
    if document_id:
        candidates.append(f"id:{document_id}")
    if file_path:
        normalized_path = file_path.replace("\\", "/").strip().casefold()
        candidates.append(f"path:{normalized_path}")
        candidates.append(f"name:{normalized_path.rsplit('/', 1)[-1]}")
    return list(dict.fromkeys(candidates))


def _slug(value: str) -> str:
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if ascii_slug:
        return ascii_slug[:48]
    chinese_slug = re.sub(r"[^\u4e00-\u9fa5]+", "-", value).strip("-")
    return f"kb-{abs(hash(chinese_slug)) % 10**10}" if chinese_slug else ""


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
