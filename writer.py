"""Template-based writers for the patent agent workflow."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from external_search import ExternalSearchResult
from models import KnowledgeBundle, TaskPlan


def build_markdown(
    question: str,
    knowledge_result: Any,
    retrieval_context: Any | None = None,
) -> str:
    """Generate a Markdown draft from a user question and LightRAG response."""
    answer = _extract_answer(knowledge_result)
    references = _extract_references(knowledge_result)
    diagnosis = _build_diagnosis(answer, references, retrieval_context)
    raw_json = json.dumps(knowledge_result, ensure_ascii=False, indent=2)
    context_json = (
        json.dumps(retrieval_context, ensure_ascii=False, indent=2)
        if retrieval_context is not None
        else ""
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    references_section = _format_references(references)
    context_section = ""
    if retrieval_context is not None:
        context_section = f"""
## 原始检索上下文

```json
{context_json}
```
"""

    return f"""# 知识库回答草稿

## 用户问题 / 任务

{question}

## 基于知识库的回答

{answer}

{diagnosis}

## 参考来源

{references_section}

## 原始 API 返回

```json
{raw_json}
```
{context_section}

---

生成时间：{generated_at}
"""


def _extract_answer(result: Any) -> str:
    """Best-effort extraction without assuming one fixed LightRAG schema."""
    if isinstance(result, str):
        return result.strip() or "知识库返回了空字符串。"

    if isinstance(result, dict):
        for key in ("response", "answer", "result", "content", "message"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return "未找到明确回答字段，请查看下方原始 API 返回并根据实际字段调整 writer.py。"

    return "API 返回的数据结构暂未适配，请查看下方原始 API 返回。"


def _extract_references(result: Any) -> list[Any]:
    if not isinstance(result, dict):
        return []

    references = result.get("references") or result.get("sources") or result.get("docs")
    if isinstance(references, list):
        return references

    return []


def _format_references(references: list[Any]) -> str:
    if not references:
        return "暂无 references。"

    lines = []
    for index, item in enumerate(references, start=1):
        if isinstance(item, dict):
            title = item.get("title") or item.get("file_path") or item.get("source") or str(item)
            lines.append(f"{index}. {title}")
        else:
            lines.append(f"{index}. {item}")

    return "\n".join(lines)


def _build_diagnosis(
    answer: str,
    references: list[Any],
    retrieval_context: Any | None,
) -> str:
    no_context = "[no-context]" in answer or "no context" in answer.lower()
    if not no_context and references:
        return ""

    hints = []
    if no_context:
        hints.append("LightRAG 返回了 no-context，说明这次查询没有检索到可用知识库片段。")
    if not references:
        hints.append("API 返回的 references 为空，当前草稿缺少可引用来源。")
    if retrieval_context in (None, "", [], {}):
        hints.append("可以运行带检索上下文的调试命令，确认 `/query/data` 是否能返回原始上下文。")

    if not hints:
        return ""

    bullet_text = "\n".join(f"- {hint}" for hint in hints)
    return f"""## 检索诊断

{bullet_text}
"""


def build_patent_markdown(
    task: str,
    plan: TaskPlan,
    knowledge: KnowledgeBundle,
    external_search: ExternalSearchResult,
    include_raw: bool = False,
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    material = _compose_material(task, knowledge, external_search)
    references = _collect_reference_lines(knowledge)
    external_notes = _format_external_search(external_search)

    if plan.task_type == "topic_analysis":
        body = _build_topic_analysis(task, material, references, external_notes)
    elif plan.task_type == "figures":
        body = _build_figures(task, material, references, external_notes)
    else:
        body = _build_technical_disclosure(task, material, references, external_notes)

    raw = _format_raw_materials(knowledge) if include_raw else ""
    return f"""{body}

---

## Workflow 追溯

| 环节 | 结果 |
|------|------|
| 任务输入 | {task} |
| Skill 判断 | {plan.intent} |
| 知识库 API | 查询 {len(knowledge.query_results)} 次，错误 {len(knowledge.errors)} 个 |
| 外部资料 | {'已启用' if external_search.enabled else '未启用'} |
| 对标文件 | patent-writing skill + 示例 Markdown 章节结构 |

生成时间：{generated_at}

