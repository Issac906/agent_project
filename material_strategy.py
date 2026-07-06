"""Explain how knowledge-base and external materials are combined for ideas."""

from __future__ import annotations

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


def build_material_strategy(
    documents: list[dict[str, Any]],
    external: ExternalSearchResult | None,
    candidates: list[PatentCandidate] | None = None,
) -> dict[str, Any]:
    """Build a readable material layering and combination map."""
    layers = [_build_layer(definition, documents, external) for definition in LAYER_DEFINITIONS]
    non_empty_layers = [layer for layer in layers if layer["knowledge_count"] or layer["external_count"]]
    candidate_paths = [
        _candidate_path(candidate, index, non_empty_layers or layers)
        for index, candidate in enumerate(candidates or [], start=1)
    ]
    return {
        "summary": _strategy_summary(non_empty_layers or layers, documents, external),
        "layers": layers,
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
        "idea_rationale": _candidate_rationale(candidate, matched),
    }


def _candidate_rationale(candidate: PatentCandidate, layers: list[dict[str, Any]]) -> str:
    names = "、".join(layer["name"] for layer in layers[:3]) or "知识库材料和外部检索材料"
    return f"该候选将{names}组合为一个可申请方向：以知识库中的问题场景和技术/数据层提取核心特征，以外部检索层作为避让约束和风险检查，不把外部已有方案作为创新来源。"


def _strategy_summary(
    layers: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    external: ExternalSearchResult | None,
) -> str:
    active = [layer["name"] for layer in layers if layer["knowledge_count"] or layer["external_count"]]
    return (
        f"本次读取 {len(documents)} 份知识库材料、"
        f"{len((external.results if external else []) or [])} 条外部检索结果；"
        f"系统按 {len(active)} 个有效素材层组织证据，避免将不同领域文档无差别混合。"
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
