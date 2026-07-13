"""Explain how knowledge-base and external materials are combined for ideas."""

from __future__ import annotations

import math
import re
from typing import Any

from external_search import ExternalSearchResult
from patent_discovery_agent import PatentCandidate


LAYER_DEFINITIONS = [
    {
        "id": "domain",
        "name": "行业背景与问题场景",
        "role": "确定发明要解决的行业问题、使用场景和约束边界。",
        "keywords": ["背景", "场景", "行业", "问题", "痛点", "铝电解", "电解槽", "生产", "管理"],
    },
    {
        "id": "technical",
        "name": "技术方案与算法流程",
        "role": "提供方法步骤、系统结构、模型流程和可落地的技术路线。",
        "keywords": ["方法", "系统", "模型", "算法", "流程", "控制", "预测", "检测", "优化", "数字孪生"],
    },
    {
        "id": "data",
        "name": "数据指标与状态变量",
        "role": "提取输入数据、关键指标、状态变量、参数和可计算特征。",
        "keywords": ["数据", "指标", "变量", "参数", "特征", "温度", "电流", "电压", "浓度", "效率"],
    },
    {
        "id": "evidence",
        "name": "实施例与效果证据",
        "role": "支撑实施方式、有益效果和可验证的性能或工程收益。",
        "keywords": ["实施例", "实验", "结果", "效果", "准确率", "效率", "能耗", "降低", "提升", "%"],
    },
    {
        "id": "prior_art",
        "name": "外部专利与避重材料",
        "role": "识别相近方案，帮助候选 idea 避开已有专利或公开技术。",
        "keywords": ["专利", "权利要求", "公开号", "Google Patents", "CN", "prior art", "patent", "claim"],
    },
]

INNOVATION_LEVELS = {
    "low": {"label": "低", "scope": 0.30, "index": 30},
    "medium": {"label": "中", "scope": 0.70, "index": 70},
    "high": {"label": "高", "scope": 1.00, "index": 100},
}


def normalize_innovation_level(value: Any) -> str:
    """Normalize old numeric values and new three-level values."""
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"low", "conservative", "保守", "低"}:
            return "low"
        if lowered in {"high", "bold", "aggressive", "大胆", "高"}:
            return "high"
        if lowered in {"medium", "balanced", "平衡", "中"}:
            return "medium"
        try:
            value = int(lowered)
        except ValueError:
            return "medium"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "medium"
    if number <= 35:
        return "low"
    if number >= 85:
        return "high"
    return "medium"


def innovation_index_for_level(level: str) -> int:
    return int(INNOVATION_LEVELS.get(normalize_innovation_level(level), INNOVATION_LEVELS["medium"])["index"])


def innovation_level_label(level: str) -> str:
    return str(INNOVATION_LEVELS.get(normalize_innovation_level(level), INNOVATION_LEVELS["medium"])["label"])


def build_material_strategy(
    documents: list[dict[str, Any]],
    external: ExternalSearchResult | None,
    candidates: list[PatentCandidate] | None = None,
    knowledge_graph: dict[str, Any] | None = None,
    innovation_index: int = 50,
    innovation_level: str | None = None,
) -> dict[str, Any]:
    """Build a readable material layering and combination map."""
    innovation_level = normalize_innovation_level(innovation_level if innovation_level is not None else innovation_index)
    innovation_index = innovation_index_for_level(innovation_level)
    layers = [_build_layer(definition, documents, external) for definition in LAYER_DEFINITIONS]
    non_empty_layers = [layer for layer in layers if layer["knowledge_count"] or layer["external_count"]]
    graph_fusion = _select_graph_fusion(knowledge_graph, innovation_level=innovation_level)
    candidate_paths = [
        _candidate_path(
            candidate,
            index,
            non_empty_layers or layers,
            _select_graph_fusion(
                knowledge_graph,
                innovation_level=innovation_level,
                focus_text=f"{candidate.title}\n{candidate.summary}\n{candidate.raw}",
                pair_offset=index - 1,
            ),
        )
        for index, candidate in enumerate(candidates or [], start=1)
    ]
    graph_signal = _graph_signal(knowledge_graph)
    return {
        "summary": _strategy_summary(non_empty_layers or layers, documents, external),
        "layers": layers,
        "fusion_overview": _fusion_overview(non_empty_layers or layers, documents, external, graph_signal),
        "fusion_blueprint": _fusion_blueprint(non_empty_layers or layers, graph_signal),
        "graph_signal": graph_signal,
        "graph_fusion": graph_fusion,
        "innovation_index": innovation_index,
        "innovation_level": innovation_level,
        "innovation_level_label": innovation_level_label(innovation_level),
        "combination_rules": [
            "先用行业背景层确定问题场景，避免把无关领域材料强行合并。",
            "再用技术方案层和数据指标层抽取可写成方法、系统或模型的技术特征。",
            "实施例与效果证据层只用于支撑可验证效果，不把没有证据的数据写成确定提升幅度。",
            "外部专利与避重材料层用于对照已有公开方案，候选 idea 必须体现差异化切入点。",
        ],
        "candidate_paths": candidate_paths,
        "external_usage_boundary": "外部检索结果只作为背景补充、已有技术识别、不可复用特征提取和避重约束，不作为候选专利的核心创新来源。",
    }