{raw}
"""


def _build_topic_analysis(
    task: str,
    material: str,
    references: str,
    external_notes: str,
) -> str:
    return f"""# 专利选题分析

## 任务理解

本次任务：{task}

目标是基于项目材料和知识库素材，形成可继续扩展为技术交底书的专利选题分析。

## 项目基础素材

{material}

## 行业检索结论

| 已有方向 | 核心技术 | 代表专利/论文 | 与本项目关系 |
|---------|---------|---------------|--------------|
| 待补充 | 待基于正式检索补充 | 待补充 | 用于判断新颖性边界 |

{external_notes}

正式检索提醒：请在国家知识产权局专利检索及分析系统进行人工检索，记录检索日期、检索式、代表专利号、相近权利要求和说明书关键段落。

## 创新点候选

| 序号 | 创新点 | 跨行业来源 | 可结合的项目基础 | 数据分析强度 | 实施难度 | 专利壁垒 |
|------|--------|------------|------------------|--------------|----------|----------|
| 1 | 基于知识库素材提炼核心方法流程 | 项目知识库/RAG | {task} | 中 | 中 | 中 |
| 2 | 引入不确定性或可信度输出 | 气象预报/风险控制 | 预测或分析结果 | 高 | 中 | 中高 |
| 3 | 引入漂移检测与自适应更新 | 数据流监控 | 长期运行数据 | 高 | 中高 | 高 |

## 可专利性评估

| 要件 | 初步判断 | 后续需要补充 |
|------|----------|--------------|
| 新颖性 | 需要正式检索确认 | 相同/相近专利清单 |
| 创造性 | 需要证明技术联动效果 | 对比现有方案的差异和实验效果 |
| 实用性 | 需要项目数据支撑 | 数据来源、指标、部署场景 |

## 推荐选题

建议优先选择“项目已有技术方案 + 1 个可解释或自适应增量创新”的路径，避免只把通用算法名称包装成专利点。

## 后续资料清单

- 项目原始说明书、方案文档、实验数据或截图
- 模型/算法输入输出字段
- 精度指标、消融实验或上线效果
- 已公开论文、汇报、合同中的知识产权条款

## 知识库参考来源

{references}
"""


def _build_technical_disclosure(
    task: str,
    material: str,
    references: str,
    external_notes: str,
) -> str:
    invention_name = _infer_invention_name(task)
    return f"""# {invention_name}

## 标题页

| 项目 | 内容 |
|------|------|
| 文档标题 | {invention_name} |
| 文档类型 | 发明专利技术方案文档 |
| 申请人/单位 | 待补充 |
| 发明名称 | {invention_name} |
| 发明人 | 待补充 |
| 联系电话 | 待补充 |
| E-mail | 待补充 |

## 一、发明名称

{invention_name}

## 二、技术领域

本发明涉及目标行业场景中的数据分析、智能决策或优化控制技术方向，尤其涉及一种基于项目素材构建的行业问题识别、状态建模、方案生成与闭环优化的方法及系统。

## 三、背景技术

### 3.1 行业问题

{material}

结合现有材料，本发明主要面向以下问题：

1. 业务场景中的关键对象、状态变化和影响因素难以通过单一规则稳定表达。
2. 现有分析或控制过程对多源数据、历史响应和约束条件的联动利用不足。
3. 方案生成后缺少与实际执行反馈相结合的校验、评估和持续优化机制。

## 四、发明内容

### 4.1 关键创新点

1. 将目标行业对象、状态数据、约束条件和反馈结果统一建模，形成可复用的技术处理链路。
2. 将候选方案生成、约束校验、综合评估和执行反馈连接为闭环流程。
3. 结合外部检索结果对相似技术方案进行避让分析，降低与现有专利重复的风险。

### 4.2 发明目的

本发明旨在提供一种面向具体行业问题的智能分析与方案生成方法及系统，使业务对象能够在多源数据、约束规则和反馈信息共同作用下完成更稳定的识别、决策和优化。

### 4.3 拟解决的技术问题

针对背景技术中的问题，本发明拟解决：

1. 如何对目标行业对象的状态变化和影响因素进行结构化表达。
2. 如何在多源数据、历史响应和业务约束之间建立可计算的关联关系。
3. 如何在方案生成后进行约束校验、效果评估和执行反馈更新。

