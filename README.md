# LightRAG 专利写作 Agent Workflow

这是一个基于 LightRAG 知识库 API、外部检索和 Ollama 的交互式专利发现与写作 agent。它参考 `patent-writing` skill，把流程拆成：

```text
读取知识库材料
↓
评估素材是否充分
↓
无论是否充分都进行外部专利相关搜索
↓
自动提出多个可能的专利方向
↓
生成相似专利差异分析表
↓
用户选择要继续的专利方向
↓
按章节逐步写作，每一步由用户确认、重写或手动编辑
↓
对标检查
↓
输出最终文档
```

当前版本不再提供前端页面。默认运行 `python main.py` 会进入命令行交互流程。正式使用前仍需要在国家知识产权局专利检索及分析系统做人工检索核对。

## 项目结构

```text
.
├── main.py                 # 命令行入口
├── patent_discovery_agent.py # 交互式专利发现与分步骤写作
├── workflow.py             # 串联完整 agent workflow
├── config.py               # 读取 .env 配置
├── skill_router.py         # 根据任务判断输出类型
├── lightrag_client.py      # LightRAG Server API 客户端
├── writer.py               # 专利文档 Markdown 生成
├── evaluator.py            # 和 skill/示例章节对标检查
├── external_search.py      # 外部网页搜索 fallback
├── llm_writer.py           # Ollama / LLM 最终写作
├── similar_patent_analysis.py # 相似专利差异分析 Excel/Markdown 生成
├── models.py               # 数据模型
├── requirements.txt
├── .env.example
└── outputs/
```

## 配置

复制配置模板：

```bash
cp .env.example .env
```

`.env` 示例：

```bash
LIGHTRAG_BASE_URL=http://192.168.130.130:9621/webui/#/
LIGHTRAG_API_KEY=
LIGHTRAG_QUERY_MODE=mix
LIGHTRAG_TOP_K=
LIGHTRAG_INCLUDE_CHUNK_CONTENT=true

LLM_PROVIDER=ollama
LLM_API_KEY=
LLM_BASE_URL=http://192.168.130.130:9621
LLM_MODEL=lightrag:latest

SEARCH_PROVIDER=duckduckgo
SEARCH_API_KEY=
```

### 1. LightRAG 知识库 API

- `LIGHTRAG_BASE_URL` 是 LightRAG Server 地址。即使填 Web UI 地址，代码也会自动规范化到 API root。
- `LIGHTRAG_API_KEY` 是 LightRAG Server API 的鉴权 key，不是 OpenAI key。
- 如果 LightRAG Server 没开鉴权，`LIGHTRAG_API_KEY` 留空即可。
- `LIGHTRAG_QUERY_MODE` 是知识库查询模式。你的 OpenAPI 显示可选值是 `local`、`global`、`hybrid`、`naive`、`mix`、`bypass`，默认建议先用 `mix`。
- `LIGHTRAG_TOP_K` 是可选检索数量参数。如果你的 Swagger 不支持，先留空。
- `LIGHTRAG_INCLUDE_CHUNK_CONTENT` 控制 references 是否尽量包含原始 chunk 内容，建议先保持 `true`。

### 2. LLM 写作模型 API

这些变量用于调用 LLM 生成最终稿。当前已支持 Ollama 原生 `/api/generate`。

- `LLM_PROVIDER`：当前填 `ollama`。
- `LLM_API_KEY`：Ollama 原生接口一般不需要 key，留空即可。
- `LLM_BASE_URL`：Ollama 服务根地址。你当前可填 `http://192.168.130.130:9621`，不要加 `/api/generate`。
- `LLM_MODEL`：具体模型名。你当前 `/api/tags` 返回 `lightrag:latest`。

### 3. 外部搜索 API

当前默认使用 `duckduckgo`，不需要 key，但稳定性一般。

- `SEARCH_PROVIDER`：搜索服务商，例如 `duckduckgo`、`tavily`、`serpapi`、`bing`。
- `SEARCH_API_KEY`：搜索服务商的 API key。DuckDuckGo 留空；Tavily/SerpAPI/Bing 才需要填。