def _build_layer(
    definition: dict[str, Any],
    documents: list[dict[str, Any]],
    external: ExternalSearchResult | None,
) -> dict[str, Any]:
    knowledge_items = []
    for doc in documents:
        text = _item_text(doc, "file_path", "filename", "content_summary")
        if _matches(text, definition["keywords"]):
            knowledge_items.append(_document_item(doc))

    external_items = []
    for result in (external.results if external else []) or []:
        text = _item_text(result, "title", "snippet", "url")
        if _matches(text, definition["keywords"]):
            external_items.append(_external_item(result))

    return {
        "id": definition["id"],
        "name": definition["name"],
        "role": definition["role"],
        "knowledge_count": len(knowledge_items),
        "external_count": len(external_items),
        "knowledge_items": knowledge_items[:6],
        "external_items": external_items[:6],
    }


def _candidate_path(
    candidate: PatentCandidate,
    index: int,
    layers: list[dict[str, Any]],
    graph_fusion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = f"{candidate.title}\n{candidate.summary}\n{candidate.raw}"
    matched = []
    for layer in layers:
        layer_text = " ".join(
            [
                layer.get("name", ""),
                layer.get("role", ""),
                " ".join(item.get("title", "") for item in layer.get("knowledge_items", [])),
                " ".join(item.get("title", "") for item in layer.get("external_items", [])),
            ]
        )
        if _keyword_overlap(text, layer_text) or layer["id"] in {"domain", "technical", "prior_art"}:
            matched.append(
                {
                    "id": layer["id"],
                    "name": layer["name"],
                    "role": layer["role"],
                    "knowledge": layer.get("knowledge_items", [])[:3],
                    "external": layer.get("external_items", [])[:3],
                }
            )
    return {
        "candidate_index": index,
        "title": candidate.title,
        "source_layers": matched[:4],
        "idea_rationale": _candidate_rationale(candidate, matched, graph_fusion),
        "fusion_steps": _candidate_fusion_steps(candidate, matched),
        "contribution_matrix": _candidate_contribution_matrix(candidate, matched),
        "risk_controls": _candidate_risk_controls(candidate),
        "non_reuse_boundary": _field(candidate.raw, "未复用已有技术特征") or "外部检索结果只作为避让边界，不直接复用已有专利的核心特征组合。",
        "used_graph_nodes": (graph_fusion or {}).get("selected_nodes", []),
        "used_graph_edges": (graph_fusion or {}).get("selected_edges", []),
        "bridge_graph_nodes": (graph_fusion or {}).get("bridge_nodes", []),
        "primary_graph_nodes": (graph_fusion or {}).get("primary_pair", []),
        "innovation_index": (graph_fusion or {}).get("innovation_index"),
        "innovation_level": (graph_fusion or {}).get("innovation_level"),
        "innovation_level_label": (graph_fusion or {}).get("innovation_level_label"),
        "node_fusion_policy": (graph_fusion or {}).get("policy", ""),
        "node_fusion_reason": (graph_fusion or {}).get("reason", ""),
        "node_path_distance": (graph_fusion or {}).get("distance"),
        "intermediate_node_count": (graph_fusion or {}).get("intermediate_node_count", 0),
        "path_nodes": (graph_fusion or {}).get("path_nodes", []),
        "path_edges": (graph_fusion or {}).get("path_edges", []),
    }


def _candidate_rationale(
    candidate: PatentCandidate,
    layers: list[dict[str, Any]],
    graph_fusion: dict[str, Any] | None = None,
) -> str:
    core = _field(candidate.raw, "核心方案") or candidate.summary
    innovation = _field(candidate.raw, "创新点") or _field(candidate.raw, "新技术特征")
    effect = _field(candidate.raw, "技术效果来源") or _field(candidate.raw, "技术效果")
    avoidance = _field(candidate.raw, "避让现有技术") or _field(candidate.raw, "未复用已有技术特征")
    node_names = _graph_node_names(graph_fusion)
    layer_names = [layer["name"] for layer in layers[:4] if layer.get("name")]

    parts = [f"“{candidate.title}”的组合起点是"]
    if node_names:
        parts.append(f"图谱节点 {node_names}")
    elif layer_names:
        parts.append("、".join(layer_names[:3]))
    else:
        parts.append("当前知识图谱证据包")
    if core:
        parts.append(f"；问题/方案主线为：{_sentence_fragment(core, 120)}")
    if innovation:
        parts.append(f"；差异化技术特征为：{_sentence_fragment(innovation, 120)}")
    if effect:
        parts.append(f"；效果支撑来自：{_sentence_fragment(effect, 100)}")
    if avoidance:
        parts.append(f"；外部检索只用于避让：{_sentence_fragment(avoidance, 100)}")
    else:
        parts.append("；外部检索仅用于已有技术识别和避让，不作为核心创新来源")
    return "".join(parts) + "。"


def _fusion_overview(
    layers: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    external: ExternalSearchResult | None,
    graph_signal: dict[str, Any],
) -> dict[str, Any]:
    active_layers = [layer["name"] for layer in layers if layer["knowledge_count"] or layer["external_count"]]
    return {
        "title": "多素材融合说明",
        "summary": (
            "系统先把知识库素材按用途分层，再用知识图谱关系确认材料之间是否存在技术对象、"
            "问题场景、数据变量或方法流程上的连接；外部检索只作为已有技术边界和避让依据。"
        ),
        "document_count": len(documents),
        "external_count": len((external.results if external else []) or []),
        "active_layers": active_layers,
        "graph_basis": graph_signal.get("summary") or "未读取到可展示的图谱摘要。",
        "anti_random_mix_rule": "只有当材料能共同回答“解决什么问题、用什么技术链路、输入哪些数据、产生什么效果、如何避开已有技术”时，才被放入同一个候选 idea。",
    }


def _fusion_blueprint(layers: list[dict[str, Any]], graph_signal: dict[str, Any]) -> list[dict[str, Any]]:
    layer_names = {layer["id"]: layer["name"] for layer in layers}
    return [
        {
            "stage": "1. 问题锚定",
            "uses": [layer_names.get("domain", "行业背景与问题场景"), "知识图谱核心实体"],
            "logic": "先确认不同文档是否指向同一类行业对象、业务痛点或运行约束，避免把无关领域材料直接拼接。",
            "output": "形成候选专利要解决的具体问题边界。",
        },
        {
            "stage": "2. 技术链路抽取",
            "uses": [layer_names.get("technical", "技术方案与算法流程"), layer_names.get("data", "数据指标与状态变量")],
            "logic": "从自有材料中抽取方法步骤、模型流程、状态变量和指标关系，组合成可落地的技术方案主线。",
            "output": "形成候选 idea 的核心技术特征。",
        },
        {
            "stage": "3. 证据支撑校验",
            "uses": [layer_names.get("evidence", "实施例与效果证据")],
            "logic": "只把材料中已有的实施例、实验线索或工程效果作为支撑，不凭空创造量化效果。",
            "output": "确定哪些效果可以写实，哪些只能保守表述。",
        },
        {
            "stage": "4. 外部避让",
            "uses": [layer_names.get("prior_art", "外部专利与避重材料")],
            "logic": "外部检索结果用于识别不可复用的已有技术组合，并反向约束新方案必须体现差异化技术特征。",
            "output": "形成重合风险提示和技术避让边界。",
        },
        {
            "stage": "5. 创新点成型",
            "uses": graph_signal.get("representative_nodes") or ["知识库实体关系"],
            "logic": "把问题、技术链路、数据变量和避让边界收束为候选专利的独立创新点，而不是简单罗列素材。",
            "output": "生成可供用户选择的候选专利方向。",
        },
    ]


def _candidate_fusion_steps(candidate: PatentCandidate, layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    core = _field(candidate.raw, "核心方案") or candidate.summary
    innovation = _field(candidate.raw, "创新点") or _field(candidate.raw, "新技术特征")
    avoidance = _field(candidate.raw, "避让现有技术") or _field(candidate.raw, "重合风险")
    effect = _field(candidate.raw, "技术效果来源") or _field(candidate.raw, "技术效果")
    return [
        {
            "name": "问题来源",
            "evidence": _layer_titles(layers, "domain", fallback_count=2),
            "explanation": _short(core, 180) or "从行业背景层识别候选方案要解决的具体场景问题。",
        },
        {
            "name": "自有技术组合",
            "evidence": _layer_titles(layers, "technical", fallback_count=2) + _layer_titles(layers, "data", fallback_count=2),
            "explanation": _short(innovation, 220) or "从技术方案层和数据指标层组合可写成方法步骤的核心特征。",
        },
        {
            "name": "效果支撑",
            "evidence": _layer_titles(layers, "evidence", fallback_count=2),
            "explanation": _short(effect, 180) or "仅引用材料中可支撑的工程效果；缺少量化证据时保守表述。",
        },
        {
            "name": "避让边界",
            "evidence": _layer_external_titles(layers, "prior_art", fallback_count=3),
            "explanation": _short(avoidance, 220) or "用外部检索识别已有技术，不把外部方案作为本候选的核心创新来源。",
        },
    ]


def _candidate_contribution_matrix(candidate: PatentCandidate, layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    layer_by_id = {layer["id"]: layer for layer in layers}
    contributions = [
        ("domain", "定义问题场景", "决定该 idea 针对哪个行业对象和痛点。"),
        ("technical", "提供技术主线", "贡献方法步骤、模型流程或系统模块。"),
        ("data", "提供输入变量", "贡献状态变量、指标、参数或特征构造。"),
        ("evidence", "约束效果表述", "支撑实施方式和有益效果，避免无依据夸大。"),
        ("prior_art", "提供避让约束", "识别已有技术组合和需要人工确认的重合风险。"),
    ]
    rows = []
    for layer_id, role, meaning in contributions:
        layer = layer_by_id.get(layer_id)
        if not layer:
            continue
        knowledge = layer.get("knowledge_items", [])[:2]
        external = layer.get("external_items", [])[:2]
        if not knowledge and not external:
            continue
        rows.append(
            {
                "layer": layer.get("name", layer_id),
                "role": role,
                "meaning": meaning,
                "sources": [item.get("title", "") for item in knowledge + external if item.get("title")],
            }
        )
    if not rows:
        rows.append(
            {
                "layer": "知识库证据包",
                "role": "综合支撑",
                "meaning": f"围绕“{candidate.title}”组织已有材料中的问题、方法、数据和避让信息。",
                "sources": [],
            }
        )
    return rows


def _candidate_risk_controls(candidate: PatentCandidate) -> list[str]:
    risk = _field(candidate.raw, "重合风险")
    confirm = _field(candidate.raw, "人工确认点")
    controls = []
    if risk:
        controls.append(f"重合风险：{_short(risk, 180)}")
    if confirm:
        controls.append(f"人工确认：{_short(confirm, 180)}")
    controls.append("外部检索结果不得作为核心创新来源，只用于背景、已有技术识别和技术避让。")
    return controls


def _strategy_summary(
    layers: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    external: ExternalSearchResult | None,
) -> str:
    active = [layer["name"] for layer in layers if layer["knowledge_count"] or layer["external_count"]]
    return (
        f"本次读取 {len(documents)} 份知识库材料、"
        f"{len((external.results if external else []) or [])} 条外部检索结果；"
        f"共形成 {len(active)} 类有效证据，可用于解释候选 idea 的来源。"
    )


def _document_item(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(doc.get("file_path") or doc.get("filename") or "未知文档"),
        "id": str(doc.get("id") or doc.get("doc_id") or doc.get("document_id") or ""),
        "chunks_count": int(doc.get("chunks_count") or 0),
        "snippet": _clean(doc.get("content_summary") or ""),
    }


def _external_item(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(result.get("title") or "外部检索结果"),
        "url": str(result.get("url") or ""),
        "snippet": _clean(result.get("snippet") or ""),
    }


def _item_text(item: dict[str, Any], *keys: str) -> str:
    return " ".join(str(item.get(key) or "") for key in keys)


def _matches(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _keyword_overlap(left: str, right: str) -> bool:
    seeds = [token for token in ("铝电解", "电解槽", "数字孪生", "预测", "控制", "效率", "专利", "系统", "方法") if token in left]
    return any(seed in right for seed in seeds)


def _clean(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _short(value: Any, limit: int) -> str:
    return _clean(value, limit=limit)


def _sentence_fragment(value: Any, limit: int) -> str:
    return _short(value, limit).rstrip("。；;，,、 ")


def _field(raw: str, label: str) -> str:
    text = str(raw or "")
    pattern = rf"(?:\*\*)?{label}(?:：|\:)(?:\*\*)?\s*([\s\S]*?)(?=\n\s*(?:\*\*)?[\u4e00-\u9fa5A-Za-z0-9 /（）()]+(?:：|\:)(?:\*\*)?|\n---|\Z)"
    match = re.search(pattern, text)
    return _clean(match.group(1), limit=360) if match else ""


def _graph_node_names(graph_fusion: dict[str, Any] | None, limit: int = 4) -> str:
    if not isinstance(graph_fusion, dict):
        return ""
    nodes = graph_fusion.get("selected_nodes") or graph_fusion.get("primary_pair") or []
    names = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = str(node.get("full_label") or node.get("label") or node.get("id") or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return "、".join(names)


def _layer_titles(layers: list[dict[str, Any]], layer_id: str, fallback_count: int = 2) -> list[str]:
    for layer in layers:
        if layer.get("id") == layer_id:
            titles = [item.get("title", "") for item in layer.get("knowledge_items", [])[:fallback_count]]
            return [title for title in titles if title]
    return []


def _layer_external_titles(layers: list[dict[str, Any]], layer_id: str, fallback_count: int = 2) -> list[str]:
    for layer in layers:
        if layer.get("id") == layer_id:
            titles = [item.get("title", "") for item in layer.get("external_items", [])[:fallback_count]]
            return [title for title in titles if title]
    return []


def _graph_signal(graph: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(graph, dict):
        return {"summary": "", "representative_nodes": [], "representative_edges": []}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    representative_nodes = [
        str(node.get("full_label") or node.get("label") or node.get("id"))
        for node in nodes
        if isinstance(node, dict) and node.get("type") != "document"
    ][:8]
    representative_edges = [
        {
            "source": edge.get("source_label") or edge.get("source"),
            "relation": edge.get("label") or "关联",
            "target": edge.get("target_label") or edge.get("target"),
        }
        for edge in edges
        if isinstance(edge, dict)
    ][:8]
    return {
        "summary": str(graph.get("summary") or ""),
        "source": str(graph.get("source") or ""),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "representative_nodes": representative_nodes,
        "representative_edges": representative_edges,
    }


def _select_graph_fusion(
    graph: dict[str, Any] | None,
    innovation_index: int | None = None,
    focus_text: str = "",
    pair_offset: int = 0,
    innovation_level: str | None = None,
) -> dict[str, Any]:
    innovation_level = normalize_innovation_level(innovation_level if innovation_level is not None else innovation_index)
    innovation_index = innovation_index_for_level(innovation_level)
    if not isinstance(graph, dict):
        return _empty_graph_fusion(innovation_level, "暂无可用知识图谱，无法选择融合节点。")

    raw_nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict) and node.get("id")]
    raw_edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict) and edge.get("source") and edge.get("target")]
    usable_nodes = [
        node for node in raw_nodes
        if node.get("type") not in {"document", "root"} and not node.get("is_document")
    ]
    if len(usable_nodes) < 2:
        usable_nodes = [node for node in raw_nodes if node.get("type") != "root"]
    if len(usable_nodes) < 2:
        return _empty_graph_fusion(innovation_level, "知识图谱节点不足，无法形成节点融合对。")

    node_by_id = {str(node.get("id")): node for node in raw_nodes}
    adjacency: dict[str, set[str]] = {str(node.get("id")): set() for node in raw_nodes}
    for edge in raw_edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)

    raw_pairs: list[tuple[dict[str, Any], dict[str, Any], int, int, int, int, list[str]]] = []
    disconnected_pairs: list[tuple[dict[str, Any], dict[str, Any], int, int, int, int, list[str]]] = []
    for index, left in enumerate(usable_nodes):
        for right in usable_nodes[index + 1:]:
            left_id = str(left.get("id"))
            right_id = str(right.get("id"))
            path = _shortest_path(left_id, right_id, adjacency)
            distance = max(0, len(path) - 1) if path else None
            type_bonus = 1 if _node_type(left) != _node_type(right) else 0
            degree_score = int(left.get("degree") or 0) + int(right.get("degree") or 0)
            focus_score = _node_focus_score(left, focus_text) + _node_focus_score(right, focus_text)
            if distance is None:
                disconnected_pairs.append((left, right, 999, type_bonus, degree_score, focus_score, [left_id, right_id]))
            else:
                raw_pairs.append((left, right, distance, type_bonus, degree_score, focus_score, path))

    if not raw_pairs:
        raw_pairs = disconnected_pairs

    max_distance = max((item[2] for item in raw_pairs), default=1)
    scope = float(INNOVATION_LEVELS[innovation_level]["scope"])
    max_allowed_distance = max(1, math.ceil(max_distance * scope))
    eligible_pairs = raw_pairs if innovation_level == "high" else [
        item for item in raw_pairs if item[2] <= max_allowed_distance
    ]
    if not eligible_pairs:
        eligible_pairs = raw_pairs

    scored_pairs: list[tuple[float, dict[str, Any], dict[str, Any], int]] = []
    for left, right, distance, type_bonus, degree_score, focus_score, path in eligible_pairs:
        if innovation_level == "high":
            score = min(distance, 12) * 6 + type_bonus * 4 + degree_score * 0.25 + focus_score * 2.2
        elif innovation_level == "low":
            score = (max_allowed_distance + 1 - min(distance, max_allowed_distance)) * 8 + degree_score * 0.4 + focus_score * 3.5
        else:
            target_distance = max(1, math.ceil(max_allowed_distance * 0.55))
            score = (max_allowed_distance + 1 - abs(distance - target_distance)) * 4 + type_bonus * 3 + degree_score * 0.35 + focus_score * 2.8
        scored_pairs.append((score, left, right, distance, path))

    scored_pairs.sort(key=lambda item: item[0], reverse=True)
    selected_pair = scored_pairs[pair_offset % min(len(scored_pairs), 12)] if scored_pairs else (0, usable_nodes[0], usable_nodes[1], 1, [str(usable_nodes[0].get("id")), str(usable_nodes[1].get("id"))])
    _, left, right, distance, path_ids = selected_pair
    selected_ids = set(path_ids)

    path_edges = _path_edges(path_ids, raw_edges, node_by_id)
    selected_edges = path_edges[:16]
    path_nodes = [_node_public(node_by_id[node_id]) for node_id in path_ids if node_id in node_by_id]
    primary_nodes = [_node_public(left), _node_public(right)]
    primary_ids = {str(left.get("id")), str(right.get("id"))}
    bridge_nodes = [node for node in path_nodes if node.get("id") not in primary_ids]
    if not selected_edges and len(path_nodes) >= 2:
        selected_edges = [
            {
                "source": path_nodes[0]["id"],
                "target": path_nodes[1]["id"],
                "source_label": path_nodes[0]["full_label"] or path_nodes[0]["label"],
                "target_label": path_nodes[1]["full_label"] or path_nodes[1]["label"],
                "label": "组合参考",
                "relation_kind": "inferred",
            }
        ]
        path_edges = selected_edges

    display_distance = None if distance >= 999 else distance
    return {
        "innovation_index": innovation_index,
        "innovation_level": innovation_level,
        "innovation_level_label": innovation_level_label(innovation_level),
        "policy": f"当前仅在知识图谱可探索范围内选择节点；档位：{innovation_level_label(innovation_level)}。",
        "distance": display_distance,
        "max_graph_distance": None if max_distance >= 999 else max_distance,
        "allowed_distance": max_allowed_distance,
        "intermediate_node_count": max(0, len(path_ids) - 2),
        "primary_pair": primary_nodes,
        "selected_nodes": primary_nodes,
        "selected_edges": selected_edges,
        "bridge_nodes": bridge_nodes,
        "path_nodes": path_nodes,
        "path_edges": path_edges,
        "node_pairs": [
            {
                "left": _node_public(left),
                "right": _node_public(right),
                "distance": display_distance,
                "path_node_count": len(path_ids),
                "intermediate_node_count": max(0, len(path_ids) - 2),
                "reason": _pair_reason(left, right, display_distance, innovation_level),
            }
        ],
        "reason": _pair_reason(left, right, display_distance, innovation_level),
    }


def _node_focus_score(node: dict[str, Any], focus_text: str) -> int:
    if not focus_text:
        return 0
    focus = focus_text.lower()
    label = str(node.get("full_label") or node.get("label") or node.get("id") or "").lower()
    summary = str(node.get("summary") or "").lower()
    tokens = [token for token in re.split(r"[\s_\-/()（）,，:：;；]+", label) if len(token) >= 2]
    score = 0
    if label and label in focus:
        score += 8
    score += sum(3 for token in tokens if token in focus)
    for keyword in ("预测", "控制", "检测", "效率", "数据", "模型", "系统", "方法", "电解", "阳极", "温度", "质量"):
        if keyword in summary and keyword in focus:
            score += 1
    return score


def _empty_graph_fusion(innovation_level: str, reason: str) -> dict[str, Any]:
    innovation_level = normalize_innovation_level(innovation_level)
    return {
        "innovation_index": innovation_index_for_level(innovation_level),
        "innovation_level": innovation_level,
        "innovation_level_label": innovation_level_label(innovation_level),
        "policy": "图谱节点不足时采用材料分层融合。",
        "distance": None,
        "intermediate_node_count": 0,
        "primary_pair": [],
        "selected_nodes": [],
        "selected_edges": [],
        "path_nodes": [],
        "path_edges": [],
        "node_pairs": [],
        "reason": reason,
    }


def _node_public(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(node.get("id") or ""),
        "label": str(node.get("label") or node.get("full_label") or node.get("id") or ""),
        "full_label": str(node.get("full_label") or node.get("label") or node.get("id") or ""),
        "type": _node_type(node),
        "native_type": str(node.get("native_type") or ""),
        "summary": str(node.get("summary") or ""),
        "file_path": str(node.get("file_path") or ""),
        "source_id": str(node.get("source_id") or ""),
        "degree": int(node.get("degree") or 0),
        "is_focus": bool(node.get("is_focus")),
    }


def _edge_public(edge: dict[str, Any], node_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source = str(edge.get("source") or "")
    target = str(edge.get("target") or "")
    return {
        "source": source,
        "target": target,
        "source_label": str(edge.get("source_label") or node_by_id.get(source, {}).get("label") or source),
        "target_label": str(edge.get("target_label") or node_by_id.get(target, {}).get("label") or target),
        "label": str(edge.get("label") or "关联"),
    }


def _path_edges(
    path_ids: list[str],
    raw_edges: list[dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(path_ids) < 2:
        return []
    edges: list[dict[str, Any]] = []
    for source_id, target_id in zip(path_ids, path_ids[1:]):
        match = next(
            (
                edge
                for edge in raw_edges
                if {str(edge.get("source")), str(edge.get("target"))} == {source_id, target_id}
            ),
            None,
        )
        if match:
            edges.append(_edge_public(match, node_by_id))
        else:
            source = node_by_id.get(source_id, {})
            target = node_by_id.get(target_id, {})
            edges.append(
                {
                    "source": source_id,
                    "target": target_id,
                    "source_label": str(source.get("label") or source_id),
                    "target_label": str(target.get("label") or target_id),
                    "label": "路径连接",
                }
            )
    return edges


def _node_type(node: dict[str, Any]) -> str:
    return str(node.get("type") or node.get("native_type") or "concept")


def _pair_reason(left: dict[str, Any], right: dict[str, Any], distance: int | None, innovation_level: str) -> str:
    left_label = left.get("full_label") or left.get("label") or left.get("id")
    right_label = right.get("full_label") or right.get("label") or right.get("id")
    distance_text = f"图谱距离约为 {distance}" if distance is not None else "图谱距离较远或跨分支"
    return f"{left_label} 与 {right_label} 被选为该候选的图谱组合起点，{distance_text}。"


def _graph_distance(start: str, end: str, adjacency: dict[str, set[str]]) -> int | None:
    path = _shortest_path(start, end, adjacency)
    if not path:
        return None
    return max(0, len(path) - 1)


def _shortest_path(start: str, end: str, adjacency: dict[str, set[str]]) -> list[str]:
    if start == end:
        return [start]
    queue: list[list[str]] = [[start]]
    seen = {start}
    while queue:
        path = queue.pop(0)
        node = path[-1]
        if len(path) > 7:
            continue
        for next_node in adjacency.get(node, set()):
            if next_node in seen:
                continue
            next_path = [*path, next_node]
            if next_node == end:
                return next_path
            seen.add(next_node)
            queue.append(next_path)
    return []


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))