### 4.4 总体技术方案

一种面向目标行业问题的智能分析与闭环优化方法，包括：

S1，获取目标对象相关的业务数据、运行状态数据、历史结果数据和约束条件数据；

S2，对所述数据进行清洗、时间对齐、异常处理和特征构建，形成描述目标对象状态和上下文的输入特征集合；

S3，基于输入特征集合构建候选方案生成模型或规则，使系统输出一个或多个候选处理方案；

S4，对候选方案进行业务约束、边界条件和执行可行性校验；

S5，按照准确性、稳定性、资源消耗、风险控制和反馈表现等指标对候选方案进行综合评价；

S6，输出最终方案并采集执行反馈，用于后续参数修正、模型更新或规则优化。

### 4.5 有益效果

在现有材料尚未提供明确实验数据的情况下，本节仅作定性说明：

1. 有助于减少人工经验对方案生成过程的影响，提高处理流程的一致性。
2. 有助于把业务约束和执行反馈纳入方案评估过程，降低方案不可执行或效果不稳定的风险。
3. 有助于形成从数据获取、方案生成、约束校验到反馈更新的闭环流程，便于后续持续优化。

## 五、保护范围

本发明拟保护的范围包括：

1. 一种面向目标行业问题的智能分析与闭环优化方法。
2. 一种执行上述方法的系统，包括数据采集、特征构建、方案生成、约束校验、方案评估和反馈更新模块。
3. 一种用于执行上述方法的装置、电子设备或计算机可读存储介质。
4. 上述方法中围绕对象建模、输入特征构建、约束校验、评分机制和闭环反馈形成的组合技术特征。

## 六、附图说明

图1 为本发明总体流程图。  
图2 为本发明系统架构图。  
图3 为本发明关键机制逻辑图。  
图4 为本发明系统模块组成图。

## 七、具体实施方式

在一个实施例中，系统以目标业务对象为处理对象，采集与该对象相关的历史数据、实时状态数据、业务约束数据和执行反馈数据。系统按照统一时间粒度对多源数据进行对齐，并构建反映对象状态、上下文条件和约束关系的输入变量。

系统根据输入变量生成候选处理方案，并对候选方案进行边界校验、约束检查和风险评估。当候选方案不满足约束条件时，系统对方案进行修正、降级或转入人工确认。方案执行后，系统记录执行结果和反馈信息，用于后续规则修正或模型更新。

外部资料状态：

{external_notes}

## 八、附图

待根据最终技术方案生成流程图、系统架构图、关键机制逻辑图和模块组成图。

## 知识库参考来源

{references}
"""


def _build_figures(
    task: str,
    material: str,
    references: str,
    external_notes: str,
) -> str:
    return f"""# 专利附图全集

## 附图清单

| 图号 | 图名 | 用途 |
|------|------|------|
| 图1 | 总体流程图 | 展示任务输入到最终文档输出的流程 |
| 图2 | 系统模块图 | 展示 skill、知识库、搜索、写作和对标模块 |
| 图3 | 数据处理流程图 | 展示知识库素材进入写作模板的处理过程 |

## 图1 总体流程图

```mermaid
flowchart TD
    A[任务输入] --> B[调用 skill 判断要干什么]
    B --> C[调用知识库 API 找素材]
    C --> D{是否需要外部资料}
    D -->|需要| E[搜索外部资料]
    D -->|不需要| F[根据撰写 skill 生成结果]
    E --> F
    F --> G[和结果对标文件比较]
    G --> H[输出最终文档]
```

## 图2 系统模块图

```mermaid
flowchart LR
    U[用户] --> R[任务路由模块]
    R --> K[LightRAG 检索模块]
    R --> W[专利写作模块]
    K --> W
    S[外部搜索模块] --> W
    W --> Q[对标检查模块]
    Q --> O[Markdown 输出]
```

## 图3 数据处理流程图

```mermaid
flowchart TD
    Q[检索问题] --> API[LightRAG /query]
    API --> A[回答]
    API --> Ref[References]
    A --> T[章节模板]
    Ref --> T
    T --> MD[最终 Markdown]
```

## 素材说明

{material}