### 你现在最少需要填什么

如果只测试知识库：

```bash
LIGHTRAG_BASE_URL=你的 LightRAG Web UI 或 API 地址
LIGHTRAG_API_KEY=如果 LightRAG 需要鉴权才填
```

如果要调用你导师提供的 Ollama 写作：

```bash
LLM_PROVIDER=ollama
LLM_API_KEY=
LLM_BASE_URL=http://192.168.130.130:9621
LLM_MODEL=lightrag:latest
```

## 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 测试知识库 API

```bash
python main.py --check
```

该命令会检查：

- `GET /documents`
- `GET /documents/status_counts`

## 运行 Workflow

### 交互式专利发现流程

默认运行：

```bash
python main.py
```

流程会自动：

- 读取 LightRAG 已处理文档；
- 计算素材充分性分数；
- 不管素材是否充分，都执行外部搜索；
- 生成多个候选专利方向；
- 根据外部搜索结果，为每个候选方向生成相似专利差异分析；
- 让用户选择一个方向；
- 按标题页、发明名称、技术领域、背景技术、发明内容、附图说明、具体实施方式、附图逐步写作；
- 每一步都可选择接受、重写、手动编辑或结束。

输出文件：

```text
outputs/interactive_patent_draft.md
outputs/result.md
outputs/similar_patent_analysis.xlsx
outputs/similar_patent_analysis.md
```

`similar_patent_analysis.xlsx` 会参考样例表格结构，为每个候选专利方向创建一个 sheet，列包括：公开号、申请号、申请日、发明名称、申请人、摘要、差异点。当前表格基于外部网页检索摘要自动整理，只能用于初筛，正式提交前仍需人工核对专利全文、权利要求和法律状态。

### 直接生成模式

如果你仍然想跳过候选选择，直接按输入任务生成：

生成技术交底书：

```bash
python main.py --direct "为基于 LightRAG 知识库 API 的专利写作 agent workflow 撰写技术交底书"
```

如果 LightRAG 没有检索到上下文，程序会自动尝试外部搜索。也可以手动强制启用：

```bash
python main.py --direct --enable-external-search "为基于 LightRAG 知识库 API 的专利写作 agent workflow 撰写技术交底书"
```

生成选题分析：

```bash
python main.py --direct "分析基于知识库 API 的专利写作 agent 的创新点和可专利性"
```

生成附图全集：

```bash
python main.py --direct "为这个专利写作 agent workflow 生成附图 Mermaid"
```

同时调试 `/query/data`：

```bash
python main.py --direct --with-query-data "为基于 LightRAG 知识库 API 的专利写作 agent workflow 撰写技术交底书"
```

输出文件会写入 `outputs/`，并同时更新兼容文件：

```text
outputs/result.md
```

根据任务类型，还会生成：

- `outputs/technical_disclosure.md`
- `outputs/topic_analysis.md`
- `outputs/patent_figures.md`

## 关于 `/query` 请求体

当前默认请求体：

```json
{
  "query": "用户问题",
  "mode": "mix",
  "only_need_context": false,
  "only_need_prompt": false,
  "response_type": "Multiple Paragraphs",
  "include_references": true,
  "include_chunk_content": true
}
```

如果服务端返回 400 或 422，代码会自动回退为：

```json
{
  "query": "用户问题"
}
```

如果你的 Swagger/OpenAPI 文档要求其他字段，请调整 `LightRAGClient._build_query_payload()`。

## 如果输出仍然 no-context

`[no-context]` 表示 API 通了，但知识库没有检索到可用上下文。请检查：

- 文档是否已经上传到 LightRAG 知识库；
- 文档处理状态是否完成；
- 问题是否和知识库内容匹配；
- `/query` 的 `mode`、`top_k` 等参数是否符合你的 LightRAG Server 版本；
- `/query/data` 是否能返回原始检索片段。