## 外部资料说明

{external_notes}

## 知识库参考来源

{references}
"""


def _summarize_knowledge(knowledge: KnowledgeBundle) -> str:
    blocks = []
    for item in knowledge.query_results:
        question = item.get("question", "")
        answer = _extract_answer(item.get("data"))
        if _is_no_context_answer(answer):
            continue
        blocks.append(f"### 检索问题：{question}\n\n{answer}")

    return "\n\n".join(blocks)


def _compose_material(
    task: str,
    knowledge: KnowledgeBundle,
    external_search: ExternalSearchResult,
) -> str:
    knowledge_material = _summarize_knowledge(knowledge)
    if knowledge_material.strip():
        return knowledge_material

    if external_search.results:
        rows = []
        for index, item in enumerate(external_search.results, start=1):
            title = item.get("title", "").strip()
            snippet = item.get("snippet", "").strip()
            url = item.get("url", "").strip()
            rows.append(f"{index}. {title}\n   - 摘要：{snippet or '无摘要'}\n   - 链接：{url}")
        return f"""知识库未返回可用上下文，以下为外部搜索获得的初步素材，仅用于快速摸底，不能替代正式专利检索：

{chr(10).join(rows)}
"""

    return _build_fallback_material(task)


def _build_fallback_material(task: str) -> str:
    return f"""知识库和外部搜索均未返回可直接引用的素材。以下内容仅基于任务题目进行结构化展开，不能作为最终事实依据：

- 拟解决对象：{task}。
- 可考虑的技术场景：多个注汽井或井组在蒸汽资源、地层响应、产量目标和能耗约束下进行协同控制。
- 可考虑的核心方法：将每口井或每个井组抽象为智能体，利用多智能体强化学习学习协同注汽策略。
- 可考虑的输入数据：井口注汽参数、压力温度数据、生产动态、地质或油藏特征、历史调控记录。
- 可考虑的输出结果：各井注汽量、注汽时序、协同控制动作、收益/能耗/采收率综合优化指标。
- 后续必须补充：真实项目数据、控制约束、奖励函数、训练方式、实验指标、相近专利检索结果。
"""


def _collect_reference_lines(knowledge: KnowledgeBundle) -> str:
    lines = []
    for item in knowledge.query_results:
        refs = _extract_references(item.get("data"))
        for ref in refs:
            if isinstance(ref, dict):
                lines.append(ref.get("title") or ref.get("source") or ref.get("file_path") or str(ref))
            else:
                lines.append(str(ref))

    if not lines:
        return "暂无 references。"

    unique = list(dict.fromkeys(lines))
    return "\n".join(f"- {line}" for line in unique)


def _format_raw_materials(knowledge: KnowledgeBundle) -> str:
    data = {
        "query_results": knowledge.query_results,
        "query_data_results": knowledge.query_data_results,
        "errors": knowledge.errors,
    }
    raw_json = json.dumps(data, ensure_ascii=False, indent=2)
    return f"""<details>
<summary>原始知识库返回</summary>

```json
{raw_json}
```

</details>
"""


def _infer_invention_name(task: str) -> str:
    cleaned = task.strip().strip("。")
    if cleaned.startswith("一种"):
        return cleaned
    if "方法" in cleaned or "系统" in cleaned:
        return f"一种{cleaned}"
    return f"一种基于知识库和专利撰写技能的{cleaned}生成方法及系统"


def _is_no_context_answer(answer: str) -> bool:
    normalized = answer.lower()
    return "[no-context]" in normalized or "no context" in normalized


def _format_external_search(external_search: ExternalSearchResult) -> str:
    notes = "\n".join(f"- {item}" for item in external_search.notes)
    if not external_search.results:
        return notes

    rows = [
        "| 序号 | 标题 | 摘要 | 链接 |",
        "|------|------|------|------|",
    ]
    for index, item in enumerate(external_search.results, start=1):
        title = _escape_table_cell(item.get("title", ""))
        snippet = _escape_table_cell(item.get("snippet", ""))
        url = item.get("url", "")
        rows.append(f"| {index} | {title} | {snippet} | {url} |")

    return f"""{notes}

### 外部搜索结果

{chr(10).join(rows)}
"""


def _escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()
